# PriorPath v2 — Progress Log (Plans 1 to 3)

_Last updated: 2026-10-02 · Branch: `v2-bill-audit` (pushed, not merged; `main` still holds v1; production was deployed from this branch with the Vercel CLI) · Live: https://priorpath.vercel.app (API docs at `/api/docs`)_

PriorPath v2 rebuilds the old prior-authorization demo as an **AI medical bill auditor** for claims auditors and patient advocates. Deterministic rules over public CMS data decide what is wrong with a claim; an LLM only explains each finding in plain English, and a person approves every dispute letter.

- Design spec: `docs/superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md`
- Plan 1: `docs/superpowers/plans/2026-10-01-priorpath-v2-plan1-core-engine.md`
- Plan 2: `docs/superpowers/plans/2026-10-02-priorpath-v2-plan2-backend-ai.md`
- Plan 3: `docs/superpowers/plans/2026-10-02-priorpath-v2-plan3-reviewer-ui.md`

---

## At a glance

| | Plan 1 (core engine) | Plan 2 (backend, database, AI) | Plan 3 (reviewer UI) |
|---|---|---|---|
| Dates | 2026-10-01 | 2026-10-02 | 2026-10-02 |
| Commits | 14 (c9fe87b → b5c7c8d, plus final-review fixes) | 27 (08dbf27 → c0fafe3) | 13 commits (f394756..HEAD at ship, including the final-review fix commit) |
| Outcome | Rule engine + CMS data + FHIR input + eval gate + first deploy | Usable audit API with Postgres, per-visitor demo, grounded AI explanations, dispute letters | React reviewer UI served by the same FastAPI app, Playwright smoke test in CI |
| Tests at end | 73 | 183 | backend 189, web 43 + 1 E2E |
| Live | `/api/health`, `/api/version` | Full audit flow via `/api/docs` | Full flow in the browser at `/` |

All three plans were built with subagent-driven development: a fresh implementer per task (test-first), a separate reviewer per task, scoped re-reviews for every fix round, and an Opus whole-branch review before each deploy.

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
- Demo service dates are Oct–Dec 2026 (the loaded CMS quarter); bills from before 2026-10-01 get a "cannot audit" notice until earlier releases are loaded (spec §16 item 4).
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
| Plumbing | `ensureWorkspace()` is memoized so the first workspace-scoped call finishes before any other (no double workspaces). Money is shown with `Intl.NumberFormat` and never computed in the UI. Copy says the data is synthetic and nothing is sent anywhere. |
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

## How to run it

```bash
# local
docker compose up -d db
source .venv/bin/activate
pytest -q                                   # 189 tests against Docker Postgres
python -m evals.run --n 300 --seed 7        # rule-engine eval gate

# UI: unit tests and browser smoke test (needs the DB env vars; builds the UI and starts uvicorn)
cd web && npm test && npm run e2e && cd ..

# production smoke test
python scripts/smoke.py https://priorpath.vercel.app --require-explanations
```

Rebuilding the demo or migrating production needs secrets, so those steps run in your own terminal (see the plan's Task 10).

---

## What's next
- **Plan 4:** PDF bills (vision extraction + line review screen), Presidio redaction, Langfuse tracing and an LLM-judge faithfulness eval, README rewrite, and the remaining spec §16 items (eval negative plants, Q3 2026 reference data).
