# PriorPath

[![CI](https://github.com/Ttheegela/PriorPath/actions/workflows/ci.yml/badge.svg?branch=v2-bill-audit)](https://github.com/Ttheegela/PriorPath/actions/workflows/ci.yml)

PriorPath audits medical bills for billing errors using public Medicare rules, explains each finding in plain English with AI (artificial intelligence), and drafts a dispute letter a person approves.

- **Live demo:** https://priorpath.vercel.app (no sign-up, nothing to install)
- **API (application programming interface) docs:** https://priorpath.vercel.app/api/docs
- **Design spec:** [`docs/superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md`](docs/superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md)
- **Build log:** [`docs/PROGRESS.md`](docs/PROGRESS.md)

Every abbreviation in this file is spelled out the first time it appears and again in the [Glossary](#glossary) at the end.

---

## Contents

1. [Try it in 60 seconds](#try-it-in-60-seconds)
2. [The problem](#the-problem)
3. [What it checks](#what-it-checks)
4. [How it works](#how-it-works)
5. [AI safeguards](#ai-safeguards)
6. [Evaluation](#evaluation)
7. [Tech stack](#tech-stack)
8. [Run it locally](#run-it-locally)
9. [API reference](#api-reference)
10. [Deployment](#deployment)
11. [Security and privacy](#security-and-privacy)
12. [Glossary](#glossary)
13. [Project status and roadmap](#project-status-and-roadmap)
14. [License and data notices](#license-and-data-notices)

---

## Try it in 60 seconds

1. Open https://priorpath.vercel.app. Your browser gets its own private workspace, already filled with synthetic demo cases that have been audited.
2. In the case queue, set **Sort** to **Highest est. overcharge** and open the top case.
3. Under **Billing errors**, read a finding: the rule, the evidence row from the Medicare table, the AI explanation and the estimated overcharge. Click **Accept** (or **Reject** with a reason).
4. In **Dispute letter**, click **Draft dispute letter**. Edit the text if you like, click **Approve letter**, then **Download .txt** or **Download .docx**.
5. Try your own upload from the queue:
   - **FHIR (Fast Healthcare Interoperability Resources) claim:** click **Download a sample claim (FHIR JSON)** to get a JSON (JavaScript Object Notation) file, then upload it. The audit runs automatically and the new case appears in the list.
   - **PDF (Portable Document Format) bill:** click **Download a sample bill (PDF)**, tick **"This is a synthetic or test bill (page images are sent to an AI model)"**, then upload it. An AI vision model reads the line items and the new case appears with an **Open** link. If any value was hard to read, the case opens on a line review screen where you check the lines against the page images and save before the audit runs.
6. **Audit log** (top of the page) shows every action taken in your workspace.

All demo data is synthetic. Workspaces are deleted after 24 hours (a daily cleanup job, so in practice up to about 48 hours). **Reset demo data** restores the sample cases at any time. Do not upload real patient data.

---

## The problem

Medical bills and EOB (Explanation of Benefits) statements regularly contain charges that should not be there: the same service billed twice, a service billed separately when it is already included in another one, impossible unit counts, codes that were not valid on the date of service, and prices far above any benchmark. Finding these by hand means cross-checking every line, by its CPT (Current Procedural Terminology) or HCPCS (Healthcare Common Procedure Coding System) code, against large public tables from CMS (Centers for Medicare & Medicaid Services): the NCCI (National Correct Coding Initiative) edits, the MUE (Medically Unlikely Edit) unit limits and the PFS (Physician Fee Schedule).

**Who it is for:**

- **Claims auditors** who review many claims and need findings they can defend line by line.
- **Patient advocates** who help people dispute a hospital or physician bill.
- **Self-funded employer benefits teams**, who pay claims out of the company's own money and want to catch overpayments.

**What goes wrong, with one real example per rule.** These are findings PriorPath produces on its synthetic demo and eval claims (codes are shown as numbers only; see [License and data notices](#license-and-data-notices)):

| Rule | Example finding (as PriorPath writes it) | Estimated overcharge |
|---|---|---|
| R0 coverage | A line dated before 2026-07-01 → "Cannot audit 1 line(s): no reference data loaded for …". PriorPath says it cannot check the line instead of silently passing it. | none (notice) |
| R1 duplicate | "97140 billed 2 times on 2026-12-02 with identical units and modifiers" | $65.70 (the extra line) |
| R2 unbundling | "97161 is bundled into 97163 on 2026-11-07 (NCCI edit, modifier indicator 0)" | $238.78 (the bundled line) |
| R3 unit limit | "96375: 7 units billed on one date of service (2026-11-27); Medicare's unit limit is 6" | $29.52 (1 excess unit at the billed unit price) |
| R4 invalid code | "83992 is not valid for Medicare billing on 2026-11-27; Medicare requires a different code" | $100.00 (the whole line) |
| R5 price outlier | "96375 charged 86.51 for 1 unit(s); that is more than 3x the Medicare national rate of 15.70 per unit" | $39.41 (charge minus 3x the rate) |

---

## What it checks

Each rule has an ID (identifier) from R0 to R5 and is a pure Python function over the claim and the loaded CMS reference tables (`app/rules/`). No network calls, no AI. Every finding ("flag") records which reference release it used, for example `NCCI-2026Q4`, so it can be traced and reproduced.

| Rule ID | Name | What it catches | Data source | Severity | How the over-charge is estimated |
|---|---|---|---|---|---|
| R0 | Coverage | Lines whose date of service (DOS) falls outside the loaded reference releases | Reference version table | notice | None. The lines are reported as "cannot audit". |
| R1 | Duplicate | Two or more lines with the same code, modifiers, DOS and units. Lines carrying a repeat modifier are exempt: 76 (repeat procedure, same physician), 77 (repeat procedure, another physician) or 91 (repeat lab test). | The claim itself | error | Charge of every line after the first |
| R2 | NCCI unbundling | A code pair listed in the NCCI (National Correct Coding Initiative) PTP (procedure-to-procedure) edits for that DOS. Modifier indicator 0: never billed together. Indicator 1: allowed only with an NCCI-associated modifier, for example 25 (separate evaluation and management service), 59 (distinct procedural service), 91 (repeat clinical diagnostic laboratory test), XE (separate encounter), XS (separate structure), XP (separate practitioner) or XU (unusual non-overlapping service). Indicator 9: edit not applied. | NCCI PTP edits, practitioner | error | Charge of the column-2 (bundled) line |
| R3 | MUE unit limit | Units above the MUE (Medically Unlikely Edit) limit, checked per line or per DOS according to the MUE adjudication indicator | NCCI MUE table, practitioner | error | Excess units x (billed charge / billed units) |
| R4 | Invalid code | A code with PFS (Physician Fee Schedule) status D (deleted) or I (not valid for Medicare) on the DOS | PFS RVU (Relative Value Unit) file, status column | error; status I becomes a **lead** when the payer is not Medicare | Whole line charge (errors); $0 for leads |
| R5 | Price outlier | A charge above 3x the Medicare national rate x units. Lines at a facility POS (place of service, for example 21, 22, 23) use the facility rate; all others use the non-facility rate. Lines with modifier 26 (professional component) or TC (technical component) are skipped (the PFS rate is the global rate). | PFS RVU file, national rates | outlier | Charge minus 3x rate x units |

**Errors, price outliers and leads are kept apart** in the UI (user interface), in the case totals and in letters:

- A **billing error** (R1 to R4) breaks a published rule. It goes into the dispute letter as an error, and its estimated overcharge counts toward the case's "Est. overcharge (errors)" total.
- A **price outlier** (R5) is not wrong by itself: providers may set their own prices. The letter only asks the provider for an itemized justification, and the amount is shown separately as "Above benchmark (outliers)".
- A **lead** is worth checking but not an error. Today the only lead is R4 status I for a commercial or unknown payer (status I is a Medicare rule; a commercial plan may accept the code). Leads never go into letters and never count toward totals.
- A **notice** (R0) is information only and cannot be accepted or rejected.

Case totals never count more than a line was billed: if a line is both an R2 bundled line and an R5 outlier, the overlap is capped at the line's charge (`app/rules/totals.py`).

**Reference data loaded** (see [`data/reference/SOURCES.md`](data/reference/SOURCES.md)): 2026 Q3 (third quarter, July 1 to September 30) and 2026 Q4 (fourth quarter, October 1 to December 31) releases of NCCI PTP, MUE and the PFS RVU file (RVU26C and RVU26D). Bills dated July to December 2026 can be audited; other dates get an R0 notice.

---

## How it works

The browser talks to the API over HTTP (Hypertext Transfer Protocol) with JSON bodies; explanations arrive over an SSE (server-sent events) stream. The built UI files are served from Vercel's CDN (content delivery network).

```
 Browser: React single-page app (case queue, line review, case detail, letter, audit log)
    |  HTTP + JSON, SSE stream for explanations
    v
 FastAPI app (one Python function on Vercel; the built UI is served from Vercel's CDN)
    |
    |-- ingest/      FHIR EOB parser (never raises; bad items become errors with a JSON path)
    |                PDF -> page images (pypdfium2) ----------------------> vision model (OpenRouter)
    |                                                                        returns lines only
    |-- llm/extract  schema check + LineItem validation, per-field confidence -> line review
    |-- rules/       R0-R5, pure functions over CMS reference data (data/reference/subset)
    |-- llm/explain  LangGraph: draft -> number check -> one retry ------> text model (OpenRouter)
    |-- services/    letters (template from accepted flags), budgets, demo seeding, audit log
    v
 Neon Postgres: workspaces, cases, case_documents (PDF bytes), flags, letters,
                audit_events, llm_usage   (all deleted with the workspace)
```

**The flow, step by step:**

1. **Upload.** `POST /api/cases` takes either a FHIR `ExplanationOfBenefit` bundle (JSON, JavaScript Object Notation) or a PDF bill, up to 4,000,000 bytes. A FHIR bundle can hold several claims; each becomes a case, and bad items are reported with their JSON path while the rest still load.
2. **Extraction (PDF only).** Each page (at most 10) is rendered to a JPEG (Joint Photographic Experts Group) image at 150 DPI (dots per inch) and sent to a vision LLM (large language model) with a strict JSON schema. The model returns code, modifiers, units, charge, DOS and POS for every line, each with a confidence score. Our code (not the model) validates every row; invalid rows are dropped with a per-line error and never guessed. If any line has confidence below 0.9, or no lines were read, the case goes to **line review**: the reviewer sees the extracted lines beside the page images, fixes them, saves, and runs the audit.
3. **Rules.** R0 to R5 run on the claim (deterministic, no AI).
4. **Explanations.** For each flag, a LangGraph loop asks a small text model to explain the finding in at most three sentences using only the rule text and the flag's evidence. A number check rejects any draft that uses an amount or percentage not found in the evidence; the model gets one retry. Explanations stream to the browser over SSE (server-sent events).
5. **Review.** A person accepts or rejects each flag (a rejection needs a reason).
6. **Letter.** A template (not a model) builds the dispute letter from accepted errors and outliers only. The reviewer may edit it, but an edit may not add numbers or links that were not in the generated text.
7. **Approve and export.** Approval only succeeds if the accepted findings are unchanged since drafting. Then the letter downloads as TXT (plain text) or DOCX (Microsoft Word document). PriorPath never sends the letter; the person does.

Every step writes an entry to the workspace's audit log.

**Rules decide, AI explains.** The model never decides whether something is an error. Billing errors come only from rules over published CMS tables, so every finding is reproducible, testable (see [Evaluation](#evaluation)) and comes with the exact table row behind it. AI is used where it helps and its output can be checked: reading a bill into structured lines (validated and reviewable) and turning a finding into plain English (number-checked). An agent that decided flags itself was considered and rejected in the design: it would not be reproducible, could invent errors on medical bills, and would be hard to evaluate.

---

## AI safeguards

| Safeguard | What it does | Where |
|---|---|---|
| Number grounding check | Every dollar amount and percentage in an explanation must match one in the flag's evidence, by type (money, percent, plain number). Plain counts from 0 to 10 are allowed ("billed 2 times"). Rewrites such as "$4k", "USD 7" (USD: United States dollars), "seven dollars", digits from other scripts and URLs (Uniform Resource Locators, web addresses) are rejected. A failed draft gets one retry; after that the explanation is marked unavailable, and the flag itself still shows. | `app/llm/grounding.py`, `app/llm/explain.py` |
| Letter edit check | The same number check runs on letter edits against the generated letter; links are refused; approval fails if the accepted findings changed after drafting. | `app/api/letters.py` |
| Budgets and rate limits | Two separate hourly pools per workspace: 20 explanations and 20 PDF pages. Across all workspaces, 100 AI calls per hour in total, shared by both kinds. A PDF that would go over budget is refused with 429 ("hourly AI limit reached; try again later") and nothing is stored. Explanations are capped at 300 output tokens; a stream stops after 240 seconds. PDF reading also stops at 240 seconds (504, nothing stored). The OpenRouter key also has a credit cap. | `app/services/llm_budget.py` |
| What is sent to a model | **Explanations:** one flag at a time: rule ID (identifier), severity, finding message, evidence row (codes, dates, units, modifiers, rates, release) and estimated overcharge. **Never sent:** patient pseudonym, provider name, payer name, or any other claim line. **PDF extraction:** the page images, with identifiers blacked out on text-layer pages (see the redaction row). Demo-case explanations are precomputed, so browsing the demo makes no model calls. | `app/llm/explain.py`, `app/services/pdf_cases.py` |
| PDF redaction | On pages with a text layer, Presidio (small spaCy model) finds names, phone numbers, email addresses, SSNs (Social Security numbers), locations and labelled member/policy IDs in the page text, and those regions are painted black on the image before it is sent. Codes, modifiers, units, charges, dates of service and place of service are not targeted, and tests check this on generated bills of every layout. Scanned or rotated pages can't be redacted and are sent as they are; the upload response reports `pages_redacted`, `pages_not_redactable`, `pages_partially_redacted` and entity counts. Best-effort, not de-identification. | `app/ingest/redact.py`, `app/ingest/pdf.py` |
| PDF synthetic-only confirmation | Page images go to a hosted model and redaction is best-effort, so the API refuses a PDF unless the request carries `confirm_synthetic=true` (422 "PDF uploads must be synthetic or test bills; confirm to continue"), and the UI requires the checkbox. | `app/api/cases.py`, `web/src/components/CaseQueue.tsx` |
| Extraction is not a decision | The vision model only reads lines; rules still decide flags. Its output must pass the JSON schema and `LineItem` validation; text on the page is treated as data, not instructions. Low-confidence lines go to human line review. | `app/llm/extract.py` |
| Human approval for letters | Letters are templates over findings a person accepted; a person approves and downloads every letter. Nothing is sent by the app. | `app/services/letters.py` |

---

## Evaluation

Both evals run in CI (continuous integration) on every push and never call a model there: the rule eval is fully deterministic, and the PDF extraction eval replays recorded model output once a recording is committed. Full description of the rule eval: [`docs/EVALS.md`](docs/EVALS.md).

### Rule engine eval (gate)

`python -m evals.run --n 300 --seed 7` builds 300 synthetic claims from real codes in the committed CMS subset, plants labeled errors, sends every claim through the FHIR writer and parser, and runs the rules. Latest result ([`evals/results/latest.md`](evals/results/latest.md)). TP, FP and FN are true positives, false positives and false negatives. "Neg plants" are traps a rule must *not* flag; "Neg FP" counts how many it flagged anyway.

| Rule | Support | TP | FP | FN | Precision | Recall | Neg plants | Neg FP |
|---|---|---|---|---|---|---|---|---|
| R1 | 59 | 59 | 0 | 0 | 1.000 | 1.000 | 33 | 0 |
| R2 | 61 | 61 | 0 | 0 | 1.000 | 1.000 | 24 | 0 |
| R3 | 51 | 51 | 0 | 0 | 1.000 | 1.000 | 0 | 0 |
| R4 | 41 | 41 | 0 | 0 | 1.000 | 1.000 | 0 | 0 |
| R5 | 43 | 43 | 0 | 0 | 1.000 | 1.000 | 54 | 0 |

Claims: 300 (clean: 110, clean with flags: 0); FHIR parse errors: 0.

**What the gate covers.** CI fails unless, for R1 to R5: precision and recall are 1.000; each rule has at least 10 positive plants; claims with no positive plant have no error or outlier flags; there are no FHIR parse errors; and no negative plant is flagged by the rule it targets (with at least 10 negative plants each for R1, R2 and R5).

- **Positive plants** (must be flagged): an exact duplicate line (R1); an NCCI pair with modifier indicator 0 or 1 and no bypass modifier (R2); units above the MUE limit (R3); a PFS status D or I code (R4); an office line above 3x the non-facility rate, or a hospital line (POS 22) between 3x the facility rate and 3x the non-facility rate (R5).
- **Negative plants** (must not be flagged): repeats carrying modifier 76 or 91 (R1); an indicator-1 NCCI pair with 59 or XS (R2); a 26 or TC line priced 3.5x to 6x the non-facility rate (R5); an office line (POS 11) between 3x the facility rate and 3x the non-facility rate (R5). The facility/office pair checks that R5 picks the right rate in both directions.
- The negative plants were checked by mutation: removing the 76/91 exemption, the NCCI bypass modifiers or the 26/TC skip, or using the facility rate in offices, each fails the gate.
- CI also re-runs the eval and fails if the committed results file differs (`git diff --exit-code evals/results`), so the table above always matches the code.

A perfect score shows the rules do what they say on synthetic claims built from the same rules; it does not show they match every payer's real adjudication.

### PDF extraction eval

`python -m evals.extract_eval` renders 30 synthetic claims (seed 11) as PDF bills in three layouts (`table`, `statement`, `compact`), half of them with simulated scan noise, and reads them with the vision model. It scores:

- **Line-level F1** (the balance of precision and recall) on matched lines (code, units and charge must all match). Gate: at least 0.95.
- **End-to-end recall per rule:** claims are rebuilt from the extracted lines, the rules run, and the flags are scored against the original labels. Gate: at least 0.90 for every rule with 3 or more plants, and 0 negative plants flagged.
- Breakdowns by layout and by clean vs noisy pages.

Each candidate model's output is recorded once (`--record <model>`, needs `OPENROUTER_API_KEY`) into `evals/recorded/candidates/`. The chosen model's recording is promoted (`--promote <model>`) to `evals/recorded/extraction.json`, the one file CI replays (`--replay`), so CI never calls a model; replay re-runs our own parsing and validation on the raw model JSON. The chosen model's results table is in [`evals/results/extraction.md`](evals/results/extraction.md); the hand-written comparison of candidate models (scores, cost, latency) is in [`evals/results/extraction-comparison.md`](evals/results/extraction-comparison.md).

### Explanation model choice

Compared on the 12 demo flags by how many explanations passed the number check:

| Model | Grounded | Price (input / output per million tokens) |
|---|---|---|
| `deepseek/deepseek-v4-pro` | 0 / 12 (reasoning tokens likely used up the 300-token cap) | $0.21 / $0.42 |
| **`deepseek/deepseek-v4-flash`** (chosen) | **11 / 12** | **$0.04 / $0.08** |

### Known misses (not covered by any gate)

- No rule for diagnosis-to-procedure mismatch, upcoding, modifier misuse beyond NCCI bypass, global surgery periods, or payer-specific contract rates.
- MUE edge cases beyond per-line vs per-day, and NCCI edits deleted part-way through a quarter, are unit-tested only.
- Payer-aware R4 (status I as a lead) is unit-tested; the eval claims are all Medicare.
- Explanation and letter quality: the number check is unit-tested but there is no faithfulness gate yet (an LLM-judge faithfulness eval is planned for Plan 5).
- A few rare amount formats still pass the number check ("7 US dollars", "7 euros"), and plain counts up to 10 are always allowed, so a letter edit could change "2 times" to "9 times".
- Every eval claim and bill is synthetic.

---

## Tech stack

| Tool | Used for | Why |
|---|---|---|
| Python 3.12 + FastAPI | Backend API | Typed request handling, automatic OpenAPI docs at `/api/docs`, runs as one Vercel Python function |
| Pydantic | Claim, line and flag models; request validation | Validation at the trust boundary; money as `Decimal` rounded to cents |
| SQLAlchemy 2.0 + psycopg 3 | Database access | Typed ORM (object-relational mapper) and plain SQL (Structured Query Language) where needed (upserts for budgets) |
| Alembic | Database migrations | Versioned schema; `alembic check` in CI catches models that drift from migrations |
| Neon Postgres | Production database | Serverless Postgres that fits a free-tier demo; Postgres 17 in Docker for tests |
| LangGraph | Explanation loop (draft, check, retry once) | Small explicit graph instead of a hidden agent loop |
| OpenRouter (via the `openai` SDK, software development kit) | Text and vision model calls | One key, model IDs in environment variables, easy model comparison |
| pypdfium2 + Pillow | PDF page rendering | Ships as Python wheels, so nothing extra to install on Vercel |
| React 19 + Vite + TypeScript | Reviewer UI | Typed single-page app with a fast build |
| Tailwind CSS (Cascading Style Sheets) | Styling | Black, white and neutral grays only; state shown by words and weight, never color |
| Vitest + Testing Library | UI unit tests | Same config as Vite |
| Playwright | E2E (end-to-end) browser tests | Drives the real built UI and API in CI and against production |
| pytest | Backend tests | Runs against a real Postgres, not mocks |
| ruff | Python lint and format | One fast tool for both |
| mypy (strict) | Python type checking | Catches type errors across `app`, `evals` and `scripts` |
| oxlint | TypeScript/React lint | Warning-free is part of the frontend chain |
| GitHub Actions | CI | Three jobs: backend, frontend, E2E |
| Vercel | Hosting, CDN (content delivery network) and the daily cleanup cron | One project serves the UI and the API |

---

## Run it locally

**Prerequisites:** Python 3.12, Node.js 22 or newer, Docker (for Postgres), Git.

```bash
git clone https://github.com/Ttheegela/PriorPath.git
cd PriorPath
git checkout v2-bill-audit

# 1. Database: Postgres 17 on localhost:5433 (database priorpath_test)
docker compose up -d db

# 2. Python environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# 3. Schema
export DATABASE_URL=postgresql+psycopg://priorpath:priorpath@localhost:5433/priorpath_test
alembic upgrade head

# 4. API on http://127.0.0.1:8000 (docs at /api/docs)
SESSION_SECRET=dev-secret uvicorn app.main:app --reload --port 8000

# 5. UI on http://localhost:5173 (in a second terminal; Vite proxies /api to port 8000)
cd web && npm ci && npm run dev
```

The local database is the same one the tests use, and the tests empty its tables, so run tests and the dev server one at a time.

### Environment variables

| Variable | Required | What it does |
|---|---|---|
| `DATABASE_URL` | Yes | Postgres connection string (`postgresql+psycopg://...`). Neon in production. |
| `SESSION_SECRET` | Yes, for the API | Signs the workspace cookie `pp_ws`. Also the fallback key for patient pseudonyms. Tests set their own. |
| `CRON_SECRET` | Only for cleanup | Bearer token for `GET /api/internal/cleanup`. If unset, cleanup always returns 401. |
| `OPENROUTER_API_KEY` | Optional | Enables AI explanations and PDF extraction. Without it, explanations are marked unavailable (demo explanations are precomputed and still show), and PDF uploads return 503. |
| `EXPLAIN_MODEL` | Optional | OpenRouter model ID for explanations. Default `deepseek/deepseek-v4-flash`. |
| `EXTRACT_MODEL` | Optional | OpenRouter vision model ID for PDF extraction. Default set in `app/llm/vision.py` (currently `google/gemini-2.5-flash-lite`). |
| `PSEUDONYM_SECRET` | Optional | Key for patient pseudonyms. Falls back to `SESSION_SECRET`. |
| `PRIORPATH_DEMO` | Optional | `0` turns off seeding demo cases into new workspaces. Default on. |

### Tests and evals

```bash
# Backend (needs the Docker database; do not export SESSION_SECRET or CRON_SECRET in this shell)
ruff check . && ruff format --check . && mypy app evals scripts
pytest -q
alembic check

# Rule eval (writes evals/results/latest.md and latest.json)
python -m evals.run --n 300 --seed 7

# PDF extraction eval, replaying a committed recording (no API key needed)
python -m evals.extract_eval --replay evals/recorded/extraction.json
# Record a candidate model's output into evals/recorded/candidates/ (calls OpenRouter; needs OPENROUTER_API_KEY)
python -m evals.extract_eval --record google/gemini-2.5-flash-lite
# Make a candidate the recording CI replays
python -m evals.extract_eval --promote google/gemini-2.5-flash-lite

# Frontend
cd web
npm run lint && npm test && npm run build
npm run e2e            # Playwright: builds the UI, starts uvicorn on :8000 (venv active; needs DATABASE_URL and SESSION_SECRET)
PLAYWRIGHT_BASE_URL=https://priorpath.vercel.app npm run e2e   # same tests against production
```

`npx playwright install chromium` is needed once before the first E2E run.

### Rebuilding the demo data

```bash
PYTHONPATH=. python scripts/build_demo.py                     # data/demo/cases.json (10 FHIR claims)
PYTHONPATH=. python scripts/build_demo.py --explain           # also precomputed explanations (needs OPENROUTER_API_KEY)
PYTHONPATH=. python scripts/build_demo.py --bills             # the 2 sample PDF bills (no key)
PYTHONPATH=. python scripts/build_demo.py --bills --extract   # recorded extractions for the PDF bills (needs key)
```

---

## API reference

Interactive OpenAPI docs: https://priorpath.vercel.app/api/docs (locally at http://127.0.0.1:8000/api/docs).

Every route under `/api` except health, version, samples and cleanup belongs to the caller's workspace, identified by the `pp_ws` cookie. A request without a valid cookie gets a new workspace and cookie. Ids that belong to another workspace return 404. Any route that has to create a new workspace returns 503 when the database is near its storage limit. Malformed ids or bodies return 422 (FastAPI validation).

| Method | Path | Purpose | Status codes |
|---|---|---|---|
| GET | `/api/health` | Liveness check, returns `{"status": "ok"}` | 200 |
| GET | `/api/version` | App version and the loaded reference releases with their date ranges | 200 |
| GET | `/api/workspace` | The caller's workspace id and creation time (creates one if needed) | 200, 503 |
| POST | `/api/cases` | Upload a FHIR bundle or a PDF as the raw request body. Query: `payer_type` = `medicare`, `commercial` or `unknown` (default); `confirm_synthetic=true` (required for PDFs). FHIR cases are audited at once; PDF cases are audited unless they need line review. | 201; 413 over 4,000,000 bytes; 422 not JSON / not a FHIR bundle / no valid claims (with paths) / PDF not confirmed, encrypted, unreadable or over 10 pages; 429 AI budget too small for the whole PDF; 502 vision model failed; 503 storage full or PDF extraction not configured; 504 extraction took too long (over 240 seconds) |
| GET | `/api/cases` | List the workspace's cases with counts and totals | 200 |
| GET | `/api/cases/{case_id}` | Case detail: lines, flags, latest letter | 200, 404 |
| GET | `/api/cases/{case_id}/pages/{page}` | One page of a PDF case as a JPEG image (1-based) | 200, 404 |
| PATCH | `/api/cases/{case_id}/lines` | Replace the case's lines after line review; clears flags and draft letters | 200, 404, 409 approved letter exists, 422 invalid line or duplicate line ids |
| POST | `/api/cases/{case_id}/audit` | Run (or re-run) the rules | 200, 404, 409 approved letter exists or lines still need review |
| POST | `/api/cases/{case_id}/explain` | Stream explanations for pending or unavailable flags as SSE events `start`, `explanation`, `error`, `done` | 200 (stream), 404 |
| PATCH | `/api/flags/{flag_id}` | Accept, reject or reopen a flag (`{"status": "accepted"}`, `{"status": "rejected", "reject_reason": "..."}` or `{"status": "open"}`) | 200, 404, 422 notice or missing reason |
| POST | `/api/cases/{case_id}/letter` | Draft a dispute letter from accepted errors and outliers | 201, 404, 409 approved letter exists or nothing accepted |
| PATCH | `/api/letters/{letter_id}` | Edit a draft letter | 200, 404, 409 already approved, 422 adds numbers or links |
| POST | `/api/letters/{letter_id}/approve` | Approve a draft | 200, 404, 409 already approved or findings changed since drafting |
| GET | `/api/letters/{letter_id}/export?format=txt` | Download an approved letter (`txt` or `docx`) | 200, 404, 409 not approved |
| GET | `/api/audit-log` | The workspace's last 200 audit events | 200 |
| GET | `/api/cases/{case_id}/audit-log` | One case's audit events | 200, 404 |
| POST | `/api/demo/reset` | Delete the workspace's cases and re-seed the demo | 200 |
| GET | `/api/samples/claim.json` | Sample FHIR bundle (2 demo claims) as a download | 200 |
| GET | `/api/samples/bill.pdf` | Sample synthetic PDF bill as a download | 200, 404 none in this deployment |
| GET | `/api/internal/cleanup` | Delete workspaces older than 24 hours (Vercel cron). Needs `Authorization: Bearer <CRON_SECRET>`. | 200, 401 |

GET, POST and PATCH are HTTP (Hypertext Transfer Protocol) methods: read, create or run, and partly update.

Example: upload the sample claim with curl.

```bash
curl -s -c jar -b jar -o claim.json https://priorpath.vercel.app/api/samples/claim.json
curl -s -c jar -b jar -H 'Content-Type: application/json' --data-binary @claim.json \
  'https://priorpath.vercel.app/api/cases?payer_type=medicare'
```

---

## Deployment

- **Vercel, one project.** `vercel.json` builds the UI (`cd web && npm ci && npm run build`, output to `public/`). The FastAPI app in `app/main.py` is a single Python function (it includes `data/**` and excludes `web/**`). `app.frontend("/", directory=public, fallback="index.html")` registers the UI, and `[tool.vercel.fastapi.static] exclude = true` in `pyproject.toml` tells Vercel to serve those files from its CDN instead of bundling them into the function. `.vercelignore` keeps raw CMS files, tests, evals and docs out of the upload.
- **Production deploys** are made from the `v2-bill-audit` branch with the Vercel CLI (command-line interface): `vercel deploy --prod`. (`main` still holds v1 until v2 is finished.)
- **Neon Postgres** holds all data. Secrets are Vercel environment variables of the secret type: `DATABASE_URL`, `OPENROUTER_API_KEY`, `SESSION_SECRET`, `CRON_SECRET` (and optionally `PSEUDONYM_SECRET`).
- **Migrations run from your own terminal**, not during the build (the `migrations/` folder is not deployed):
  ```bash
  DATABASE_URL='<Neon connection string>' alembic upgrade head
  ```
- **Daily cleanup cron:** `vercel.json` calls `GET /api/internal/cleanup` at 05:00 UTC (Coordinated Universal Time); Vercel sends `CRON_SECRET` as the bearer token.
- **Smoke test** against a deployment (demo case, explanations, accept, letter, approve, export):
  ```bash
  python scripts/smoke.py https://priorpath.vercel.app --require-explanations
  ```

---

## Security and privacy

**This is a demo for synthetic data only. It is not HIPAA (Health Insurance Portability and Accountability Act) compliant.** There are no BAAs (Business Associate Agreements) with any vendor, no user accounts, and PDF page images reach a hosted model with only best-effort redaction (scanned pages not at all). Do not upload PHI (protected health information) or any real patient data. The full picture, including what a HIPAA-ready deployment would need, is in [`docs/SECURITY.md`](docs/SECURITY.md).

- **Synthetic data only.** Demo claims and bills are generated from real codes with made-up patients, providers and amounts. PDF uploads require the synthetic-bill confirmation described above.
- **Pseudonyms.** Patient references in uploaded FHIR are replaced with `P-` plus the first 16 hex characters of a keyed HMAC-SHA256 (hash-based message authentication code using SHA-256, the Secure Hash Algorithm with a 256-bit output). Without the server key a pseudonym cannot be recomputed from a guessed patient id. Patient names and addresses from the FHIR file are not stored. This is pseudonymization, not de-identification: provider, payer, dates and codes are kept.
- **What is stored, and for how long.** The parsed claim, flags, letters, the audit log, hourly AI counters and, for PDF cases, the uploaded PDF bytes (which can contain anything printed on the bill). Everything belongs to a workspace and is deleted with it. The daily cleanup deletes workspaces older than 24 hours, so data lives up to about 48 hours. The workspace cookie carries no timestamp, so a copied cookie keeps working until that cleanup. Nothing is written to disk on the server.
- **Licensed CMS raw files are not committed.** The raw NCCI, MUE and PFS downloads live in git-ignored `data/reference/raw/` and are excluded from the deployment. Only a normalized subset is committed: code numbers, edit dates, indicators, unit limits and computed national rates.
- **No CPT descriptors.** CPT (Current Procedural Terminology) code descriptions are copyrighted by the AMA (American Medical Association). PriorPath commits and shows code numbers only.
- **Access.** Workspaces are isolated by a signed, HttpOnly, SameSite=Lax cookie; the cleanup route requires a secret compared in constant time.

---

## Glossary

| Abbreviation | Full form | Meaning here |
|---|---|---|
| AI | Artificial intelligence | Here, the hosted language and vision models PriorPath calls. |
| AMA | American Medical Association | Owns the copyright on CPT code descriptions. |
| API | Application programming interface | The HTTP endpoints under `/api`. |
| BAA | Business Associate Agreement | A HIPAA contract a vendor signs before handling PHI. None are in place. |
| CDN | Content delivery network | Vercel's edge servers that serve the built UI files. |
| CI | Continuous integration | GitHub Actions runs lint, types, tests and evals on every push. |
| CLI | Command-line interface | The `vercel` command used to deploy. |
| CMS | Centers for Medicare & Medicaid Services | United States agency that publishes the NCCI, MUE and fee schedule files. |
| CPT | Current Procedural Terminology | The AMA's five-character procedure codes (for example 99213). |
| CSS | Cascading Style Sheets | Styling language for web pages; Tailwind generates it. |
| CSV | Comma-separated values | Format of the committed reference subset files. |
| D, I (PFS status) | Deleted; not valid for Medicare | PFS status indicators that R4 checks. |
| DOCX | Word document (the .docx file extension) | Microsoft Word file format for letter export. |
| DOS | Date of service | The day a billed service was provided; every lookup is resolved by it. |
| DPI | Dots per inch | Resolution PDF pages are rendered at (150). |
| E2E | End-to-end | Browser tests that drive the real UI and API (Playwright). |
| EDI | Electronic data interchange | Standard electronic claim formats (X12 837 claims, 835 remittances). Out of scope. |
| EOB | Explanation of Benefits | A payer's statement of what was billed, allowed and paid; FHIR models it as `ExplanationOfBenefit`. |
| F1 | F1 score | Harmonic mean of precision and recall; used for PDF line extraction. |
| FHIR | Fast Healthcare Interoperability Resources | HL7's JSON standard for health data; PriorPath reads FHIR claim bundles. |
| FN | False negative | A planted error the rule missed. |
| FP | False positive | A flag the rule raised where nothing was planted. |
| GET, POST, PATCH | HTTP methods | Read; create or run; partly update. |
| HCPCS | Healthcare Common Procedure Coding System | CMS code set: CPT codes plus a second level of codes for supplies, drugs and services that CPT does not cover. |
| HIPAA | Health Insurance Portability and Accountability Act | United States law governing PHI. This demo is not HIPAA compliant. |
| HL7 | Health Level Seven | The standards body that publishes FHIR. |
| HMAC | Hash-based message authentication code | A keyed hash; used for patient pseudonyms (HMAC-SHA256). |
| HTTP, HTTPS | Hypertext Transfer Protocol (Secure) | The web protocol the API uses; HTTPS is the encrypted version. |
| ICD-10 | International Classification of Diseases, 10th revision | Diagnosis codes; stored with lines but not checked by any rule yet. |
| ID | Identifier | For example a rule ID (R1) or a model ID. |
| JPEG | Joint Photographic Experts Group | Image format of rendered PDF pages (quality 85). |
| JSON | JavaScript Object Notation | Text data format for FHIR uploads, API bodies and model output. |
| LLM | Large language model | A text (or vision) AI model, called through OpenRouter. |
| MB | Megabyte | Upload limit is 4,000,000 bytes (shown as 4 MB in the UI). |
| MUE | Medically Unlikely Edit | CMS limit on units of a service per line or per day (rule R3). |
| NCCI | National Correct Coding Initiative | CMS program whose PTP edits list code pairs not billed together (rule R2). |
| ORM | Object-relational mapper | SQLAlchemy maps Python classes to database tables. |
| PDF | Portable Document Format | Bill format PriorPath can read with a vision model. |
| PFS | Physician Fee Schedule | Medicare's payment rates and code status per service (rules R4, R5). |
| PHI | Protected health information | Health data that identifies a person. Never upload it here. |
| POS | Place of service | Two-digit CMS code for where a service happened (11 office, 22 hospital outpatient); decides facility vs non-facility rate in R5. |
| PTP | Procedure-to-procedure | NCCI edit type: column-1 / column-2 code pairs. |
| Q3, Q4 | Third quarter, fourth quarter | Calendar quarters of 2026 that the reference data covers. |
| R0, R1, R2, R3, R4, R5 | Rule IDs | PriorPath's rules: coverage, duplicate, NCCI, MUE, invalid code, price outlier. |
| RVU | Relative Value Unit | CMS measure of a service's resources; the PFS RVU file holds rates and code status. |
| RVU26C, RVU26D | 2026 PFS RVU file, releases C and D | July and October 2026 releases. |
| SDK | Software development kit | Here, the `openai` Python package used to call OpenRouter. |
| SHA, SHA256 | Secure Hash Algorithm (256-bit) | Hash function inside HMAC-SHA256; SHA-256 also fingerprints PDF uploads. |
| SPA | Single-page application | The React UI: one web page that updates in place. |
| SQL | Structured Query Language | Database query language (Postgres). |
| SSE | Server-sent events | One-way HTTP stream used to send explanations as they finish. |
| TC | Technical component | Modifier: only the equipment/technical part of a service. R5 skips it. |
| TP | True positive | A planted error the rule correctly flagged. |
| TXT | Plain text file | Letter export format. |
| UI | User interface | The React reviewer screens. |
| URL | Uniform Resource Locator | A web address. Links are refused in explanations and letter edits. |
| US | United States | As in "7 US dollars". |
| USD | United States dollar | Currency code; "USD 7" is one of the amount formats the number check rejects. |
| UTC | Coordinated Universal Time | Time zone of the cleanup schedule (05:00 UTC). |
| UUID | Universally unique identifier | Format of workspace, case, flag and letter ids. |
| X12 | Accredited Standards Committee X12 | The EDI standard family for claims (837) and remittances (835). Out of scope. |
| XE | Separate encounter | NCCI bypass modifier: service in a separate encounter on the same day. |
| XP | Separate practitioner | NCCI bypass modifier: performed by a different practitioner. |
| XS | Separate structure | NCCI bypass modifier: on a separate organ or structure. |
| XU | Unusual non-overlapping service | NCCI bypass modifier: does not overlap the usual parts of the main service. |
| 25 (modifier) | Significant, separately identifiable evaluation and management service | Office visit on the same day as a procedure; an NCCI-associated modifier. |
| 26 (modifier) | Professional component | Only the physician's part of a service. R5 skips it. |
| 59 (modifier) | Distinct procedural service | Main NCCI bypass modifier for services that are truly separate. |
| 76 (modifier) | Repeat procedure by the same physician | Exempts a line from R1. |
| 77 (modifier) | Repeat procedure by another physician | Exempts a line from R1. |
| 91 (modifier) | Repeat clinical diagnostic laboratory test | Exempts a line from R1. |

Other terms: **flag** (one finding from one rule), **lead** (worth checking, not an error), **outlier** (price far above the Medicare benchmark, not an error by itself), **workspace** (a private, temporary space per browser), **pseudonym** (a stand-in for a patient id), **unbundling** (billing parts of a service separately when one code covers them all), **facility / non-facility rate** (Medicare pays the physician less when a facility such as a hospital also bills for the same service).

---

## Project status and roadmap

| Plan | Status | What it delivered |
|---|---|---|
| Plan 1: core engine | Done | Rules R0 to R5 over real 2026 CMS data, FHIR parser, synthetic eval with a CI gate, first Vercel deploy |
| Plan 2: backend and AI | Done | Postgres, per-browser workspaces, upload/audit/review API, grounded AI explanations, dispute letters, budgets, daily cleanup |
| Plan 3: reviewer UI | Done | React UI served by the same app, Playwright smoke test in CI |
| Plan 4: usability, PDF bills, README | Built; release step next (record the extraction eval, deploy) | Upload runs the audit, sample files, PDF bills with vision extraction and line review, PDF extraction eval, Q3 2026 reference data, eval negative plants, `docs/SECURITY.md`, this README |
| Plan 5 | Next | Presidio redaction of PDF text and images before model calls, Langfuse tracing, an LLM-judge faithfulness eval for explanations, the remaining Definition-of-Done docs, portfolio write-up |

Details, decisions and what each review caught: [`docs/PROGRESS.md`](docs/PROGRESS.md).

---

## License and data notices

- **Code:** MIT License — see [`LICENSE`](LICENSE). The license covers the source code only, not the CMS reference data or the AMA-licensed CPT code set described below.
- **CMS data:** NCCI, MUE and PFS files are public data published by CMS ([`data/reference/SOURCES.md`](data/reference/SOURCES.md) lists every file, release and download URL). The committed subset holds only code numbers, edit dates, indicators, unit limits and computed rates.
- **AMA notice:** CPT is a registered trademark of the American Medical Association. CPT codes and descriptions are copyright AMA. PriorPath uses code numbers only and includes no CPT descriptors; the CMS/AMA licence terms were accepted before the raw files were downloaded.
- PriorPath is not legal, medical or billing advice. Findings are rule-based estimates for a person to review.
