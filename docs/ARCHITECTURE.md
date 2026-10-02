# Architecture

How PriorPath is put together, how a request moves through it, and why it is built this way. Product context:
[`CUSTOMER_BRIEF.md`](CUSTOMER_BRIEF.md). Operations: [`RUNBOOK.md`](RUNBOOK.md). Security and data handling:
[`SECURITY.md`](SECURITY.md). The original design is in
[`superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md`](superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md);
where the code differs from it, this page describes the code.

## Components

```
                         Browser: React 19 + Vite + TypeScript SPA (web/)
       case queue · line review (PDF) · case detail · letter review · audit log   (?case=<id>, ?view=log)
                                       |
                                       | HTTPS: JSON; SSE for explanation streams
                                       v
 Vercel ----------------------------------------------------------------------------------------------
  CDN: built UI files (web/ -> public/, served by Vercel, not by the function)
  Python function app/main.py (FastAPI, Fluid Compute, 300 s limit)       Cron 05:00 UTC -> /api/internal/cleanup
   |
   |  api/        routers: cases, flags, letters, demo, audit_log, samples, workspace; deps.py = cookie workspace
   |  ingest/     fhir.py   FHIR ExplanationOfBenefit parser (never raises; errors carry a JSON path)
   |              pdf.py    pypdfium2: page limits, 150 DPI render, JPEG; one process-wide pdfium lock
   |              redact.py text layer -> Presidio + labelled regexes -> black boxes on the page image
   |  llm/        vision.py  + extract.py   page image -> JSON schema -> LineItem rows + confidence
   |              explain.py (LangGraph)    draft -> number check (grounding.py) -> one retry
   |              client.py, cache.py (precomputed demo explanations), rule_text.py
   |  rules/      R0-R5 pure functions + totals.py (per-line overcharge cap)
   |  reference/  loads data/reference/subset (NCCI, MUE, PFS; 2026 Q3 + Q4) into memory, by release
   |  services/   audit_run, cases, pdf_cases, explanations, letters, llm_budget, capacity, demo, audit_log
   |  observability.py  Langfuse spans (allow-listed metadata only), bounded flush after each response
   |
   +--> Neon Postgres (workspaces, cases, case_documents, flags, letters, audit_events, llm_usage)
   +--> OpenRouter (openai SDK) --> EXPLAIN_MODEL (text) and EXTRACT_MODEL (vision)
   +--> Langfuse (optional; only when both LANGFUSE keys are set)
 -----------------------------------------------------------------------------------------------------
  UptimeRobot --> GET /api/health
```

Everything that decides a finding (`rules/`, `reference/`) is deterministic and makes no network calls. Model
calls happen in exactly two places in the running app: `llm/vision.py` (reading a PDF page) and `llm/client.py`
(writing an explanation). The faithfulness judge (`evals/faithfulness.py`) runs only from a developer's
terminal when recording.

## Request flows

### FHIR upload: `POST /api/cases`

1. `read_upload` refuses bodies over 4,000,000 bytes (413), by `Content-Length` and by actual size.
2. `current_workspace` reads the signed `pp_ws` cookie. No valid cookie: check the storage breaker (503 if the
   database is over 400 MB), create a workspace, seed the demo cases inside a savepoint (a seeding failure is
   logged and the empty workspace still works), set the cookie.
3. Not JSON → 422 with a friendly message. `parse_fhir` turns each `ExplanationOfBenefit` into a `Claim`; bad
   items become errors with their JSON path and the rest still load. Patient references become an HMAC-SHA256
   pseudonym; names and addresses are not stored. No valid claims → 422.
4. `create_cases` stores each claim as JSON on a `cases` row and writes a `case_uploaded` audit event.
5. `run_audit` runs R0 to R5 against the reference release for each line's date of service, stores `flags`
   rows with evidence and release, and sets the case to `needs_review`.
6. 201 with audited case summaries and any parse errors.

### PDF upload: `POST /api/cases` with a PDF body

1. Same size limit and workspace step. Then: `confirm_synthetic=true` is required (422 otherwise); no
   `OPENROUTER_API_KEY` → 503; storage breaker → 503.
2. **Render and redact** (`pdf_page_images_with_redaction`). spaCy/Presidio load on first use, before the pdfium
   lock is taken. Under the lock: open (encrypted, unreadable, empty or over 10 pages → 422), and for each page
   check the 20-megapixel cap, render at 150 DPI, read the text layer, find identifiers and paint them black,
   encode as JPEG (quality 85). A page with under 20 text characters (a scan), a rotated page or a text layer that
   doesn't line up with the characters is sent unmasked and counted as not redactable. What is and isn't
   targeted is in [`SECURITY.md`](SECURITY.md#pdf-redaction).
3. **Budget pre-check.** If the workspace's remaining hourly page budget is smaller than the page count → 429,
   before any model call and with nothing stored.
4. **Per-page extraction, synchronous, with a deadline.** For each page: stop with 504 if 240 seconds have
   passed since the upload started; consume one budget unit (429 if refused); commit to release the
   `llm_usage` row lock; call the vision model (25-second timeout, `max_tokens` 4000, temperature 0, strict JSON
   schema). A model failure, an incomplete or non-JSON response → `VisionError` → 502, nothing stored.
5. `parse_page` validates every row through `LineItem`. Invalid rows are dropped with a per-line error (capped at
   160 characters); an unreadable place of service keeps the line but clears the POS. Each field has a confidence;
   an empty modifier list counts as confidence 1.
6. `store_pdf_case` stores the claim, the PDF bytes (`case_documents`) and the redaction counts in the
   `case_uploaded` audit event. If any line is below 0.9 confidence, or no lines were read, the case goes to
   `needs_line_review`; otherwise it is audited at once.
7. 201 with the case summary, row errors and `redaction` (`pages_redacted`, `pages_not_redactable`,
   `pages_partially_redacted`, entity counts).

### Line review

- `GET /api/cases/{id}/pages/{n}` renders one page of the stored PDF as a JPEG (the original, unredacted: it is
  the uploader's own document), cached privately for an hour.
- `PATCH /api/cases/{id}/lines` replaces the lines (each re-validated; confidence set to 1), deletes the flags
  and any draft letter, sets the case back to `uploaded`, and logs `lines_edited`. 409 if a letter is approved.
- `POST /api/cases/{id}/audit` then runs the rules. It returns 409 while the case is still in
  `needs_line_review`.

### Explanation stream: `POST /api/cases/{id}/explain`

1. The request collects the ids of flags whose explanation is `pending` or `unavailable`, then closes its
   database session. The stream opens its own.
2. For each flag, while under the 240-second stream deadline: use the precomputed demo explanation if the flag
   matches one (`llm/cache.py`, no model call); otherwise need a configured client and one unit of the
   workspace's explanation budget (commit to release the row lock), then run the LangGraph loop:
   - **draft**: the system prompt plus one flag only (rule text, severity, message, evidence row, release,
     estimated overcharge), `max_tokens` 300, temperature 0;
   - **check**: every money amount and percentage must match one in the sources by type; URLs are rejected;
   - **retry** once with the offending numbers named; after two attempts the flag is `unavailable`.
3. Each flag is committed and sent as an SSE `explanation` event (`ready` or `unavailable` with a reason:
   not configured, over budget, model unavailable, or not grounded). An unexpected error on one flag sends an
   `error` event and the stream continues. A final `done` event reports counts, including flags left for the
   next request when the deadline hit, and the `explanations_generated` audit event is written.
4. After the last byte is sent, the `FlushTraces` middleware delivers pending Langfuse spans (bounded, see below).

### Review, letter, export

1. `PATCH /api/flags/{id}`: accept, reject (reason required) or reopen. Notices (R0) can't be decided.
2. `POST /api/cases/{id}/letter`: `build_letter` (a template, not a model) lists accepted errors with their
   message, estimated overcharge and explanation, and accepted outliers only as a request for itemized
   justification. Leads are never included. The text is also stored as `generated_body`.
3. `PATCH /api/letters/{id}`: the edit may not add an amount, percentage or URL that isn't in `generated_body`.
4. `POST /api/letters/{id}/approve`: 409 unless the accepted findings are the ones the letter was drafted from.
5. `GET /api/letters/{id}/export?format=txt|docx`: approved letters only; the file is built before the case is
   marked `exported`. The UI downloads it with `fetch` and a blob URL.

Case statuses: `uploaded` → (`needs_line_review`, PDF only) → `needs_review` → `letter_ready` → `approved` →
`exported`. Every step writes an `audit_events` row.

## Data model

All tables hang off `workspaces` with `ON DELETE CASCADE`, so deleting a workspace deletes everything it owns.

| Table | Key columns | Notes |
|---|---|---|
| `workspaces` | `id` (UUID), `created_at` | One per browser cookie. The daily cleanup deletes rows older than 24 hours. |
| `cases` | `id`, `workspace_id`, `status`, `source` (`fhir`/`pdf`), `payer_type`, `claim` (JSONB) | The whole `Claim` (lines, pseudonym, provider, payer) as JSON. |
| `case_documents` | `case_id` (PK), `content` (bytes, deferred load), `page_count` | PDF cases only. |
| `flags` | `id`, `case_id`, `position`, `flag_key` (text), `rule_id`, `severity`, `line_ids`, `evidence` (JSONB), `est_overcharge` (numeric 12,2), `message`, `explanation`, `explanation_status`, `status`, `reject_reason` | Rebuilt on every audit. |
| `letters` | `id`, `case_id`, `flag_ids`, `body`, `generated_body`, `status`, `approved_at` | `generated_body` is the number-check baseline. |
| `audit_events` | `id` (bigint), `workspace_id`, `case_id` (nullable), `actor`, `action`, `detail` (JSONB), `ref_versions`, `at` | Workspace and per-case timelines. |
| `llm_usage` | PK (`workspace_id`, `hour_start`, `kind`), `calls` | Hourly AI counters; `kind` is `explain` or `extract`. |

Migrations (`migrations/versions/`): `a2e396cb180c` initial schema → `681b2a69995f` letter generated body →
`0f75e156ec4c` flag key as text → `0f2549585d12` case documents and `llm_usage.kind` (adds `kind` to the primary
key). `alembic check` in CI fails if the models drift from the migrations.

Reference data is not in the database. `data/reference/subset/` (code numbers, edit dates, indicators, unit
limits, computed national rates) is bundled with the function and loaded once per instance.

## LLM components and guardrails

| Component | Model (default) | Input | Output | Guardrails |
|---|---|---|---|---|
| PDF extraction | `EXTRACT_MODEL`, `google/gemini-2.5-flash-lite` (`app/llm/vision.py`, prompt `extract-v2`) | One redacted page image (JPEG) | Lines with code, modifiers, units, charge, DOS, POS, each with a confidence | Strict JSON schema; every row re-validated by `LineItem`; invalid rows dropped, never guessed; low confidence → human line review; page text treated as data; rules, not the model, decide findings |
| Explanation | `EXPLAIN_MODEL`, `deepseek/deepseek-v4-flash` (`app/llm/client.py`, prompt `explain-v1`) | One flag: rule text, severity, message, evidence row, release, estimated overcharge | At most three sentences | Typed number check (money / percent / plain counts up to 10), URL and non-ASCII digit rejection, one retry, else `unavailable`; no patient, provider or payer names sent; 300 output tokens |
| Faithfulness judge (eval only) | `google/gemini-2.5-flash` (`evals/faithfulness.py`, prompt `judge-v2`) | One demo explanation plus its flag and rule text | `unsupported_claims`, `reason`, `verdict` | Strict schema; junk output counts as not faithful; `max_tokens` 2000 with low reasoning effort; recorded once, replayed in CI |

The letter is not an LLM component: it is a template over accepted findings.

**Tracing.** `trace_llm` wraps each model call in a Langfuse generation (`explain`, `extract`, `judge`) and
records only the model id, prompt version, latency, token usage, finish reason, success or error type, and the
rule id or page number where they apply. It is a no-op unless `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are
set. SDK errors are swallowed and logged once. The SDK's own flush can block for 30 seconds, so the flush after a
response runs in a daemon thread and the request waits at most 2 seconds for it.

## Budgets and limits

| Limit | Value | Where |
|---|---|---|
| Upload size | 4,000,000 bytes (413) | `app/api/cases.py` |
| PDF pages / pixels / resolution | 10 pages; 20 megapixels per page, checked before rendering; 150 DPI; JPEG quality 85 | `app/ingest/pdf.py` |
| Explanations per workspace | 20 per hour | `app/services/llm_budget.py` |
| PDF pages per workspace | 20 per hour (separate pool) | `app/services/llm_budget.py` |
| AI calls across all workspaces | 100 per hour, shared by both kinds; each workspace counts at most up to its own cap | `app/services/llm_budget.py` |
| Explanation output | 300 tokens, 2 attempts, 30 s client timeout | `app/llm/client.py`, `app/llm/explain.py` |
| Vision call | 25 s timeout, 4000 tokens, no client retries | `app/llm/vision.py` |
| Explanation stream deadline | 240 s (remaining flags reported in `done`) | `app/api/cases.py` |
| PDF extraction deadline | 240 s, checked before each page (504, nothing stored) | `app/services/pdf_cases.py` |
| Storage breaker | 503 for new workspaces and uploads above 400 MB | `app/services/capacity.py` |
| Health check | `SET LOCAL statement_timeout = '2s'` | `app/main.py` |
| Langfuse | 3 s SDK timeout; 2 s flush budget per request | `app/observability.py` |
| Workspace lifetime | 24 h cookie max age; daily cleanup of workspaces older than 24 h | `app/api/deps.py`, `app/services/demo.py` |
| OpenRouter key | Credit cap set on the key itself (outside the code) | OpenRouter dashboard |

## Deployment topology

- **Vercel, one project.** `vercel.json` builds the UI (`cd web && npm ci && npm run build`, into `public/`),
  declares one function, `app/main.py` (includes `data/**`, excludes `web/**`), and the daily cron.
  `[tool.vercel.fastapi.static] exclude = true` in `pyproject.toml` keeps the built UI out of the function so
  Vercel serves it from its CDN. `.vercelignore` keeps raw CMS files, tests, evals, scripts, docs and migrations out
  of the upload.
- **Python function** on Fluid Compute: FastAPI behind every `/api/*` route; no state between requests except
  per-instance caches (reference data, demo files, the Presidio analyzer, the Langfuse client). The bundle must
  stay under Vercel's 500 MB Python limit; Presidio, spaCy and its small English model are the largest additions,
  and the size is checked on a preview deploy before release.
- **Neon Postgres**, reached with `NullPool` and `prepare_threshold=None` so it works through Neon's pooled
  (PgBouncer) URL. Migrations run from a developer terminal, never during a build.
- **OpenRouter** for both models, through the `openai` SDK with `base_url=https://openrouter.ai/api/v1` and
  `max_retries=0` (retries are decided by our code, not the SDK).
- **Langfuse** (optional) for traces; **UptimeRobot** polls `/api/health`, which returns 503
  (`{"status": "degraded", "db": "unavailable"}`) when the database doesn't answer within its timeout and 200 with
  the loaded reference releases otherwise.
- **GitHub Actions**: backend (ruff, format, mypy strict, pytest on Postgres 17, `alembic check`, rule eval,
  extraction and faithfulness replays, each followed by `git diff --exit-code evals/results`), frontend (oxlint,
  vitest, build) and an E2E job (Playwright against the built UI and a real Postgres).

## Key decisions

| Decision | Alternatives considered | Why |
|---|---|---|
| **Rules decide, AI explains.** Findings come only from pure rule functions over published CMS tables; models read PDFs and explain findings. | An agent that reads the claim and decides flags itself. | Reproducible (same claim, same flags, with the table row and release), testable to precision/recall 1.000 with planted errors, and a model can misread a PDF line (line review exists for that) or word an explanation badly, but never decides a finding. |
| **OpenRouter** for every model call, model ids in environment variables. | Calling one provider's SDK directly. | One key and one client for text and vision; candidate models were compared on the same eval by changing a string (deepseek-v4-flash 11/12 grounded vs v4-pro 0/12; gemini-2.5-flash-lite chosen for extraction). Cost: a router in the data path, which is one more party a BAA would have to cover. |
| **FastAPI serves the UI with `app.frontend()`** and Vercel serves the files from its CDN. | A separate frontend deployment; Vercel rewrites to a root `public/` folder. | A preview showed that a root `public/` created during the build is not served as static files. `app.frontend()` plus `static exclude` gives one project and one origin (no CORS, the cookie just works) with CDN delivery. |
| **Query-string routing** (`/?case=<id>`, `/?view=log`). | A router library with path routes. | No new runtime dependency, and every view is the root path plus a query string, so a deep link or refresh always loads the same `index.html`. |
| **Record/replay evals.** Model output is recorded once from a developer terminal; CI replays it and re-runs our own parsing, validation and rules. | Calling models in CI; mocking the model. | CI stays deterministic, free and secret-free, yet still catches regressions in our code against real model output. A changed results file fails CI. |
| **Split AI budgets**: 20 explanations and 20 PDF pages per workspace per hour, 100 calls per hour globally. | One shared per-workspace pool. | A 10-page PDF would otherwise use half of a visitor's explanations. The global cap bounds spend across all visitors; the OpenRouter credit cap bounds it again outside the code. |
| **Per-page synchronous extraction with a 240 s deadline.** The upload request reads every page before it returns. | A background job queue with polling. | The function limit is 300 s. With at most 10 pages, a 25 s per-page timeout and an all-pages budget pre-check, one request is enough, and a queue would add a second service, job state and a polling UI. The deadline turns a slow model into a clean 504 with nothing stored instead of a killed function. |
| **Template letters**, not model-written letters. | An LLM drafting the letter from accepted flags (the spec's first plan). | The letter is the one artifact sent to a third party. A template over accepted findings can't introduce a number or claim; edits are number-checked against the generated text. |
