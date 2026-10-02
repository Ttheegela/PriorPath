# PriorPath v2: AI Medical Bill Auditor (Design Spec)

- **Date:** 2026-10-01
- **Author:** Tarun Theegela (solo)
- **Branch:** `v2-bill-audit` (merges to `main` when the Definition of Done in §12 is met)
- **Status:** Draft for review
- **Origin:** Half Baked idea "AI for Medical Bill Auditing" (2025-12-05), rebuilt on PriorPath v1's FastAPI + LangGraph skeleton. The v1 prior-authorization flow is retired; the name and repo stay.

---

## 1. Goal and success criteria

**User:** a claims auditor at a patient-advocacy firm or a self-funded employer's benefits team. They review many medical claims and need to find billing errors they can defend.

**Problem:** itemized bills and Explanation of Benefits (EOB) statements contain duplicates, unbundled services, impossible unit counts and prices far above benchmarks. Finding them by hand means cross-checking codes against large CMS tables.

**Product:** upload claims (FHIR EOB bundles or PDF bills) → deterministic rules flag errors against public CMS tables → an LLM explains each flag with the exact rule and evidence → the reviewer accepts or rejects flags → a dispute letter is drafted from accepted flags only → the reviewer approves and exports it.

**Success criteria:**
1. A public URL where anyone can open a demo case, review flags and export a letter with no signup or API key.
2. Rule precision and recall of 1.00 on the synthetic eval set (FHIR path); zero flags on clean claims.
3. PDF path: line extraction F1 ≥ 0.95 and end-to-end recall ≥ 0.90 per rule.
4. Zero unsupported numbers in explanations and letters (every number traceable to a flag's evidence).
5. CI runs lint, types, tests and deterministic evals on every push; the README shows the latest eval table.

**Out of scope (v2):** user accounts and roles, X12 835/837 EDI input, sending letters or submitting appeals, real patient data, payer-specific contract rates.

---

## 2. Approach

**Rules decide, the LLM explains (approach A), with an LLM "leads" tier as a week-3 stretch (C).**

- Every error flag comes from a pure, unit-tested rule over CMS reference data. Flags are reproducible and auditable.
- The LLM only (a) extracts line items from PDF bills, (b) explains a flag using the evidence attached to it, (c) drafts letters from accepted flags, and (d, stretch) proposes "leads" that are labeled as such and never counted as errors.
- Rejected alternative: an agent that decides flags itself (approach B). It isn't reproducible, risks invented errors on medical bills and is hard to evaluate.

---

## 3. Architecture

```
                 ┌──────────────── React + Vite + TypeScript (reviewer UI) ───────────────┐
                 │  Case queue → Line review (PDF) → Case detail (flags) → Letter review    │
                 └───────────────────────────────┬─────────────────────────────────────────┘
                                                 │ REST + SSE (audit progress)
┌──────────────────────── FastAPI (one Python function on Vercel) ───────────────────────────┐
│ ingest/      FHIR EOB parser ─────────────────────────────┐                                │
│              PDF → page images → vision LLM extraction ───┴─→ Claim + LineItems           │
│ phi/         redact identifiers before any hosted-LLM call                                │
│ rules/       (pure) R1 duplicates · R2 NCCI PTP · R3 MUE · R4 invalid code · R5 price      │
│ explain/     LangGraph: flag → retrieve rule text (pgvector) → cited explanation           │
│ letters/     draft from accepted flags → reviewer approves → export PDF / DOCX             │
│ leads/       (stretch) LLM "possible issue" tier, labeled lead                             │
│ reference/   loaders for CMS public files → versioned Postgres tables                      │
│ audit/       append-only audit log                                                         │
└───────────────┬──────────────────────────────────────────────┬────────────────────────────┘
        Neon Postgres (+ pgvector)                      OpenRouter (LLMs)  ·  Langfuse (traces)
```

**Hosting (all on Vercel):**
- One Vercel project: the Vite app is served as static files; FastAPI runs as a single Python function (Fluid Compute) behind `/api/*` rewrites.
- Neon Postgres through the Vercel Marketplace integration; each preview deployment gets its own Neon branch.
- Vercel limits that shape the design (Hobby plan): 300 s max function duration; 500 MB Python bundle; 4.5 MB request body; no in-memory state between requests; cron at most daily.

**Model provider: OpenRouter** via the `openai` Python SDK (`base_url=https://openrouter.ai/api/v1`). Model IDs live in config, not code:
- `EXTRACT_MODEL`: vision-capable model with structured output (default: a Claude Sonnet-class model).
- `EXPLAIN_MODEL`: fast, cheap model with structured output (default: a Claude Haiku-class model).
- Startup check: the configured models must support the features they're used for (image input, JSON schema output); fail fast otherwise. Exact model IDs are confirmed against OpenRouter's model list during implementation.

---

## 4. Data

### 4.1 Reference data (public, versioned)
| Table | Source | Use |
|---|---|---|
| `ncci_ptp` | CMS NCCI procedure-to-procedure edits (practitioner + outpatient hospital), quarterly | R2 |
| `mue` | CMS Medically Unlikely Edits (value + adjudication indicator), quarterly | R3 |
| `pfs_rvu` + `pfs_cf` | CMS Physician Fee Schedule RVU file + conversion factor | R5 national rate |
| `hcpcs_valid` | Codes valid per year (from the PFS / HCPCS files) with effective and end dates | R4 |
| `rule_text` (+ embeddings) | Plain-language rule descriptions written from the CMS policy manuals | explanations |

- Every row carries `ref_version` (e.g. `NCCI-2026Q4`). Every flag records the version used, so old audits reproduce exactly.
- A date of service with no loaded reference version produces a "cannot audit" flag, never a silent pass.
- CPT descriptors are AMA-licensed: the UI shows code numbers and the short descriptors CMS publishes, nothing more.
- Loaders run as a CLI (`python -m app.reference.load --release 2026Q4`) and are run locally / in CI against Neon, not inside a request.

### 4.2 Claims (synthetic only)
- **Generator** (`evals/generate.py`): 300 claims built from real codes, with planted errors and labels (60% with ≥1 error of type R1–R5, 40% clean). Emits FHIR `ExplanationOfBenefit` bundles.
- **PDF renderer:** the same claims as itemized-bill PDFs in 3 layouts, some with scan noise.
- **Synthea (optional):** used for realistic FHIR structure; its code coverage (much SNOMED, limited CPT) is verified in week 1 before relying on it.

### 4.3 Core models (Pydantic)
```
Claim:     id, case_id, patient_pseudonym, provider, payer, service_dates, lines[LineItem], source (fhir|pdf)
LineItem:  id, code, modifiers[], units, charge, date_of_service, place_of_service, diagnosis_codes[],
           source (structured|extracted), confidence (0–1, extracted only)
Flag:      id, claim_id, rule_id, severity (error|outlier|lead), line_ids[], evidence{table, ref_version, row},
           est_overcharge, explanation (nullable), status (open|accepted|rejected), reject_reason
Letter:    id, case_id, flag_ids[], body, status (draft|approved), approved_by, approved_at
AuditEvent: id, case_id, actor, action, before, after, ref_versions, at
```

---

## 5. Rule engine

Each rule is a pure function `rule(claim: Claim, ref: Reference) -> list[Flag]`, collected in a plain list. No network, no LLM.

| ID | Rule | Logic | Est. overcharge | Severity |
|---|---|---|---|---|
| R1 | Duplicate | Same code + modifiers + date of service + units on ≥2 lines | Charge of each extra line | error |
| R2 | NCCI unbundling | Pair present in `ncci_ptp` for the date. Modifier indicator 0: never allowed. Indicator 1: allowed only with a valid modifier (59, XE, XS, XP, XU, and other NCCI-associated modifiers) | Charge of the column-2 code | error |
| R3 | MUE units | Units > MUE value; per line or per day according to the adjudication indicator | Excess units × unit price | error |
| R4 | Invalid/deleted code | Code not valid on the date of service | Line charge | error |
| R5 | Price outlier | Charge > k × Medicare national rate (k = 3 default, adjustable per case) | Charge − k × rate | outlier |
| C | Leads (stretch) | LLM suggests e.g. procedure–diagnosis mismatch (possible upcoding) | none | lead |

- Errors, outliers and leads are separated in the UI, the letters and the metrics.
- Outliers appear in letters only as a request for itemized justification, never as an error claim.

---

## 6. LLM components

| Component | Input | Output | Guardrails |
|---|---|---|---|
| PDF extraction | Page images (redacted where possible) | `list[LineItem]` via JSON schema, with per-field confidence | Schema validation; lines < 0.9 confidence send the case to line review |
| Explanation (LangGraph) | One flag + its evidence + retrieved rule text | Short plain-English explanation | May cite only the flag's evidence; numeric check (§8) |
| Letter | Accepted flags + their explanations | Editable draft | Numbers locked to evidence; no URLs or outside facts; human approval required |
| Leads (stretch) | Claim lines + diagnosis codes | `list[Flag(severity=lead)]` | Labeled "lead", never counted as errors, scored separately |

All calls are traced in Langfuse (inputs redacted).

---

## 7. Reviewer workflow and UI

**Case lifecycle:**
`uploaded → extracting* → needs_line_review* → auditing → needs_review → letter_ready → approved → exported`
(* PDF path only; line review is skipped when every extracted line has confidence ≥ 0.9.) Every transition writes an `AuditEvent`.

**Screens (React + Vite + TypeScript + Tailwind):**
1. **Case queue:** pseudonym, provider, lines, errors found, est. overcharge, status; filter by status, sort by overcharge; "New case" upload (FHIR JSON or PDF ≤ 4 MB).
2. **Line review (PDF):** extracted lines beside the page image, low-confidence fields highlighted, editable; "Run audit".
3. **Case detail:** line table with flag badges; flag panel grouped Errors / Price outliers / Leads; each flag shows rule, evidence row, explanation, est. overcharge, Accept / Reject (+ reason); header totals.
4. **Letter review:** draft from accepted flags only; editable; approve → export PDF / DOCX. Nothing is sent anywhere.
5. **Audit log:** per-case timeline + global view.

Audit progress streams over SSE ("parsing… 12 lines… R2: 2 flags… explaining 3/5").

**API (OpenAPI at `/api/docs`):**
`POST /api/cases` · `GET /api/cases` · `GET /api/cases/{id}` · `PATCH /api/cases/{id}/lines` · `POST /api/cases/{id}/audit` (SSE) · `PATCH /api/flags/{id}` · `POST /api/cases/{id}/letter` · `POST /api/letters/{id}/approve` · `GET /api/letters/{id}/export?format=pdf|docx` · `GET /api/cases/{id}/audit-log` · `GET /api/health` · `GET /api/version` (includes loaded reference versions)

**Public demo (no signup):**
- Each visitor gets a workspace (signed cookie → `workspace_id` row in Postgres), seeded with 10 sample cases (FHIR + PDF, mixed errors). "Reset demo" re-seeds.
- Workspaces older than 24 h are deleted by a daily Vercel Cron call to `POST /api/internal/cleanup` (protected by `CRON_SECRET`).
- Rate limit: ~20 LLM calls per workspace per hour, counted in Postgres. Explanations for the 10 sample cases are precomputed, so the main demo path makes no LLM calls.
- One reviewer role; the audit log records the workspace as actor.

---

## 8. Evals

| Check | Metric | CI gate | When |
|---|---|---|---|
| Rules on FHIR input | Precision / recall per rule | 1.00 | Every push |
| Clean claims | False-positive rate | 0 | Every push |
| PDF extraction | Line-level F1 (code, units, charge) | ≥ 0.95 | Every push, on recorded LLM outputs; live on `main` (30-case sample) |
| PDF end to end | Recall per rule | ≥ 0.90 | Same as above |
| Explanations + letters | Unsupported numbers (every number must appear in the flag evidence) | 0 | Every push (recorded) + `main` (live) |
| Explanations | Faithfulness (Ragas / LLM judge) | ≥ 0.9, tracked | `main` |
| Leads (stretch) | Precision on planted mismatches | tracked, no gate | `main` |

The README shows the latest table plus a "known misses" list (error types no rule covers).

---

## 9. Error handling

| Failure | Behavior |
|---|---|
| LLM down or rate-limited | Flags still show (rules don't need the LLM); explanation shows "unavailable, retry"; letter waits for explanations |
| PDF extraction fails or low confidence | Case goes to `needs_line_review`; reviewer enters or fixes lines |
| Invalid FHIR | 422 with the failing field path; other claims in the batch still process |
| No reference data for the date of service | "Cannot audit" flag on those lines |
| Unknown/deleted code | R4 flag |
| Function nearing 300 s | Audit streams progress; explanation generation is split per flag so a case never needs one long call |
| Upload > 4 MB | 413 with a clear message (Vercel body limit is 4.5 MB) |

---

## 10. Security and PHI

- **Redaction before hosted LLMs.** FHIR: remove identifying fields structurally (`Patient.name`, address, telecom, member/subscriber IDs). PDF text: Microsoft Presidio with the small spaCy model. Hosted models see codes, units, charges and dates only (page images for extraction are of synthetic bills only in the demo).
- **Uploads:** PDF or JSON only, ≤ 4 MB, ≤ 10 pages.
- **Retention:** demo workspaces deleted after 24 h; no files kept on disk (functions are stateless; PDFs live in Postgres as bytes for the life of the workspace).
- **Prompt injection:** document text is data; extraction must pass schema validation; letters contain only facts from accepted flags (numeric check + no URLs).
- **Secrets:** environment variables only (`OPENROUTER_API_KEY`, `DATABASE_URL`, `LANGFUSE_*`, `CRON_SECRET`, `SESSION_SECRET`). `SECURITY.md` documents what is stored and for how long.

---

## 11. Testing and CI

- **Unit (pytest):** each rule, FHIR parser, redaction, overcharge math, numeric check.
- **Integration:** API against a real Postgres (Docker locally; a Neon branch in CI).
- **Evals:** §8.
- **E2E (Playwright):** one smoke test: open demo case → accept a flag → approve letter → export.
- **GitHub Actions:** ruff, mypy, eslint, tsc, pytest, deterministic evals, frontend build. Vercel deploys previews per PR and production on `main`.

---

## 12. Definition of Done

- [ ] Live production URL on Vercel; demo works with no signup or keys
- [ ] All CI gates in §8 green on `main`
- [ ] `docs/CUSTOMER_BRIEF.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`, `SECURITY.md`, `docs/LEARNING.md`
- [ ] README: problem first, 60-second demo GIF, architecture, eval table, known misses
- [ ] Langfuse traces visible for LLM calls; `/api/health` checks DB + reference versions; uptime monitor on the URL
- [ ] Portfolio entry updated (`content/projects/priorpath.md`: new summary, stack, `links.demo`)

---

## 13. Milestones

| Week | Deliverables |
|---|---|
| 1 | Repo restructure on `v2-bill-audit` · Vercel + Neon hello-world deploy · CMS loaders + versioned tables · rules R1–R5 + unit tests · claim generator · FHIR ingest · eval harness in CI |
| 2 | Postgres schema + API · queue / case / review UI · LangGraph explanations + numeric check · letters + export · audit log · demo workspaces + cleanup cron |
| 3 | PDF path (renderer, vision extraction, line review) · Presidio redaction · rate limiting + precomputed demo explanations · Langfuse · docs + README + portfolio entry · **stretch: leads (C)** |

---

## 14. What carries over from v1

| v1 piece | v2 |
|---|---|
| FastAPI app + SSE streaming pattern | Kept, restructured under `app/` |
| LangGraph workflow pattern | Reused for the explanation graph |
| Qdrant retrieval | Replaced by pgvector in Neon |
| Supabase audit log | Replaced by `AuditEvent` table in Neon |
| 5-agent prior-auth graph, CMS eye-care guideline texts, payer simulator | Retired (kept in git history) |
| Render / Railway configs, keep-warm workflow | Removed (Vercel) |
| Single-file HTML UI | Replaced by React + Vite |

---

## 15. Open items to verify during implementation

1. Exact OpenRouter model IDs and their support for image input + JSON schema output.
2. Synthea output's CPT/HCPCS coverage (decides whether Synthea is used at all).
3. Exact CMS file names/URLs for the current NCCI, MUE and PFS releases.
4. Python bundle size with Presidio + spaCy small + LangGraph stays under 500 MB.

---

## 16. Carry-over requirements for Plan 2 (from the Plan 1 final review)

1. R4 status I is Medicare-specific: codes like 80320, 77061/77062 are valid CPT for commercial payers. Once the claim carries payer type, status-I flags become `lead` unless the payer is Medicare; status D stays `error`. Keep the eval gate meaningful by adding a status-D code from an older RVU release or splitting R4 into R4-D / R4-I.
2. Case totals must not double-count: when summing est_overcharge for a case or queue row, cap each line's total at that line's billed charge (e.g. a `case_overcharge(claim, flags)` helper with a test for an R2 column-2 line that is also an R5 outlier).
3. Strengthen the eval gate with negative plants: an indicator-1 NCCI pair carrying 59/XS (expect no R2), a facility-POS line priced between 3x facility and 3x non-facility rate handling (expect R5 only per the facility rate), repeat-modifier 76/91 duplicates (expect no R1), 26/TC lines (expect no R5). The README must state what the gate covers.
4. Reference coverage: add Q3 2026 NCCI/MUE and RVU26C so bills dated July-September 2026 are auditable; add overlap validation for versions of the same kind in load_normalized and make generate's coverage window handle multiple versions.
5. Pseudonyms: replace unsalted 8-hex sha256 with HMAC-SHA256 keyed by a server secret and at least 16 hex chars before pseudonyms are stored.
