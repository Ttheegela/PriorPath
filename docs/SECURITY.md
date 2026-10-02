# Security and compliance

PriorPath is a public demo of a medical-bill auditor. It is built for **synthetic and test data only**.
This page describes what the deployed demo (https://priorpath.vercel.app) actually does today, and
what is still missing before it could handle real patient data.

Status as of 2026-10-02 (branch `v2-bill-audit`). Items marked *(Plan 4)* describe the PDF upload path that
is being built and should be re-checked once it is merged.

## Not HIPAA compliant

Do not upload real patient data. The demo is not HIPAA compliant:

- **No Business Associate Agreements (BAAs)** are in place with any vendor in the data path: Vercel (hosting),
  Neon (Postgres), OpenRouter (model routing) or the model providers OpenRouter forwards to.
- **No user accounts or authentication.** Anyone with the URL gets an anonymous workspace.
- **No role-based access control.** Every workspace has one implicit reviewer role.
- **No redaction of PDF page images** before they reach a hosted model *(Plan 4; redaction planned with Presidio in Plan 5)*.
- **No breach-notification process,** incident-response plan or named security owner.
- **No formal risk assessment,** security policies, workforce training or vendor review.
- **Audit log is deleted with the workspace** after about a day, so it cannot serve as a retained access record.

### What a HIPAA-ready deployment would need

- [ ] Signed BAAs with every vendor that stores, processes or transmits PHI (hosting, database, logging,
      tracing, model providers), or replacement vendors that will sign one. Verify current vendor terms;
      some offer BAAs only on specific paid plans or for specific services.
- [ ] A model path covered by a BAA end to end (a router that forwards to many providers needs each one covered,
      or a single provider/endpoint under a BAA, or a self-hosted model).
- [ ] Real authentication (SSO or MFA), role-based access control and per-user audit trails instead of an
      anonymous per-browser workspace.
- [ ] PHI redaction or de-identification before any third-party model call, with tests, for both FHIR and PDF input.
- [ ] Audit-log retention that meets policy (HIPAA documentation is commonly kept 6 years), stored append-only
      and separately from the data it describes.
- [ ] Key management: managed secrets with rotation, documented access, and separate keys per environment.
- [ ] Encryption review: confirm at-rest encryption and backup/branch retention settings for the database and logs.
- [ ] A documented risk analysis and risk-management plan.
- [ ] Written policies: access control, incident response and breach notification, retention and disposal,
      contingency/backup, workforce training, vendor management.
- [ ] Logging that never contains PHI, with log retention and access controls.

## What is stored

All application data lives in one Postgres database (Neon in production).

| Table | Contents |
|---|---|
| `workspaces` | Random workspace id and creation time. |
| `cases` | The parsed claim as JSON: claim id, patient **pseudonym**, provider name, payer name, payer type, and line items (codes, modifiers, units, charges, dates of service, place of service, diagnosis codes). The raw uploaded FHIR file is not kept; patient names and addresses in it are not stored. |
| `flags` | Rule findings: rule id, severity, line ids, evidence row (codes, dates, indicators, rates, units), estimated overcharge, the AI explanation text, accept/reject status and the reviewer's reject reason. |
| `letters` | Dispute-letter drafts and approved text, and the generated baseline used for the number check. |
| `audit_events` | Per-workspace timeline of actions (upload, audit, accept, reject, letter actions) with small JSON details. |
| `llm_usage` | Per-workspace hourly counters for AI calls. |
| `case_documents` *(Plan 4)* | The uploaded PDF bytes, kept for the life of the workspace so page images can be re-rendered for line review. A PDF can contain anything printed on the bill, including names. |

Nothing is written to disk on the server; functions are stateless.

**Where:** Neon Postgres. Neon states that data is encrypted at rest and that client connections use TLS.
The site is served over HTTPS by Vercel.
Verify current vendor terms and region settings before relying on these for anything beyond a demo.

**How long:** a daily Vercel Cron job (`/api/internal/cleanup`, 05:00 UTC) deletes workspaces older than
24 hours, and every table above is removed with its workspace (`ON DELETE CASCADE`). Because the job runs
once a day, data can live up to about 48 hours. "Reset demo" deletes a workspace's cases (and their flags,
letters and case events) on demand and re-seeds the sample cases.
Deleted rows may still exist in the database provider's backups or point-in-time history for that
provider's retention window; check the Neon project's history retention setting.

## Access model

- Each browser gets a workspace from a signed cookie `pp_ws` (itsdangerous, signed with `SESSION_SECRET`;
  HttpOnly, SameSite=Lax, Secure on Vercel, 24 h max age).
- Every route looks up data by workspace. Ids from another workspace return **404**, not 403, so they do not
  reveal that the record exists.
- The cleanup endpoint requires `Authorization: Bearer <CRON_SECRET>`, compared in constant time.
- There are no user accounts. Anyone who obtains a valid cookie has full access to that workspace.

## What is sent to third parties

| Data | Recipient | When |
|---|---|---|
| One flag at a time: rule id, severity, finding message, evidence row (codes, dates, units, modifiers, rates, release version) and estimated overcharge | OpenRouter, which forwards to the configured model provider (`EXPLAIN_MODEL`) | When a reviewer asks for explanations. Patient pseudonym, provider and payer names are **not** sent. Demo-case explanations are precomputed. |
| PDF page images (PNG, one per page), unredacted *(Plan 4)* | OpenRouter → the configured vision model (`EXTRACT_MODEL`) | On PDF upload, only after the uploader confirms the bill is synthetic or a test bill (`confirm_synthetic=true`); otherwise the upload is refused. Redaction (Presidio) is planned for Plan 5. |
| Request logs and error stack traces | Vercel | Always. AI failure logs record the error type, not the prompt. |
| Uptime checks of `/api/health` | UptimeRobot | Periodically; no user data. |

Langfuse keys are configured, but tracing is not wired into the code yet; when it is, inputs must be redacted (spec §6).
OpenRouter and model providers have their own logging and retention policies, which vary by provider;
verify current vendor terms. Assume anything sent may be retained by them.

## Pseudonyms

Patient references in uploaded FHIR are replaced with a pseudonym `P-` + the first 16 hex characters of
HMAC-SHA256 keyed with `PSEUDONYM_SECRET` (falling back to `SESSION_SECRET`). Without the key, a pseudonym
cannot be recomputed from a guessed patient id. This is pseudonymization, not de-identification: provider,
payer, dates and codes are still stored.

## Licensed reference data

- CMS NCCI, MUE and Physician Fee Schedule files are downloaded locally into `data/reference/raw/`, which is
  git-ignored and excluded from the deployment bundle (`.vercelignore`). The CMS/AMA licence terms were accepted before download.
- Only a normalized subset is committed: code numbers, edit dates, modifier indicators, unit limits and
  computed national rates (`data/reference/subset/`).
- No CPT descriptors (AMA copyright) are committed or shown in the UI; it shows code numbers only.

## Budgets and limits

- Uploads: at most 4,000,000 bytes (413 above that); FHIR JSON, or PDF with at most 10 pages *(Plan 4)*.
  The FHIR parser never raises on bad input; bad items become errors with their JSON path.
- Field length limits on codes, modifiers, claim ids, provider/payer names and reject reasons.
- AI calls: 20 per workspace per hour and 100 per hour across all workspaces; the OpenRouter key also has a
  credit cap. Explanation output is capped at 300 tokens, with one retry if the number check fails.
- Explanation streams stop after 240 seconds (serverless time limit).
- Storage breaker: new workspaces and uploads get 503 when the database passes 400 MB.
- Prompt-injection controls: document content is data; explanations and letters may only contain numbers found
  in the flag evidence, and letter edits may not add amounts or URLs. A person must approve every letter;
  nothing is sent anywhere by the app.

## Secrets

Secrets are environment variables only, stored in Vercel as secret-type variables and never committed:
`DATABASE_URL`, `OPENROUTER_API_KEY`, `SESSION_SECRET`, `CRON_SECRET`, `LANGFUSE_*` (and optionally
`PSEUDONYM_SECRET`). `.env*` files are git-ignored. There is no automated rotation; rotating `SESSION_SECRET`
invalidates every workspace cookie, and rotating the pseudonym key changes future pseudonyms. Tests and CI use
throwaway values and a local Postgres; CI never calls OpenRouter.

## Reporting a problem

This is a portfolio project with no security team. Report issues by opening a GitHub issue without
sensitive details, or by contacting the repository owner directly.
