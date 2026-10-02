# Runbook

How to deploy, migrate, rotate secrets, check health, handle incidents, re-record evals and keep costs down
for https://priorpath.vercel.app. System overview: [`ARCHITECTURE.md`](ARCHITECTURE.md). What is stored and
sent where: [`SECURITY.md`](SECURITY.md).

Conventions:

- Run commands from the repository root with the virtual environment active (`source .venv/bin/activate`).
- The Vercel CLI is used through `npx vercel` (or a global `vercel`); the repo is linked to the `priorpath`
  project (`.vercel/project.json`, git-ignored). Run `npx vercel login` and `npx vercel link` once on a new machine.
- Production secrets are Vercel secret-type variables: they can't be pulled back out of Vercel. Steps that need
  one (migrations, recordings, a manual cleanup call) run in your own terminal with the value pasted into an
  environment variable. Use `read -rs NAME && export NAME` so the value doesn't land in shell history, and never
  paste a value into a file in the repo, a commit, an issue or a chat.

## Deploy

### From the CLI (current)

```bash
# 1. Green locally: backend chain, frontend chain (CI runs the same on push)
ruff check . && ruff format --check . && mypy app evals scripts && pytest -q && alembic check
python -m evals.run --n 300 --seed 7 && git diff --exit-code evals/results
python -m evals.extract_eval --replay evals/recorded/extraction.json && git diff --exit-code evals/results
(cd web && npm run lint && npm test && npm run build)

# 2. Preview deploy, then check it (bundle size and cold start show in the build output and the first request)
npx vercel deploy
curl -s https://<preview-url>/api/health

# 3. Production
npx vercel deploy --prod
python scripts/smoke.py https://priorpath.vercel.app --require-explanations
```

Preview deployments sit behind Vercel's deployment protection unless it is turned off for the project; open the
preview in a browser where you are logged in to Vercel, or use a protection-bypass token.

If the deploy includes a migration, follow [Migrations](#migrations) first.

**Roll back** to the previous production deployment if a release breaks:

```bash
npx vercel list priorpath --environment production   # find the last good deployment URL
npx vercel rollback <deployment-url> --yes
```

A rollback does not undo a database migration; see the migration rules below before rolling back across one.

### From Git

CI (`.github/workflows/ci.yml`) runs on every push and pull request: backend, frontend, then E2E. If the Vercel
project is connected to the GitHub repository with `main` as its production branch, a push to `main` deploys
production and every other branch or pull request gets a preview deploy. Merge to `main` only when CI is green on
the branch, and still run the smoke test against production afterwards. Until v2 is on `main`, production is
deployed only from the CLI; a push of v1 code to `main` must not reach a Git-connected production.

## Migrations

Migrations are not part of the build (the `migrations/` folder is not deployed). They run from your terminal
against Neon. Use Neon's direct (non-pooled) connection string for migrations.

```bash
read -rs DATABASE_URL && export DATABASE_URL     # paste the Neon connection string; nothing is echoed
alembic current                                  # what Neon is on now
alembic history                                  # what the code expects (head)
alembic upgrade head --sql                       # print the SQL without running it; read it
```

Before running it, create a Neon branch of the production database in the Neon console as a restore point.

**Additive changes** (new table, new nullable column, new column with a server default): the old code keeps
working on the new schema.

```bash
alembic upgrade head
npx vercel deploy --prod
```

**Changes to a primary key, unique constraint or column the running code writes**: migrate, then deploy
**immediately**, back to back, at a quiet time. The old code breaks as soon as the migration commits. Example:
`0f2549585d12` added `kind` to the `llm_usage` primary key, and the old code's `ON CONFLICT (workspace_id,
hour_start)` upsert no longer matched any constraint, so every explanation would fail until the new code was live.

```bash
alembic upgrade head && npx vercel deploy --prod
python scripts/smoke.py https://priorpath.vercel.app --require-explanations
unset DATABASE_URL
```

**Undo:** `alembic downgrade -1` steps back one revision. Read the downgrade first: `0f2549585d12`'s downgrade
deletes the `extract` usage rows and drops `case_documents`. Downgrade, then roll back the deployment to code
that matches the older schema. If data is damaged, restore from the Neon branch made before the migration.

## Secrets

All production secrets live in Vercel (Production environment). Rotating one is always: create the new value at
its source, replace it in Vercel, redeploy (running deployments keep the old value), verify, then revoke the old
value at its source.

```bash
npx vercel env add NAME production --force   # prompts for the value; --force replaces the existing one
npx vercel deploy --prod                     # environment changes apply only to new deployments
```

| Secret | Where the new value comes from | What rotating it does | Verify |
|---|---|---|---|
| `DATABASE_URL` | Neon console: reset the role's password, copy the pooled connection string (the runtime uses the pooled one; migrations use the direct, non-pooled one, see [Migrations](#migrations)) | Old connections fail after the reset, so reset, replace and redeploy together | `curl -s https://priorpath.vercel.app/api/health` shows `"db": "ok"` |
| `OPENROUTER_API_KEY` | OpenRouter dashboard: create a key with a credit limit, then delete the old key | Nothing user-visible | Upload the sample PDF (below) and get 201, or upload a FHIR claim of your own and see explanations `ready` |
| `SESSION_SECRET` | A long random string, piped straight in so it is never shown: `python -c "import secrets; print(secrets.token_urlsafe(48))" \| npx vercel env add SESSION_SECRET production --force` | Every existing `pp_ws` cookie becomes invalid: all visitors get a new workspace, and the old ones are deleted by the daily cleanup. If `PSEUDONYM_SECRET` is unset, future patient pseudonyms change too | Open the site: a fresh demo workspace appears |
| `PSEUDONYM_SECRET` (optional) | Random string as above | New uploads get different pseudonyms for the same patient id; stored cases keep theirs | Upload a FHIR claim; the case shows a `P-` pseudonym |
| `CRON_SECRET` | Random string as above | Vercel Cron sends it as the bearer token automatically after the redeploy | Next day, the cron run for `/api/internal/cleanup` in the Vercel dashboard shows 200, not 401 |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (and `LANGFUSE_BASE_URL` if not the default cloud region) | Langfuse project settings: create a key pair, delete the old one after the redeploy | Tracing is off unless both keys are set; a bad key only loses traces (logged once per instance) | A new trace appears after one explanation or PDF upload |

`EXPLAIN_MODEL` and `EXTRACT_MODEL` are not secrets but are set the same way. Change them only after the new
model has passed the matching eval (see [Evals and demo data](#evals-and-demo-data)).

## Health checks

```bash
curl -s https://priorpath.vercel.app/api/health    # 200 {"status":"ok","db":"ok","reference":[...]}
                                                   # 503 {"status":"degraded","db":"unavailable"}
curl -s https://priorpath.vercel.app/api/version   # app version and each reference release's date range
python scripts/smoke.py https://priorpath.vercel.app --require-explanations
(cd web && PLAYWRIGHT_BASE_URL=https://priorpath.vercel.app npm run e2e)
```

- **UptimeRobot** polls `/api/health`. Since Plan 5 it fails when the database does not answer (503), not only
  when the function is down.
- The smoke test uses a demo case, whose explanations are precomputed, so it does **not** prove the OpenRouter
  key works. To check the live models, upload the sample PDF bill (one model call per page, counted against the
  hourly page budget):

  ```bash
  curl -s -c jar -b jar -o bill.pdf https://priorpath.vercel.app/api/samples/bill.pdf
  curl -s -c jar -b jar -H 'Content-Type: application/pdf' --data-binary @bill.pdf \
    'https://priorpath.vercel.app/api/cases?payer_type=medicare&confirm_synthetic=true'
  ```

  Expect 201 with `"redaction": {"pages_redacted": 1, ...}` for a one-page text-layer bill.
- **Langfuse:** each model call is a generation named `explain`, `extract` or `judge` with the model id, prompt
  version, latency, token usage, finish reason and `ok`/`error_type`. A run of `ok: false` with one
  `error_type` points at the incident below. There should never be prompt text, model output, images or names in
  a trace; if there are, turn tracing off (remove a Langfuse key and redeploy) and treat it as a bug.
- **Logs:** `npx vercel logs --environment production` (add `--follow` to stream). AI failures log the error
  type, never the prompt.

## Incidents

### OpenRouter 401 (bad or revoked key)

Symptoms: new explanations show "explanation model unavailable"; PDF uploads return 502 "the AI model could not
read this bill"; logs show `explanation LLM call failed: AuthenticationError` or `vision request failed: ...401`.
Demo explanations still show (they are precomputed), so the site looks healthy.

1. Check the key in the OpenRouter dashboard (deleted, disabled, or wrong value pasted into Vercel).
2. Replace it: `npx vercel env add OPENROUTER_API_KEY production --force`, then `npx vercel deploy --prod`.
3. Verify with the sample-PDF upload above.

### Rate limits (429)

Two different 429s:

- **Ours:** "hourly AI limit reached; try again later" (PDF) or "hourly explanation limit reached" (flag reason).
  A workspace used its 20 explanations or 20 PDF pages this hour, or all workspaces together used 100 calls.
  Counters reset at the top of each UTC hour. This is the cost control working; if the global cap is being hit
  by real use, raise the constants in `app/services/llm_budget.py` and deploy, after checking the credit cap.
- **OpenRouter's or the provider's:** shows as "explanation model unavailable" or a 502 on PDF upload, with
  `RateLimitError` in the logs. Wait and retry (flags marked unavailable can be explained again). If it persists,
  check the model's status page on OpenRouter or switch `EXPLAIN_MODEL` / `EXTRACT_MODEL` to an evaluated
  alternative.

### OpenRouter credit exhausted

Symptoms as for 401; the vision log line carries the provider's 402 (insufficient credits) message and explanation failures log `APIStatusError`. Check usage in the OpenRouter
dashboard. Either top up and raise the key's credit limit, or leave it: the demo keeps working on precomputed
explanations, and only new uploads lose AI features until credit returns. Look at the Langfuse token usage per
`kind` to see what used it.

### Storage breaker (503 "the demo is full right now")

The database passed 400 MB, so new workspaces and uploads are refused. Existing workspaces still work.

1. Check the size: `psql "$DATABASE_URL" -c "select pg_size_pretty(pg_database_size(current_database()))"`.
2. Run the cleanup now instead of waiting for 05:00 UTC (deletes workspaces older than 24 hours):
   ```bash
   read -rs CRON_SECRET    # the header goes to curl on stdin, so the secret is never in its argv
   printf 'Authorization: Bearer %s\n' "$CRON_SECRET" | curl -s -H @- https://priorpath.vercel.app/api/internal/cleanup
   unset CRON_SECRET
   ```
3. If it's still full (for example a bot creating workspaces), delete younger workspaces; everything they own
   cascades: `psql "$DATABASE_URL" -c "delete from workspaces where created_at < now() - interval '6 hours'"`.
4. Deleted rows free space for reuse, but `pg_database_size` may not drop until Postgres reclaims it. If it
   stays above the limit, run `vacuum full case_documents` (the PDF bytes are the largest table) at a quiet
   time; it locks that table while it runs.

### Demo seeding failure (new visitors see an empty or partial queue)

Seeding runs in a savepoint, so a failure leaves a working, empty workspace; each PDF demo bill has its own
savepoint and is skipped alone.

1. `npx vercel logs --environment production` and look for `demo seeding failed`, `demo bill ... could not be
   seeded` or `no recorded PDF extractions`.
2. Check the demo files are in the deployment: `data/demo/cases.json`, `data/demo/explanations.json`,
   `data/demo/bills/*.pdf`, `data/demo/pdf_extractions.json` (`vercel.json` includes `data/**`).
3. Reproduce locally: `pytest -q tests/test_demo.py` against the Docker database. A schema change that the demo
   insert doesn't match, or a malformed `pdf_extractions.json`, are the usual causes.
4. Fix, deploy, then in the browser click **Reset demo data** (or start a new private window) to re-seed.
5. `PRIORPATH_DEMO=0` turns seeding off entirely if it must be stopped while a fix is prepared.

### PDF extraction 504 ("this bill took too long to read")

The upload passed the 240-second extraction deadline (checked before each page); nothing was stored and only
the pages already read used budget.

1. Check the per-page `latency_ms` on `extract` generations in Langfuse, or the request duration in the Vercel
   logs. One slow page near the 25-second model timeout times 10 pages is enough to hit the deadline.
2. If the model is slow for everyone, check its OpenRouter status page; consider an evaluated alternative
   `EXTRACT_MODEL`.
3. For a single large bill, ask for a shorter PDF (fewer pages). The 10-page limit and the deadline stay: the
   function is killed at 300 seconds.

### Neon down or cold

Symptoms: `/api/health` returns 503 `{"status": "degraded", "db": "unavailable"}`; UptimeRobot alerts; API routes
return 500.

1. Check the Neon console and status page: compute suspended, quota reached, an incident, or a project setting
   changed.
2. If the password or host changed, update `DATABASE_URL` (see [Secrets](#secrets)) and redeploy.
3. Connections give up after 10 s (`connect_timeout`), and a missing `DATABASE_URL` also makes the health check
   return 503.
4. When Neon is back, run the smoke test. No data repair is needed: requests that failed rolled back.

## Evals and demo data

Recording calls OpenRouter and needs your key in the environment (`read -rs OPENROUTER_API_KEY && export
OPENROUTER_API_KEY`). CI only replays committed recordings; commit recordings and results together, or CI's
`git diff --exit-code evals/results` fails.

```bash
# Rule eval (deterministic, no key)
python -m evals.run --n 300 --seed 7

# PDF extraction eval. --record resumes from evals/recorded/candidates/extraction-<model>.json,
# so move that file aside first when the inputs changed (renderer, redaction, prompt).
PYTHONPATH=. python -m evals.extract_eval --n 30 --seed 11 --record google/gemini-2.5-flash-lite
PYTHONPATH=. python -m evals.extract_eval --promote google/gemini-2.5-flash-lite   # the file CI replays
python -m evals.extract_eval --replay evals/recorded/extraction.json                 # gate: F1 0.95, recall 0.90

# Explanation faithfulness. Recordings are keyed by flag, not by explanation text, and --record skips keys it
# already has, so delete evals/recorded/faithfulness.json after regenerating explanations.
PYTHONPATH=. python -m evals.faithfulness --record google/gemini-2.5-flash
python -m evals.faithfulness --replay evals/recorded/faithfulness.json                # gate: 0.90
```

Update `evals/results/extraction-comparison.md` by hand when comparing candidate models, and the README's eval
numbers when results change.

**Demo data** (`scripts/build_demo.py`):

```bash
PYTHONPATH=. python scripts/build_demo.py                        # data/demo/cases.json (10 FHIR claims, seed 2026)
PYTHONPATH=. python scripts/build_demo.py --explain              # + explanations.json (needs key)
PYTHONPATH=. python scripts/build_demo.py --bills --extract --explain   # PDF bills, extractions, their explanations
```

- `--explain` without `--bills` **rewrites** `explanations.json` with the FHIR cases only. Run `--bills --extract
  --explain` afterwards to add the PDF bills' explanations back (extraction is skipped for bills already
  extracted with the same model).
- `--bills` re-renders `data/demo/bills/*.pdf` from the current renderer. If the rendered bytes change, the
  recorded extractions in `pdf_extractions.json` no longer describe the committed bills, and `--extract` will not
  notice (it skips bills already extracted with the same model). Delete the affected entries before `--extract`,
  or leave the committed bills alone.
- After new explanations, re-record faithfulness (above) and check `pytest -q` (demo count tests) before
  committing. `samples/test-bills/` is regenerated separately with `PYTHONPATH=. python scripts/make_test_bills.py`.

## Cost controls

| Control | Setting | Where to change it |
|---|---|---|
| OpenRouter key credit limit | Hard cap on total spend for the key | OpenRouter dashboard |
| Global AI calls | 100 per hour across all workspaces | `GLOBAL_EXPLANATIONS_PER_HOUR`, `app/services/llm_budget.py` |
| Per-workspace AI calls | 20 explanations and 20 PDF pages per hour | same file |
| Explanation output | 300 tokens, at most 2 attempts | `app/llm/client.py`, `app/llm/explain.py` |
| Vision output | 4000 tokens per page, at most 10 pages per PDF, budget checked for all pages before the first call | `app/llm/vision.py`, `app/ingest/pdf.py`, `app/services/pdf_cases.py` |
| Precomputed demo explanations | The main demo path makes no model calls | `data/demo/explanations.json` |
| Model choice | Explain `deepseek/deepseek-v4-flash` ($0.04 / $0.08 per million input / output tokens when chosen); extract `google/gemini-2.5-flash-lite` ($0.10 / $0.40) | `EXPLAIN_MODEL`, `EXTRACT_MODEL` |
| Database | 400 MB storage breaker; daily cleanup of workspaces older than 24 hours | `app/services/capacity.py`, `vercel.json` |
| Tests and CI | Never call OpenRouter or Langfuse | fakes in `tests/`; replayed recordings |

Prices are OpenRouter's at the time each model was chosen; check current prices before relying on them.

## Incident: web app returns {"detail":"Not Found"} at `/`

Cause seen on 2026-10-02: Vercel stopped promoting the built UI to the CDN (a top-level ASGI middleware disables promotion) while `exclude = true` removed it from the function. Check `pyproject.toml` has `[tool.vercel.fastapi.static]` `cdn = true`. After any change to middleware, `app/main.py` mounting or `pyproject.toml` Vercel settings, open the preview's `/` (via `npx vercel curl <preview>/ -- -I`) before promoting. Hobby-plan rollback only reaches the previous production deployment, so prefer fixing forward.
