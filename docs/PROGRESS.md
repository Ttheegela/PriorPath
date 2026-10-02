# PriorPath v2 — Progress Log (Plans 1 to 4)

_Last updated: 2026-10-02 · Branch: `v2-bill-audit` (pushed, not merged; `main` still holds v1; production was deployed from this branch with the Vercel CLI) · Live: https://priorpath.vercel.app (API docs at `/api/docs`)_

PriorPath v2 rebuilds the old prior-authorization demo as an **AI medical bill auditor** for claims auditors and patient advocates. Deterministic rules over public CMS data decide what is wrong with a claim; an LLM only explains each finding in plain English, and a person approves every dispute letter.

- Design spec: `docs/superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md`
- Plan 1: `docs/superpowers/plans/2026-10-01-priorpath-v2-plan1-core-engine.md`
- Plan 2: `docs/superpowers/plans/2026-10-02-priorpath-v2-plan2-backend-ai.md`
- Plan 3: `docs/superpowers/plans/2026-10-02-priorpath-v2-plan3-reviewer-ui.md`
- Plan 4: `docs/superpowers/plans/2026-10-02-priorpath-v2-plan4-usability-pdf-readme.md`

---

## At a glance

| | Plan 1 (core engine) | Plan 2 (backend, database, AI) | Plan 3 (reviewer UI) | Plan 4 (usability, PDF bills, README) |
|---|---|---|---|---|
| Dates | 2026-10-01 | 2026-10-02 | 2026-10-02 | 2026-10-02 |
| Commits | 14 (c9fe87b → b5c7c8d, plus final-review fixes) | 27 (08dbf27 → c0fafe3) | 13 commits (f394756..HEAD at ship, including the final-review fix commit) | 16 on the branch (45b011b → the README commit) plus 3 from the parallel Plan 5a branch (merged as 3d641b0) |
| Outcome | Rule engine + CMS data + FHIR input + eval gate + first deploy | Usable audit API with Postgres, per-visitor demo, grounded AI explanations, dispute letters | React reviewer UI served by the same FastAPI app, Playwright smoke test in CI | Upload runs the audit, sample files, PDF bills with vision extraction and line review, PDF extraction eval, Q3 2026 data, eval negative plants, SECURITY.md, README |
| Tests at end | 73 | 183 | backend 189, web 43 + 1 E2E | backend 279 + 1 skipped, web 60, 2 E2E specs (the PDF one skips until demo extractions are recorded) |
| Live | `/api/health`, `/api/version` | Full audit flow via `/api/docs` | Full flow in the browser at `/` | Not yet deployed (release step: record extraction eval, demo extractions, Neon migration `0f2549585d12`, deploy) |

All four plans were built with subagent-driven development: a fresh implementer per task (test-first), a separate reviewer per task, scoped re-reviews for every fix round, and an Opus whole-branch review before each deploy.

---

## Plan 1 — Core engine, evals, first deploy

### What was built
| Area | Details |
|---|---|
| Models | `Claim`, `LineItem`, `Flag`, `Evidence`, `Severity` (error / outlier / lead / notice). Money is `Decimal`, rounded to cents. Codes and modifiers normalized. |
| CMS reference data | Real 2026 Q4 releases: NCCI PTP v32.3 (practitioner), MUE practitioner table, PFS RVU26D. Adapters find headers by column title (handles CMS preambles and multi-row headers). A committed code-number-only subset: 2,447 NCCI pairs, 100 MUE limits, 92 fee rates, 5 invalid codes. Raw files (AMA/CMS license) stay git-ignored. |
| Versioning | Every lookup is resolved by date of service against the loaded release; every flag records the release it used (`NCCI-2026Q4`, `MUE-2026Q4`, `PFS-2026D`). Dates outside coverage get an R0 "cannot audit" notice, never a silent pass. |
| Rules | R0 coverage notice · R1 duplicates (repeat modifiers 76/77/91 exempt) · R2 NCCI unbundling (modifier indicators 0/1/9, NCCI-associated modifiers) · R3 MUE unit limits (per line or per day) · R4 invalid codes · R5 price outliers vs Medicare national rate (facility vs non-facility, 26/TC skipped). All pure functions. |
| FHIR input | `ExplanationOfBenefit` bundle parser that never crashes on any JSON: every bad item becomes an error with its JSON path and the rest of the claim still parses. Quantity and charge magnitude caps, duplicate and non-integer sequences rejected. Writer for round-trip tests. |
| Evals | Synthetic claim generator with planted, labeled errors on real codes; eval runner sends claims through FHIR → rules. CI gate: precision and recall 1.000 per rule, zero flags on clean claims, zero parse errors. |
| CI | GitHub Actions: ruff, mypy (strict), pytest, eval, stale-results check. |
| Deploy | Vercel (Python, Fluid Compute) at https://priorpath.vercel.app with `/api/health` and `/api/version`. Needed a `[project]` table for Vercel's uv builder and a `.vercelignore` (bundle was 406 MB with raw CMS files). |

### Eval result (real 2026 Q4 data, 300 claims, seed 7)
| Rule | Support | Precision | Recall |
|---|---|---|---|
| R1 | 62 | 1.000 | 1.000 |
| R2 | 46 | 1.000 | 1.000 |
| R3 | 54 | 1.000 | 1.000 |
| R4 | 45 | 1.000 | 1.000 |
| R5 | 45 | 1.000 | 1.000 |

Clean claims: 109, with flags: 0. FHIR parse errors: 0.

### Key decisions in Plan 1
1. **R4 "invalid code" = PFS status D or I.** The real 2026 file has no status-D codes; status I means "not valid for Medicare".
2. **CMS/AMA license accepted by the user; downloads done after approval.** Only code numbers, dates, indicators and computed rates are committed.
3. **The FHIR parser must never raise** (it is the trust boundary for uploads).
4. Final-review carry-overs written into spec §16 for Plan 2 and later.

---

## Plan 2 — Backend, database and AI explanations

### What was built
| Area | Details |
|---|---|
| Database | Postgres via SQLAlchemy 2.0 + psycopg 3 + Alembic (3 revisions: initial schema → letter generated body → flag key as text). Neon in production, Postgres 17 in Docker for tests, a Postgres service in CI, `alembic check` in CI. |
| Workspaces | Each visitor gets a private workspace from a signed `pp_ws` cookie (HttpOnly, SameSite=Lax, Secure on Vercel). Another visitor's ids return 404 on every route. New workspaces are seeded with 10 synthetic demo claims (already audited, explanations pre-built). |
| Cases API | `POST /api/cases` (FHIR upload; partial success; 413 over 4 MB; 422 with paths), `GET /api/cases`, `GET /api/cases/{id}`. Audit log on every change. |
| Audit and review | `POST /api/cases/{id}/audit` runs the rules (payer-aware: status-I codes are a *lead* for non-Medicare payers). `PATCH /api/flags/{id}` accepts, or rejects with a reason. Case totals never count more than a line was billed. |
| AI explanations | `POST /api/cases/{id}/explain` streams over SSE. LangGraph loop: draft → typed number check → one retry. Any dollar amount or percentage must match one in the evidence; plain counts up to 10 allowed; "$4k", "USD 7", "seven dollars", URLs and non-ASCII digits rejected. Model failures, missing config or budget limits mark a flag "unavailable" with a reason; it can be retried. Only the flag (rule, severity, message, evidence, overcharge) is sent to the model — never patient, provider or payer names. |
| Budgets and limits | 20 explanations per workspace per hour; 100 per hour globally (your OpenRouter key also has a $10 credit cap). Storage breaker returns 503 for new workspaces and uploads when the database passes 400 MB. Input length limits on codes, modifiers and claim ids. 240-second stream deadline. |
| Letters | Template-built from accepted error and outlier flags only. Edits are checked against the drafted text: no new amounts, no URLs, no control characters. Approval only succeeds if the accepted findings are unchanged since drafting. Re-audit and re-drafting are blocked once a letter is approved. Export as TXT or Word. |
| Demo upkeep | `POST /api/demo/reset`; daily Vercel cron `GET /api/internal/cleanup` (Bearer `CRON_SECRET`, constant-time check) deletes workspaces older than 24 h. |
| Privacy | Patient pseudonyms are keyed HMAC-SHA256 (16 hex) instead of a plain hash. |
| Production | Neon migrated to `0f75e156ec4c (head)`. Secrets in Vercel (all Secret type): `DATABASE_URL`, `OPENROUTER_API_KEY`, `SESSION_SECRET`, `CRON_SECRET`, `LANGFUSE_*`. UptimeRobot monitors `/api/health`. |

### Explanation model choice
Compared on the 12 demo flags by grounding-pass rate:

| Model | Grounded | Price (in / out per M tokens) |
|---|---|---|
| `deepseek/deepseek-v4-pro` | 0 / 12 (reasoning tokens likely exhausted the 300-token cap) | $0.21 / $0.42 |
| **`deepseek/deepseek-v4-flash`** (chosen) | **11 / 12** | **$0.04 / $0.08** |

### Production verification (2026-10-02)
- Smoke test `python scripts/smoke.py https://priorpath.vercel.app --require-explanations` passes: demo case → explanations ready → accept flag → letter → approve → export.
- Live check on a freshly uploaded claim: **2 / 2 explanations ready** from the live model.
- The first live check failed with OpenRouter `401 Unauthorized` (bad key value in Vercel). The new failure logging found it immediately; the key was re-added and production redeployed.

### What reviews caught (and fixed) in Plan 2
| Task | Problem found | Fix |
|---|---|---|
| 1 | CI format check would fail (env.py; ruff also formats code blocks in docs) | `docs/` excluded from ruff; test-DB guard refuses non-test databases |
| 4 | Charges stored as "300.0" after FHIR round trip | Rounded to cents in the model itself |
| 6 | Grounding bypasses: "$7.00", "5%", "$99213", "$4k", "7 dollars", "seven dollars" | Typed grounding (money / percent / plain), currency markers, number words, ASCII digits |
| 6 | Client retries allowed up to 4 API calls per explanation | Client retries off; graph retries once |
| 8 | Letter edits checked against current flags, not the drafted text; export could 500 after marking exported | Stored `generated_body` baseline; approve-only-what-was-drafted; build file before saving |
| 9 | Corrupt demo file would 500 every new visitor | Seeding in a savepoint with logging; cached parse |
| Final | Long flag keys crashed audits; unlimited code length into the model; no global AI cap; no storage limit; re-audit deleted approved letters; no stream deadline | All fixed before production (see Plan 2 ledger summary above) |
| Deploy | Demo cache hid a broken production API key | `--require-explanations` smoke flag + live upload check |

### Known limits (logged, not blocking)
- A few rare amount formats still pass the number check ("7 US dollars", "7 euros").
- Plain counts up to 10 are always allowed, so "billed 2 times" could be edited to "billed 9 times" in a letter.
- Bots that call the API create throwaway workspaces; bounded by the storage breaker and daily cleanup.
- Demo service dates are Oct–Dec 2026. Since Plan 4 (merged Plan 5a), 2026 Q3 and Q4 releases are loaded, so bills dated Jul–Dec 2026 are auditable; other dates get a "cannot audit" notice.
- `main` still holds v1; Vercel production is deployed from this branch with the CLI. Merging anything to `main` before v2 is finished would deploy v1.

---

## Plan 3 — Reviewer UI

### What was built
| Screen | Details |
|---|---|
| Case queue | Filter by status, sort by overcharge, "Upload" of FHIR JSON (up to 4 MB). Row links open the case. |
| Case detail | Line table with flag badges; flags grouped as Billing errors, Price outliers and Leads (always labelled separately); evidence, explanation, estimated overcharge; Accept, or Reject with a reason; header totals. Code numbers only, no CPT descriptors. |
| Letter review | Draft from accepted flags only, editable text, approve, download as .txt or .docx. |
| Audit log | Per-case timeline and a global view. |
| Plumbing | `ensureWorkspace()` is memoized so the first workspace-scoped call finishes before any other (no double workspaces). Money is shown with `Intl.NumberFormat` and never computed in the UI. Copy says the data is synthetic and nothing is sent anywhere (amended in Plan 4: PDF page images are sent to an AI model; nothing else leaves the app). |
| E2E | One Playwright smoke test on the demo data: open the top-overcharge case, accept a flag, draft and approve the letter, download it. Runs in CI as a third job; `PLAYWRIGHT_BASE_URL=https://priorpath.vercel.app npm run e2e` runs it against production. |

### Key decisions in Plan 3
1. **FastAPI serves the UI** (`app.frontend("/", directory=public)`, mounted only when `public/` exists). A Vercel preview showed that a root `public/` built during the deploy is not served as static files.
2. **Query-string routing** instead of a router library (no new runtime dependency).
3. **oxlint** from the Vite template as the linter; the frontend chain must be warning-free.
4. **User directive: black-and-white UI.** Black, white and neutral grays only; state is conveyed by words and weight, never color.
5. E2E uses a single server (built UI and API on port 8000), so CI builds the UI first and needs no proxy.

### What reviews caught (and fixed) in Plan 3
| Task | Problem found | Fix |
|---|---|---|
| 2 | An unanchored `public/` git-ignore rule hid the favicon; the favicon was colored | Rule anchored to `/public/`; favicon made monochrome |
| 5 | Stale case shown when the id changed; "not found" state was sticky; a lint warning | Case reset on id change, not-found cleared, warning removed |
| 5 | Cancel on the reject form kept the typed reason | Cancel clears the reason |
| 7 | Audit-log error was sticky after a later success; timeline did not refresh after letter or explanation actions | Error cleared on success; timeline reloads after those actions |
| Final | Redraft discarded unsaved letter edits | Redraft disabled while edited, with a "Save your edits before redrafting." hint |
| Final | A failed queue load also showed "No cases match." | Table renders only after a successful load |
| Final | Client upload limit (4 MiB) differed from the server's 4,000,000 bytes | Client limit set to 4,000,000 bytes |
| Final | Ids went into fetch paths unencoded; modified clicks on in-app links were swallowed | `encodeURIComponent` on every id; ctrl/cmd/shift/middle-click fall through to the browser |

### Verification
- Backend chain (ruff, format, mypy, pytest, alembic check, eval, stale-results check): 189 tests pass.
- Frontend chain (lint warning-free, vitest, build): 43 tests pass.
- Local E2E against Docker Postgres: 1 passed.

---

## Plan 4 — Usability fixes, PDF bills, README

### What was built
| Task | Details |
|---|---|
| 1. Usability | Title links home. `POST /api/cases` now runs the audit for every new case and returns audited summaries. Friendly messages for non-JSON and non-FHIR uploads. `GET /api/samples/claim.json` (2 demo claims) with a download link in the queue. |
| 2. Synthetic PDF bills | `evals/pdf_render.py`: three layouts (table, statement, compact) and seeded scan noise. Rows never split across pages; headers repeat on continuation pages; noisy pages stay around 255 KB so bills fit the 4 MB upload cap. |
| 3. PDF ingest and vision extraction | `app/ingest/pdf.py` (at most 10 pages, encrypted or unreadable PDFs refused, 150 DPI rendering, 20-megapixel page cap checked before rendering). `app/llm/vision.py` (OpenRouter, strict JSON schema, temperature 0). `app/llm/extract.py`: `parse_page` validates every row through `LineItem`, drops invalid rows with a per-line error, keeps per-field confidence. |
| 4. PDF API | PDF branch of `POST /api/cases` (`confirm_synthetic=true` required; 422/429/502/503 mapped), `GET /api/cases/{id}/pages/{n}` (one page rendered per request, cached privately), `PATCH /api/cases/{id}/lines`. New `case_documents` table and `llm_usage.kind`; migration `0f2549585d12`. Row error text from the model is capped at 160 characters. |
| 5. Line review UI | Required "synthetic or test bill" checkbox, `LineReview` screen with page images beside editable lines; fields below 0.9 confidence are marked (same threshold as the backend). |
| 6. Extraction eval | `evals/extract_eval.py`: 30 bills (seed 11), line F1 on (code, units, charge), end-to-end recall per rule from rebuilt claims, per-layout and clean/noisy breakdowns, record/replay so CI never calls a model, resumable atomic recordings. CI replays a recording when one is committed. |
| 7. Demo PDF bills | `build_demo.py --bills [--extract [--explain]]`, 2 committed sample bills, `GET /api/samples/bill.pdf`, demo seeding of PDF cases from recorded extractions (per-bill savepoint), Playwright `pdf.spec.ts`. |
| 8. Docs | README rewritten (architecture, rules, evals, setup, API, deployment, security, full glossary); EVALS.md and SECURITY.md updated for Plan 4. |

**Plan 5a, built in parallel and merged before Task 6** (commits 4eeefba, a31f679, 0836105; merge 3d641b0): eval negative plants (spec §16 item 3), 2026 Q3 NCCI/MUE and RVU26C with overlap validation (spec §16 item 4), `docs/SECURITY.md` and `docs/EVALS.md`.

### Key decisions and rulings in Plan 4
1. **Split AI budget** (user question): explanations 20 per workspace per hour and PDF pages 20 per workspace per hour as separate pools, 100 per hour globally shared by both. One shared pool would let a single PDF starve explanations.
2. **Place of service added to PDF extraction.** R5 picks the facility or non-facility rate from POS, so all three bill layouts print POS and the schema has an optional `place_of_service`. An unreadable POS keeps the line but sends it to line review; "-", "N/A" or blank means not shown.
3. **FastAPI serves the UI with `app.frontend()`**, and `[tool.vercel.fastapi.static] exclude = true` lets Vercel serve the built files from its CDN instead of the function bundle.
4. **Extraction eval noise split** is `(i // 3) % 2 == 1`, so every layout appears both clean and noisy.
5. **PDF page images are unredacted until Plan 5**, so PDF uploads require an explicit synthetic-bill confirmation in the API and the UI.
6. Extraction eval numbers, the extraction model choice and the demo extractions are recorded in the release step (they need an OpenRouter key).

### What reviews caught (and fixed) in Plan 4
| Task | Problem found | Fix |
|---|---|---|
| 2 | Statement layout split a line across pages; noisy PDFs were ~1.4 MB per page (over the upload cap with 3 pages); headers not repeated | Rows kept together, compressed grayscale noise (~255 KB/page), repeated headers, pagination and determinism tests |
| 3 | A corrupt page could raise outside the error handling (500); `parse_page` crashed on malformed model output; unguarded empty model response | All pdfium errors map to a readable 422; malformed pages and rows become errors; empty responses raise `VisionError` |
| 4 | The page endpoint rendered every page per request | `render_page` renders only the requested page, with a private cache header |
| 5 | UI marked fields below 0.8 confidence while the backend sends lines below 0.9 to review (a case could need review with nothing marked) | UI threshold aligned to 0.9; empty and locked line review handled |
| 6 | An invalid POS dropped the whole line | Line kept, POS cleared, line sent to review |
| 6 | Noise coincided with one layout | Noise split across layouts; per-layout F1 reported |
| 7 | `pdf.spec` skip could hide seeding regressions; `--extract` saved only at the end (paid results lost on error); one bad demo bill could roll back all demo cases | Spec fails when recordings exist but no PDF case shows; extraction saved per bill and resumable; per-bill savepoint |
| 7 | Flaky App test (~1 in 3) from a stub that returned the wrong shape for `/api/audit-log` | Stub fixed; 5 consecutive green runs |
| Final | pdfium is not thread-safe (16 concurrent renders crashed the process) | One module-level lock around every pdfium open/render/close |
| Final | A 10-page PDF could outrun the 300 s function limit (60 s model timeout per page) | 240 s extraction deadline checked before each page (504, nothing stored); model timeout 25 s |
| Final | PNG pages could pass Vercel's 4.5 MB response limit and inflate model input | Pages encoded as JPEG (quality 85) for both the page endpoint and the model |
| Final | Line review could not show or edit POS, so an unreadable-POS case had nothing marked | POS column in line review; `LineItem` accepts only a two-digit POS or none (422 names the field) |
| Final | Header said "nothing is sent anywhere", false for PDFs | "PDF page images are sent to an AI model; nothing else leaves the app" |
| Final | Smoke test required exactly 10 cases; CI replayed whichever candidate recording sorted first | Smoke accepts 10 or more; candidates live in `evals/recorded/candidates/`, `--promote` copies the chosen one to `evals/recorded/extraction.json`, the only file CI replays |
| Final | Audit could run on a case still in line review; budget checked only page by page | 409 "review the extracted lines first" (Run audit hidden in line review); all pages' budget checked before the first model call |

### Verification
- Backend chain (ruff, format, mypy, pytest, alembic check, eval, stale-results check): 279 passed, 1 skipped (the committed-demo PDF test skips until demo extractions are recorded).
- Frontend chain (lint warning-free, vitest, build): 60 tests pass.
- E2E: smoke spec passes locally; the PDF spec passes with a temporary extraction recording and skips without one.

### Known limits (logged, not blocking)
- PDF page images go to the hosted vision model unredacted until Plan 5 (Presidio). Uploads require the synthetic-bill confirmation.
- Uploaded PDFs are stored for the life of the workspace and can contain anything printed on the bill, including names.
- The workspace cookie has no embedded timestamp, so a copied cookie works until the daily cleanup deletes the workspace (up to about 48 hours).
- PDF rendering holds one process-wide lock (pdfium is not thread-safe), so concurrent PDF requests on one instance render one at a time.

---

## How to run it

```bash
# local
docker compose up -d db
source .venv/bin/activate
pytest -q                                   # 279 tests against Docker Postgres
python -m evals.run --n 300 --seed 7        # rule-engine eval gate
python -m evals.extract_eval --replay evals/recorded/extraction.json   # PDF extraction eval (after --record and --promote)

# UI: unit tests and browser smoke test (needs the DB env vars; builds the UI and starts uvicorn)
cd web && npm test && npm run e2e && cd ..

# production smoke test
python scripts/smoke.py https://priorpath.vercel.app --require-explanations
```

Rebuilding the demo or migrating production needs secrets, so those steps run in your own terminal (see the plan's Task 10).

---

## What's next
- **Plan 4 release step:** record the extraction eval for each candidate model, write `evals/results/extraction-comparison.md`, `--promote` the chosen model's recording, record demo PDF extractions, then migrate Neon to `0f2549585d12` and deploy immediately after (the old code's `ON CONFLICT` no longer matches after the `llm_usage` primary-key change), and run the smoke test.
- **Plan 5:** Presidio redaction before model calls (PDF text and page images), Langfuse tracing, an LLM-judge faithfulness eval for explanations, the remaining Definition-of-Done docs, and the portfolio entry.
