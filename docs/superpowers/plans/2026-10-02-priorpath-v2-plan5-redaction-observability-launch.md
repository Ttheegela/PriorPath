# PriorPath v2 — Plan 5: Redaction, observability, faithfulness, launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the spec's Definition of Done: redact identifiers from PDF page images before the model sees them, trace every LLM call in Langfuse without sending PHI or images, measure explanation faithfulness with an LLM judge, harden `/api/health`, polish the UI, write the remaining docs and demo GIF, publish the portfolio entry, and move production to `main`.

**Architecture:** Redaction uses the PDF's own text layer: pypdfium2 gives every character and its box, Microsoft Presidio (spaCy small model) finds identifier spans in the page text, and the matching boxes are painted black on the rendered page image before it is encoded and sent. Image-only (scanned) PDFs have no text layer, so they cannot be redacted and keep the synthetic-bill confirmation as the only safeguard. Langfuse tracing is a thin wrapper (`app/observability.py`) around the existing explanation and extraction clients that records model, timing, token usage, prompt version and outcome, never page images or patient fields, and is a no-op without keys. The faithfulness eval asks a judge model whether each explanation is fully supported by its flag's evidence, with record/replay like the extraction eval.

**Tech Stack:** Existing stack plus `presidio-analyzer`, `presidio-anonymizer` (only if needed), `spacy` + `en_core_web_sm` (pinned wheel URL), `langfuse` (Python SDK, OTel-based). Frontend unchanged stack. ffmpeg (local only) for the demo GIF.

**Spec:** `docs/superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md` (§6 "All calls are traced in Langfuse (inputs redacted)", §8 faithfulness ≥ 0.9 tracked, §10 redaction before hosted LLMs, §12 Definition of Done, §15 item 4 bundle size). Progress so far: `docs/PROGRESS.md`; security posture: `docs/SECURITY.md`.

## Global Constraints

- Branch `v2-bill-audit` until Task 9 merges to `main`. Every commit ends with both lines:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01HupvBVkLftiJKKAEZ3knbL`
- Never claim HIPAA compliance. Redaction is best-effort on text-layer PDFs; the synthetic-bill confirmation stays required for every PDF upload.
- Never send to Langfuse: page images, PDF bytes, patient names/pseudonyms, provider or payer names, letter bodies. Allowed: model id, prompt version, latency, token usage, finish reason, success/failure, rule id, counts, workspace id hash.
- Redaction must never mask procedure codes, modifiers, units, charges, dates of service or place of service (the extraction eval gate must still pass after re-recording).
- Presidio/spaCy load lazily on the PDF path only (cold starts for the rest of the app must not pay for spaCy). Vercel Python bundle must stay < 500 MB (verify on a preview deploy).
- UI monochrome (black/white/neutral grays). oxlint warning-free; no TS parameter properties.
- Tests never call OpenRouter or Langfuse; use fakes. Backend chain: `ruff check . && ruff format --check . && mypy app evals scripts && pytest -q && alembic check && python -m evals.run --n 300 --seed 7 && git diff --exit-code evals/results` (plus the extraction replay step from CI). Frontend chain: `cd web && npm run lint && npm test && npm run build`. E2E: `cd web && npm run e2e`.
- Outward actions (push, deploy, merging to `main`, pushing the portfolio repo, changing Vercel settings) only in Task 9 with the user's explicit OK.

## Review Focus

1. **Redaction false positives on billing fields** — a 5-digit CPT code, a charge like "1,234.56" or a date "11/03/2026" detected as a phone/ID/date entity and masked, silently dropping lines. Expect: billing columns never masked. Pinned in Task 3.
2. **Text layer that doesn't match the pixels** (rotated page, OCR'd scan with an offset text layer, ligatures, multi-byte characters) — boxes must map to the right place or the page counts as "not redactable" and is reported as such, never a crash. Pinned in Task 3.
3. **Langfuse down or slow** — tracing must never fail or slow an upload/explanation; errors are swallowed and logged once. Pinned in Task 2.
4. **Health check under a cold or failing database** — `/api/health` returns 503 with a short reason, not a 500 stack, and stays cheap. Pinned in Task 1.
5. **Judge model returns junk** (non-JSON, missing verdict, refusal) — counted as "unjudged", reported, never as faithful. Pinned in Task 4.

---

## File Structure

```
app/main.py                  MOD  /api/health checks DB + reference
app/observability.py         NEW  Langfuse wrapper (no-op without keys), trace_llm() context manager, flush hook
app/llm/client.py            MOD  explanation calls traced
app/llm/vision.py            MOD  extraction calls traced
app/ingest/redact.py         NEW  text-layer PHI detection + box masking on page images
app/ingest/pdf.py            MOD  page images for the model go through redaction
app/services/pdf_cases.py    MOD  redaction stats in audit event + upload response
evals/faithfulness.py        NEW  judge prompt, record/replay, report
evals/recorded/faithfulness.json, evals/results/faithfulness.md   (Task 9 data)
web/src/...                  MOD  error boundary, export errors, expired-workspace hint, copy updates
web/scripts/record-demo.mjs  NEW  Playwright video → docs/demo.gif (ffmpeg)
docs/CUSTOMER_BRIEF.md, docs/ARCHITECTURE.md, docs/RUNBOOK.md, docs/LEARNING.md  NEW
README.md, docs/PROGRESS.md, docs/SECURITY.md, docs/EVALS.md  MOD
../../portfolio-website/content/projects/priorpath.md  MOD (separate repo)
requirements.txt, pyproject.toml, .github/workflows/ci.yml  MOD
```

---

### Task 1: `/api/health` checks the database and reference data

**Files:** Modify `app/main.py`; Test `tests/test_main.py`

**Interfaces:** Produces `GET /api/health` → 200 `{"status": "ok", "db": "ok", "reference": ["NCCI-2026Q3", ...]}`; 503 `{"status": "degraded", "db": "<short reason>"}` when `SELECT 1` fails (2 s statement timeout). No workspace cookie, no writes.

- [ ] **Step 1: Failing tests** — healthy DB → 200 with `db == "ok"` and the loaded `ref_version`s; monkeypatch the engine/session to raise `OperationalError` → 503, body has no stack trace or connection string (assert `"postgresql" not in r.text`).
- [ ] **Step 2: Implement** with a short-lived session from `get_engine()` running `SELECT 1` under `SET LOCAL statement_timeout = '2s'`; catch `SQLAlchemyError` → 503 `{"status": "degraded", "db": "unavailable"}` and log the exception. Reference list from `get_reference().versions`.
- [ ] **Step 3: Backend chain.** Commit `feat: /api/health checks the database and loaded reference versions`.

---

### Task 2: Langfuse tracing for every LLM call (no PHI, no images)

**Files:** Create `app/observability.py`, `tests/test_observability.py`; Modify `app/llm/client.py`, `app/llm/vision.py`, `app/main.py` (flush after each request), `requirements.txt` + `pyproject.toml` (`langfuse>=3.10,<5`), `tests/fakes.py`

**Interfaces:**
- Produces: `trace_llm(name: str, *, model: str, kind: Literal["explain", "extract", "judge"], metadata: dict[str, str | int | float | bool]) -> ContextManager[Span]` where `Span.end(output_summary: dict[str, str | int | float | bool], usage: dict[str, int] | None)`. `observability_enabled() -> bool` (true only when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set). `flush() -> None`.
- Consumes: env `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (already set in Vercel for Production/Preview; read the installed SDK to see whether it expects `LANGFUSE_HOST` or `LANGFUSE_BASE_URL`, and map the former to the latter if needed).

- [ ] **Step 1: Read the installed SDK** (`pip show langfuse`, its README/`__init__`) and pick its current manual-instrumentation API (v3+/v4 is OpenTelemetry-based: a `Langfuse()` client with a context-manager for generations/observations and `update(...)`). Do NOT use the `langfuse.openai` drop-in wrapper: it would upload message content, including base64 page images.
- [ ] **Step 2: Failing tests** with a fake Langfuse client injected through a module-level factory:
  1. without keys, `trace_llm` is a no-op and the SDK is never imported/constructed;
  2. with keys, one generation per call with `model`, `kind`, latency and usage; metadata keys limited to an allow-list (`rule_id`, `prompt_version`, `pages`, `page_no`, `finish_reason`, `ok`, `error_type`, `workspace` (salted hash)) — a test passes `provider="Clinic"` and an image byte string and asserts neither reaches the fake;
  3. SDK raising on construction, start, update or flush → swallowed, logged once per process, the wrapped LLM call still returns normally;
  4. explanation and extraction clients emit exactly one generation each per call, including failures (`ok: False`, `error_type`).
- [ ] **Step 3: Implement.** Instrument `OpenRouterClient.complete` (kind `explain`) and `OpenRouterVisionClient.extract` (kind `extract`): record `response.usage` (prompt/completion tokens) when present. Add `PROMPT_VERSION` constants (`explain-v1`, `extract-v2`) next to the prompts. Flush: FastAPI middleware calling `flush()` after the response for routes that made LLM calls (or a `BackgroundTask`), so short-lived serverless invocations deliver traces.
- [ ] **Step 4: Backend chain** (mypy strict; add a mypy override only if langfuse lacks types). Commit `feat: Langfuse tracing for explanation and extraction calls without PHI or images`.

---

### Task 3: Redact identifiers on text-layer PDF pages before the model sees them

**Files:** Create `app/ingest/redact.py`, `tests/test_redact.py`; Modify `app/ingest/pdf.py` (model path only), `app/services/pdf_cases.py`, `app/api/schemas.py` (`UploadResult.redaction`), `requirements.txt` + `pyproject.toml` (`presidio-analyzer>=2.2,<3`, `spacy>=3.8,<4`, `en_core_web_sm` via the pinned GitHub release wheel URL `https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl` — confirm the version matching the installed spaCy), `evals/pdf_render.py` (render patient address, phone and member ID in the header so redaction has something to find)

**Interfaces:**
- Produces:
  - `REDACT_ENTITIES = ("PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN", "LOCATION", "MEMBER_ID")` (`MEMBER_ID` = custom regex recognizer for labelled identifiers: `(?i)(member|subscriber|policy|mrn|patient)\s*(id|#|no\.?|number)\s*[:#]?\s*[A-Z0-9-]{5,20}` capturing the value only). `DATE_TIME` is NOT redacted (dates of service are billing data).
  - `@dataclass PageRedaction(redactable: bool, boxes: list[tuple[float, float, float, float]], entities: dict[str, int])`
  - `find_phi_boxes(page: pdfium.PdfPage) -> PageRedaction` — text via the page's textpage; Presidio over the full page text; map entity char spans → union of char boxes (PDF points); `redactable=False` when the page has no text layer (fewer than 20 non-space chars) or any box falls outside the page.
  - `mask(image: PIL.Image.Image, boxes, page_size_pt: tuple[float, float], scale: float) -> PIL.Image.Image` — paints black rectangles (2 px padding) converting PDF points (origin bottom-left) to pixels (origin top-left).
  - `pdf_page_images(data, *, redact: bool = False) -> list[bytes]` and a sibling `pdf_page_images_with_redaction(data) -> tuple[list[bytes], list[PageRedaction]]` used by `create_pdf_case`; the reviewer's `/pages/{n}` endpoint keeps showing the original (it's the uploader's own document).
  - `UploadResult.redaction: {"pages_redacted": int, "pages_not_redactable": int, "entities": {type: count}} | null` (null for FHIR); same summary in the `case_uploaded` audit event detail.
- Lazy load: the Presidio `AnalyzerEngine` is built once on first use (module-level cache, under the existing pdfium lock only for pdfium calls — Presidio itself doesn't need it).

- [ ] **Step 1: Failing tests** (`tests/test_redact.py`), using `render_bill` with the new header fields:
  1. the patient name, phone, address and member ID boxes are found; the rendered page image has black pixels at those boxes after `mask`;
  2. **no billing field is masked**: for every line, the boxes of its code, modifiers, units, charge, date and POS text do not intersect any redaction box (all three layouts);
  3. an image-only PDF (`add_scan_noise` output) → `redactable=False`, no boxes, no exception;
  4. a page with a rotated text layer or a box outside the page → `redactable=False`;
  5. `create_pdf_case` with a FakeVision that records the PNG/JPEG it receives: for a text-layer bill the image sent has the patient-name region black; the upload response reports `pages_redacted: 1` and entity counts; for a scanned bill `pages_not_redactable: 1`;
  6. Presidio is not imported when only FHIR routes are used (import-guard test: `sys.modules` check after a FHIR upload in a subprocess or fresh import).
- [ ] **Step 2: Implement** `app/ingest/redact.py` and wire it into `create_pdf_case` (model images only). Keep the existing deadline, budget precheck and lock behaviour. Do NOT rebuild the committed demo bills (`data/demo/bills/*.pdf`): their recorded extractions are keyed to the current bytes; the renderer's new header fields apply to newly rendered bills (eval dataset, test bills).
- [ ] **Step 3: Size check.** `pip install` into a clean venv and report the installed size of the new dependencies (`du -sh`); the release step verifies the Vercel bundle on a preview deploy.
- [ ] **Step 4: Backend chain + E2E.** Update `docs/SECURITY.md` (what is redacted, what isn't, scans not redactable), the UI checkbox help text ("names, phone numbers, addresses and member IDs are blacked out before the AI model sees text-based PDFs; scanned images can't be redacted"), and README "AI safeguards". Commit `feat: redact identifiers on text-layer PDF pages before vision extraction`.

---

### Task 4: Faithfulness eval for explanations (LLM judge, record/replay)

**Files:** Create `evals/faithfulness.py`, `tests/test_faithfulness.py`; Modify `.github/workflows/ci.yml` (replay step, skip cleanly when no recording), `docs/EVALS.md`

**Interfaces:**
- Produces: `python -m evals.faithfulness (--record JUDGE_MODEL | --replay PATH)`. Dataset: every flag explanation in `data/demo/explanations.json` paired with its flag (rebuild flags by auditing the demo claims — FHIR `data/demo/cases.json` and `data/demo/bill_claims.json` — and matching `explanation_cache_key`). Judge input per item: rule id, severity, message, evidence row, estimated overcharge, and the explanation text. Judge output (strict JSON schema): `{"verdict": "faithful" | "unfaithful", "unsupported_claims": [string], "reason": string}`. Metric: faithful / judged; "unjudged" (junk output) reported separately and counted as not faithful in the rate. Gate when replaying the committed recording: faithfulness ≥ 0.90 (spec §8, tracked). Writes `evals/results/faithfulness.md` with per-rule rates and every unfaithful item listed.
- Judge client: reuse `OpenRouterVisionClient`-style strict-schema chat call (text only) with `max_tokens=600`, `temperature=0`; default judge model `google/gemini-2.5-flash` (different from the explanation model `deepseek/deepseek-v4-flash`); traced with kind `judge`.

- [ ] **Step 1: Failing tests** with fake judges: all faithful → 1.0, gate passes; one unfaithful + one junk → rate 0.8, gate fails naming "faithfulness", junk counted as unjudged; replay of a missing key errors; record writes raw judge JSON atomically and resumes (skip present keys), keyed by `explanation_cache_key`.
- [ ] **Step 2: Implement.** Keep metric code pure and small; CLI wires record/replay.
- [ ] **Step 3: CI** — backend job step replaying `evals/recorded/faithfulness.json` when present, then `git diff --exit-code evals/results`.
- [ ] **Step 4: Backend chain.** Commit `feat(evals): LLM-judge faithfulness eval for explanations with record/replay`.

---

### Task 5: UI polish batch

**Files:** Modify `web/src/App.tsx`, `web/src/components/{LetterPanel,CaseDetail,AuditLog}.tsx`, `web/src/lib/api.ts`, tests; Create `web/src/components/ErrorBoundary.tsx` (+ test)

**Requirements** (deferred items from Plans 3–4 that a visitor can hit):
1. **Error boundary** around the routed screen: a render error shows "Something went wrong on this screen." with "Back to cases" and "Reload" buttons, never a blank page. Test by rendering a child that throws.
2. **Export errors visible:** replace the plain `<a download>` export links with buttons that `fetch` the export, show a 409/404 message in `role="alert"`, and on success trigger a download via an object URL (`URL.createObjectURL`), using the server filename from `Content-Disposition`. Tests for success (download link clicked with blob) and 409.
3. **Expired workspace hint:** when a case or flag call returns 404 after the app has been open for more than 24 h (store the workspace `created_at` from `/api/workspace`), show "Your demo workspace expired after 24 hours and a fresh one was created. Your earlier cases are gone." with a link to the case list. Test with a fake clock.
4. **Title link hover cue** (underline on hover) and `aria-invalid` only when a field is actually invalid in LineReview.
- [ ] Steps: failing tests → implement → frontend chain + E2E → commit `feat(web): error boundary, visible export errors, expired-workspace hint`.

---

### Task 6: 60-second demo GIF and README refresh

**Files:** Create `web/scripts/record-demo.mjs`, `docs/demo.gif`; Modify `README.md`, `web/package.json` (`"demo:record"` script)

**Requirements:**
- `record-demo.mjs` (Playwright, Chromium, 1280×800, `recordVideo`) runs against `BASE_URL` (default `http://127.0.0.1:8000`): opens the queue → sorts by overcharge → opens a case → accepts a billing error → shows an explanation → drafts and approves the letter → downloads it → returns to the queue → uploads `data/demo/bills/B0001.pdf` with the checkbox → opens the new case. Short pauses so a viewer can follow; total ≤ 60 s.
- Convert with ffmpeg to `docs/demo.gif` (`fps=10`, width 960, palette-optimized two-pass); must be ≤ 8 MB. Document the exact command in the script header.
- Run it locally against the local stack (Docker DB, `PRIORPATH_DEMO=1`; the PDF upload step needs no key only if it uses a FakeVision — instead, skip the live upload when `OPENROUTER_API_KEY` is unset and show the demo PDF case instead). The release step re-records against production for the final GIF.
- README: embed the GIF under the title; add Langfuse tracing and redaction to "AI safeguards"; add the faithfulness eval to "Evaluation" (numbers filled in Task 9); update the license line (MIT, done) and the CI badge to `main` (done in Task 9 after merge).
- Commit `docs: 60-second demo GIF and README updates`.

---

### Task 7: Customer brief, architecture, runbook, learning notes

**Files:** Create `docs/CUSTOMER_BRIEF.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`, `docs/LEARNING.md`; Modify `docs/PROGRESS.md` (Plan 5 section), `README.md` (links)

Content requirements (accurate to the code; no invented numbers):
- **CUSTOMER_BRIEF.md** (1–2 pages, for a non-technical buyer): the user (claims auditor / patient advocate / self-funded employer benefits team), the problem with a concrete example bill, what PriorPath does and doesn't do, time saved per bill (state it as a hypothesis with how to measure it, not a fact), risks and limits (synthetic data, not HIPAA compliant, not billing advice), what a pilot would need (BAAs, auth, real payer contract rates, X12 835/837 input).
- **ARCHITECTURE.md**: component diagram (ASCII), request flows (FHIR upload, PDF upload incl. redaction + extraction + line review, explanation stream, letter), data model (tables), LLM components with guardrails, budgets and limits, deployment topology (Vercel function + CDN frontend + Neon + OpenRouter + Langfuse), key decisions with alternatives considered (rules-decide/AI-explains; OpenRouter; FastAPI `app.frontend()`; query-string routing; record/replay evals; split budgets; per-page synchronous extraction with deadline).
- **RUNBOOK.md**: how to deploy (CLI + Git), run migrations safely (migrate-then-deploy-immediately when a PK/constraint changes), rotate each secret, check health (`/api/health`, UptimeRobot, Langfuse), common incidents with steps (OpenRouter 401/429/credit exhausted, storage breaker 503, demo seeding failure, extraction 504, Neon down), how to re-record evals and rebuild demo data, cost controls.
- **LEARNING.md**: what was learned building it — honest notes on decisions that changed (R4 status D→I, reasoning model failing the 300-token cap, root `public/` not served on Vercel, pdfium thread safety, empty-modifier confidence, POS for facility pricing, negative plants), what the reviews caught, what you'd do differently.
- Commit `docs: customer brief, architecture, runbook and learning notes`.

---

### Task 8: Portfolio site entry (separate repo)

**Files:** Modify `~/Desktop/portfolio/portfolio-website/content/projects/priorpath.md` only (that repo has an unrelated uncommitted change to `vaartha.md` — do not stage, modify or revert it).

- [ ] Rewrite the entry in the existing front-matter format (title, date `"2026-10"`, summary, proof, stack, links incl. `demo: https://priorpath.vercel.app` and `repo`, team, role, featured, order) for v2: one-sentence summary (AI medical bill auditor), proof line with real numbers (rule eval precision/recall 1.000 with negative plants; PDF extraction line F1 0.980; faithfulness rate from Task 9 — leave that clause out until Task 9 fills it), stack (Python, FastAPI, Postgres/Neon, LangGraph, OpenRouter, Presidio, Langfuse, React, TypeScript, Tailwind, Vercel, Playwright), 3–5 bullets in the existing style.
- [ ] Build/lint the portfolio site the way its repo does (read its package.json) to make sure the entry renders; commit only `priorpath.md` in that repo with the standard trailer. Do not push (Task 9).

---

### Task 9 (controller + user): record, verify, merge to main, ship

Not for an implementer subagent.

- [ ] **Step 1 (user terminal, OpenRouter key):**
  - re-record the extraction eval with redaction on: `PYTHONPATH=. python -m evals.extract_eval --n 30 --seed 11 --record google/gemini-2.5-flash-lite` into a fresh candidates file (move the old one aside first so it doesn't resume), compare F1 before/after redaction, promote if the gate passes;
  - record faithfulness: `PYTHONPATH=. python -m evals.faithfulness --record google/gemini-2.5-flash`.
- [ ] **Step 2 (controller):** commit recordings/results, fill README + portfolio numbers, preview deploy to check bundle size and cold start (`/api/health` time) with Presidio, check a Langfuse trace appears for one preview explanation and one PDF upload (no images/PHI in the trace).
- [ ] **Step 3 (user OK):** push `v2-bill-audit`; CI green; production deploy; production verification (health, E2E, smoke, one live text-layer PDF upload showing `pages_redacted: 1`).
- [ ] **Step 4 (user OK):** fast-forward `main` to `v2-bill-audit` (`git checkout main && git merge --ff-only v2-bill-audit && git push origin main`), update the CI badge to `main`, and set Vercel's production branch to `main` if the project is Git-connected (otherwise keep CLI deploys from `main`); push the portfolio repo's `priorpath.md` commit.
- [ ] **Step 5:** final summary with every ruling.
