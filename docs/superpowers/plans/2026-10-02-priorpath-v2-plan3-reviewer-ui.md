# PriorPath v2 — Plan 3: Reviewer UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Plan 2 API into a usable web app at https://priorpath.vercel.app: case queue, case detail with flags and streamed explanations, letter review and export, and an audit log. One Playwright smoke test proves the whole demo flow in CI and against production.

**Architecture:** A React + Vite + TypeScript + Tailwind app lives in `web/`. `vite build` writes to the repo-root `public/`, which Vercel serves from its CDN next to the existing FastAPI function (`/api/*`). There is no router library: the URL query string (`/?case=<id>`, `/?view=log`) holds the screen, so every page load is `index.html` and needs no rewrite rules. In development, Vite proxies `/api` to uvicorn so the workspace cookie stays same-origin.

**Tech Stack:** React, Vite, TypeScript (strict), Tailwind CSS v4 (`@tailwindcss/vite`), Vitest + Testing Library (jsdom), ESLint (Vite template config), Playwright (Chromium). Backend unchanged except one new audit-log router.

**Spec:** `docs/superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md` (§7 reviewer workflow and UI, §11 testing and CI, §12 Definition of Done). Progress so far: `docs/PROGRESS.md`.

## Global Constraints

- Branch `v2-bill-audit`. Commit trailer exactly: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Screens per spec §7: case queue (filter by status, sort by overcharge, "New case" upload of FHIR JSON ≤ 4 MB), case detail (line table with flag badges; flags grouped Errors / Price outliers / Leads; each shows rule, evidence, explanation, est. overcharge, Accept / Reject with reason; header totals), letter review (draft from accepted flags only, editable, approve, export), audit log (per-case timeline + global view). The PDF line-review screen is Plan 4.
- Copy rules: errors, outliers and leads are always labelled separately; outliers are "price outliers", never "errors". Show code numbers only, never CPT descriptors (AMA licence). Say plainly that data is synthetic and nothing is sent anywhere.
- Money from the API is a JSON string with 2 decimals (Pydantic `Decimal`); format it with `Intl.NumberFormat` USD, never do arithmetic on it in the UI.
- The first workspace-scoped request must complete before any other API call (a visitor with no cookie would otherwise get two workspaces). `ensureWorkspace()` is memoized and `App` renders nothing workspace-scoped until it resolves.
- No new backend dependencies. No new frontend runtime dependencies beyond `react`, `react-dom`, `tailwindcss`, `@tailwindcss/vite` (dev tooling is fine).
- Accessibility basics: every control has an accessible name; tests query by role/label, not CSS classes.
- Backend CI chain must stay green: `ruff check . && ruff format --check . && mypy app evals scripts && pytest -q && alembic check && python -m evals.run --n 300 --seed 7 && git diff --exit-code evals/results`. Frontend chain: `cd web && npm run lint && npm test && npm run build`.
- Never call OpenRouter from tests; never run anything against the production database.

## Review Focus

1. **Cookie race on first visit** — a fresh browser that fires two API calls at once must still end up with exactly one workspace (React StrictMode runs effects twice in dev). Pinned by the `ensureWorkspace` memoization test in Task 3.
2. **API errors shown, not swallowed** — 409 (letter already approved), 422 (reject without reason, letter edit adds a number, bad FHIR with paths), 413 (upload too big), 503 (demo full) must appear as readable text near the control. Pinned by `errorMessage` tests in Task 3 and the flag/letter error tests in Tasks 5–6.
3. **Explanation stream ends badly** — the stream can contain `error` events, end with `remaining > 0`, or fail before `done`; the UI must stop showing progress, report what happened, and reload the case. Pinned in Task 5.
4. **Approved letter locks the case** — after approval, Accept/Reject, Run audit and Redraft are disabled and the export links appear. Pinned in Task 6.
5. **Deep link to a missing or foreign case** — `/?case=<unknown>` shows "Case not found" with a way back, not a blank page or crash. Pinned in Task 5.

---

## File Structure

```
app/api/audit_log.py          NEW  GET /api/audit-log, GET /api/cases/{id}/audit-log
app/api/schemas.py            MOD  AuditEventOut
app/main.py                   MOD  include audit_log router
tests/test_api_audit_log.py   NEW
web/                          NEW  Vite app (package.json, vite.config.ts, tsconfig*, eslint.config.js, index.html)
web/src/main.tsx, index.css   NEW  entry + Tailwind import
web/src/App.tsx               NEW  shell, workspace bootstrap, screen switch
web/src/lib/api.ts            NEW  typed fetch client + types
web/src/lib/sse.ts            NEW  incremental SSE parser
web/src/lib/format.ts         NEW  money/status/severity labels
web/src/lib/route.ts          NEW  query-string routing hook
web/src/components/CaseQueue.tsx, CaseDetail.tsx, FlagCard.tsx, LetterPanel.tsx, AuditLog.tsx   NEW
web/src/**/*.test.ts(x)       NEW  Vitest tests
web/e2e/smoke.spec.ts, web/playwright.config.ts   NEW
vercel.json, .vercelignore, .gitignore, .github/workflows/ci.yml   MOD
README.md, docs/PROGRESS.md   MOD
```

---

### Task 1: Audit log API

**Files:**
- Create: `app/api/audit_log.py`, `tests/test_api_audit_log.py`
- Modify: `app/api/schemas.py`, `app/main.py`

**Interfaces:**
- Consumes: `AuditEvent` model (`app/db/models.py`), `WorkspaceDep`, `SessionDep` (`app/api/deps.py`), `get_case_or_404(session, ws, case_id)` (`app/services/cases.py`).
- Produces: `GET /api/audit-log` and `GET /api/cases/{case_id}/audit-log`, both returning `list[AuditEventOut]` newest first, at most 200: `{id: int, case_id: uuid | null, actor: str, action: str, detail: object, ref_versions: str[], at: datetime}`.

- [ ] **Step 1: Write the failing tests** — `tests/test_api_audit_log.py`

```python
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api import audit_log
from app.api.deps import get_reference
from app.main import app
from tests.api_helpers import sample_claim, upload
from tests.helpers import FIXTURE_REF


def setup_function() -> None:
    app.dependency_overrides[get_reference] = lambda: FIXTURE_REF


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_case_log_is_newest_first_and_scoped_to_the_case(db: Engine) -> None:
    c = TestClient(app)
    first, second = upload(c, [sample_claim("A"), sample_claim("B")]).json()["cases"]
    c.post(f"/api/cases/{first['id']}/audit")
    events = c.get(f"/api/cases/{first['id']}/audit-log").json()
    assert [e["action"] for e in events] == ["audit_run", "case_created"]
    assert {e["case_id"] for e in events} == {first["id"]}
    assert second["id"] not in {e["case_id"] for e in events}


def test_workspace_log_lists_every_case(db: Engine) -> None:
    c = TestClient(app)
    ids = {case["id"] for case in upload(c, [sample_claim("A"), sample_claim("B")]).json()["cases"]}
    assert ids <= {e["case_id"] for e in c.get("/api/audit-log").json()}


def test_other_workspace_cannot_read_a_case_log(db: Engine) -> None:
    owner, stranger = TestClient(app), TestClient(app)
    case_id = upload(owner, [sample_claim()]).json()["cases"][0]["id"]
    assert stranger.get(f"/api/cases/{case_id}/audit-log").status_code == 404
    assert case_id not in {e["case_id"] for e in stranger.get("/api/audit-log").json()}


def test_log_is_capped(db: Engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(audit_log, "AUDIT_LOG_LIMIT", 2)
    c = TestClient(app)
    upload(c, [sample_claim("A"), sample_claim("B"), sample_claim("C")])
    assert len(c.get("/api/audit-log").json()) == 2
```

Before running, check the action names the services actually record (`grep -rn 'record(' app/`) and adjust `"audit_run"` / `"case_created"` to the real strings — the assertion is about order and scoping, not the names. If seeded demo events exist in fresh workspaces (`PRIORPATH_DEMO=0` is set by `tests/conftest.py`, so they should not), filter them out by case id.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api_audit_log.py -q`
Expected: FAIL (404 from unknown route / import error for `app.api.audit_log`).

- [ ] **Step 3: Implement**

`app/api/schemas.py` — add:

```python
class AuditEventOut(BaseModel):
    id: int
    case_id: uuid.UUID | None
    actor: str
    action: str
    detail: dict[str, Any]
    ref_versions: list[str]
    at: datetime
```

`app/api/audit_log.py`:

```python
import uuid

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, WorkspaceDep
from app.api.schemas import AuditEventOut
from app.db.models import AuditEvent
from app.services.cases import get_case_or_404

AUDIT_LOG_LIMIT = 200

router = APIRouter()


def _events(session: Session, workspace_id: uuid.UUID, case_id: uuid.UUID | None = None) -> list[AuditEventOut]:
    query = select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
    if case_id is not None:
        query = query.where(AuditEvent.case_id == case_id)
    rows = session.scalars(query.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(AUDIT_LOG_LIMIT))
    return [AuditEventOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/api/audit-log", response_model=list[AuditEventOut])
def workspace_audit_log(ws: WorkspaceDep, session: SessionDep) -> list[AuditEventOut]:
    return _events(session, ws.id)


@router.get("/api/cases/{case_id}/audit-log", response_model=list[AuditEventOut])
def case_audit_log(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep) -> list[AuditEventOut]:
    get_case_or_404(session, ws, case_id)
    return _events(session, ws.id, case_id)
```

`app/main.py` — import `audit_log` alongside the other routers and add `app.include_router(audit_log.router)`.

- [ ] **Step 4: Run the backend CI chain** (Global Constraints). Expected: all green, test count +4.

- [ ] **Step 5: Commit**

```bash
git add app/api/audit_log.py app/api/schemas.py app/main.py tests/test_api_audit_log.py
git commit -m "feat: per-case and workspace audit log API"
```

---

### Task 2: Frontend scaffold, build into Vercel, frontend CI

**Files:**
- Create: `web/` (Vite React-TS template), `web/src/test/setup.ts`, `web/src/App.test.tsx`
- Modify: `vercel.json`, `.vercelignore`, `.gitignore`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `npm run dev | build | lint | test | e2e` scripts in `web/package.json`; `vite build` output in repo-root `public/`; Vitest config with jsdom + Testing Library matchers; `web/src/App.tsx` default export (placeholder shell replaced in Task 3).

- [ ] **Step 1: Scaffold**

```bash
cd ~/Desktop/portfolio/projects/PriorPath
npm create vite@latest web -- --template react-ts
cd web
npm install
npm install tailwindcss @tailwindcss/vite
npm install -D vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom @playwright/test
```

Use whatever current versions npm resolves; the committed `package-lock.json` pins them. Delete the template's demo assets (`src/App.css`, `src/assets/`, `public/vite.svg` inside `web/`) and its counter code.

- [ ] **Step 2: Configure** — `web/vite.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const api = { "/api": "http://127.0.0.1:8000" };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../public", emptyOutDir: true },
  server: { proxy: api },
  preview: { proxy: api },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
```

`web/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
```

`web/src/index.css`: replace contents with `@import "tailwindcss";`.

`web/package.json` scripts (keep the template's `dev`, `lint`, `preview`; set the rest):

```json
"build": "tsc -b && vite build",
"test": "vitest run",
"e2e": "playwright test"
```

Add `"engines": { "node": ">=22" }`. Add `"types": ["vitest/globals"]` only if the template's tsconfig needs it for `src/test/setup.ts` to type-check; otherwise leave tsconfig alone. Set `<title>PriorPath — medical bill auditor</title>` in `web/index.html`.

- [ ] **Step 3: Write the failing smoke test** — `web/src/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import App from "./App";

test("renders the product name", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: /priorpath/i })).toBeInTheDocument();
});
```

Run: `cd web && npm test` — Expected: FAIL (template App has no such heading).

- [ ] **Step 4: Minimal shell** — `web/src/App.tsx`:

```tsx
export default function App() {
  return (
    <main className="mx-auto max-w-6xl p-6">
      <h1 className="text-2xl font-semibold">PriorPath</h1>
    </main>
  );
}
```

Run: `npm test && npm run lint && npm run build`. Expected: PASS, and `../public/index.html` exists.

- [ ] **Step 5: Deployment wiring**

`vercel.json` — add `buildCommand` and keep the function lean:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "buildCommand": "cd web && npm ci && npm run build",
  "functions": {
    "app/main.py": { "includeFiles": "data/**", "excludeFiles": "{web/**,public/**}" }
  },
  "crons": [{ "path": "/api/internal/cleanup", "schedule": "0 5 * * *" }]
}
```

`.vercelignore` — append (the build recreates `public/` remotely; local `node_modules` must not upload):

```
public
web/node_modules
web/test-results
web/playwright-report
```

`.gitignore` — append `public/`, `web/test-results/`, `web/playwright-report/` (`node_modules/` is already ignored).

`.github/workflows/ci.yml` — add a second job:

```yaml
  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm ci
      - run: npm run lint
      - run: npm test
      - run: npm run build
```

- [ ] **Step 6: Run both CI chains**, then commit:

```bash
git add web vercel.json .vercelignore .gitignore .github/workflows/ci.yml
git commit -m "feat: React + Vite + Tailwind web app scaffold built into public/ for Vercel"
```

- [ ] **Step 7 (controller, not implementer): verify the Vercel build path on a preview deploy.** `npx vercel deploy` (preview, not `--prod`), then check `curl -s <preview>/ | grep -i priorpath` returns the HTML shell and `curl -s <preview>/api/health` returns `{"status":"ok"}`. Preview URLs may sit behind Vercel deployment protection; use `npx vercel curl` if plain curl gets a 401. **If the build fails because the Python builder ignores `buildCommand`:** move it to `pyproject.toml` as `[tool.vercel.scripts] build = "cd web && npm ci && npm run build"`, remove it from `vercel.json`, and retry. If `public/` is not served, fall back to FastAPI's `app.frontend("/", directory="public")` (Vercel promotes it to the CDN). Record whichever worked as a ledger ruling.

---

### Task 3: API client, SSE parser, formatting, routing, app shell

**Files:**
- Create: `web/src/lib/api.ts`, `web/src/lib/sse.ts`, `web/src/lib/format.ts`, `web/src/lib/route.ts`, `web/src/lib/api.test.ts`, `web/src/lib/sse.test.ts`, `web/src/lib/format.test.ts`, `web/src/lib/route.test.ts`
- Modify: `web/src/App.tsx`, `web/src/App.test.tsx`

**Interfaces:**
- Consumes: API from Plan 2 + Task 1.
- Produces (exact names used by Tasks 4–7):
  - Types: `Severity`, `FlagStatus`, `ExplanationStatus`, `Line`, `Flag`, `Letter`, `CaseSummary`, `CaseDetail`, `UploadResult`, `AuditEvent`, `PayerType`.
  - `class ApiError extends Error { status: number }`, `errorMessage(res: Response): Promise<string>`, `messageOf(e: unknown): string`.
  - `ensureWorkspace(): Promise<void>` (memoized), `listCases()`, `getCase(id)`, `uploadCases(file: File, payerType: PayerType)`, `auditCase(id)`, `updateFlag(id, status, rejectReason?)`, `draftLetter(caseId)`, `editLetter(id, body)`, `approveLetter(id)`, `exportUrl(id, format: "txt" | "docx"): string`, `resetDemo()`, `auditLog(caseId?: string)`, `streamExplanations(caseId, onEvent: (e: SseEvent) => void): Promise<void>`.
  - `createSseParser(onEvent): (chunk: string) => void`, `type SseEvent = { event: string; data: unknown }`.
  - `money(v: string | number): string`, `STATUS_LABEL: Record<string, string>`, `statusLabel(s: string): string`, `SEVERITY_GROUPS: { severity: Severity; title: string }[]`, `humanizeAction(a: string): string`.
  - `type Route = { name: "queue" } | { name: "case"; id: string } | { name: "log" }`, `parseRoute(search: string): Route`, `routeHref(r: Route): string`, `useRoute(): [Route, (r: Route) => void]`.

- [ ] **Step 1: Write failing tests**

`web/src/lib/sse.test.ts`:

```ts
import { expect, test } from "vitest";
import { createSseParser, type SseEvent } from "./sse";

function collect(chunks: string[]): SseEvent[] {
  const out: SseEvent[] = [];
  const feed = createSseParser((e) => out.push(e));
  chunks.forEach(feed);
  return out;
}

test("parses events split across chunks", () => {
  expect(collect(['event: start\ndata: {"pend', 'ing": 2}\n', "\nevent: done\ndata: {}\n\n"])).toEqual([
    { event: "start", data: { pending: 2 } },
    { event: "done", data: {} },
  ]);
});

test("handles CRLF framing split between chunks", () => {
  expect(collect(['event: done\r\ndata: {"ready": 1}\r', "\n\r\n"])).toEqual([{ event: "done", data: { ready: 1 } }]);
});

test("skips malformed data and comment blocks", () => {
  expect(collect([": keepalive\n\n", "event: x\ndata: {not json\n\n", "event: ok\ndata: 1\n\n"])).toEqual([
    { event: "ok", data: 1 },
  ]);
});
```

`web/src/lib/api.test.ts`:

```ts
import { beforeEach, expect, test, vi } from "vitest";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => vi.resetModules());

test("ensureWorkspace sends one request even when called twice at once", async () => {
  const fetchMock = vi.fn(async () => json(200, { id: "w", created_at: "2026-10-02T00:00:00Z" }));
  vi.stubGlobal("fetch", fetchMock);
  const { ensureWorkspace } = await import("./api");
  await Promise.all([ensureWorkspace(), ensureWorkspace()]);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test("ensureWorkspace retries after a failure", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(json(503, { detail: "the demo is full right now; please try again later" }))
    .mockResolvedValueOnce(json(200, { id: "w", created_at: "x" }));
  vi.stubGlobal("fetch", fetchMock);
  const { ensureWorkspace } = await import("./api");
  await expect(ensureWorkspace()).rejects.toThrow("the demo is full");
  await ensureWorkspace();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("errorMessage reads string detail, validation lists and upload errors", async () => {
  const { errorMessage } = await import("./api");
  expect(await errorMessage(json(409, { detail: "approve the letter before exporting it" }))).toBe(
    "approve the letter before exporting it",
  );
  expect(await errorMessage(json(422, { detail: [{ msg: "field required" }, { msg: "too long" }] }))).toBe(
    "field required; too long",
  );
  expect(await errorMessage(json(422, { cases: [], errors: [{ path: "$.entry[0]", message: "bad code" }] }))).toBe(
    "$.entry[0]: bad code",
  );
  expect(await errorMessage(new Response("<html>", { status: 502 }))).toBe("Request failed (502)");
});

test("updateFlag sends status and reason", async () => {
  const fetchMock = vi.fn(async () => json(200, {}));
  vi.stubGlobal("fetch", fetchMock);
  const { updateFlag } = await import("./api");
  await updateFlag("f1", "rejected", "documented separately");
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/flags/f1");
  expect(init.method).toBe("PATCH");
  expect(JSON.parse(init.body as string)).toEqual({ status: "rejected", reject_reason: "documented separately" });
});

test("streamExplanations feeds events and throws on HTTP errors", async () => {
  const body = 'event: start\ndata: {"pending": 1}\n\nevent: done\ndata: {"ready": 1, "unavailable": 0, "remaining": 0}\n\n';
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
  const { streamExplanations } = await import("./api");
  const seen: string[] = [];
  await streamExplanations("c1", (e) => seen.push(e.event));
  expect(seen).toEqual(["start", "done"]);

  vi.stubGlobal("fetch", vi.fn(async () => json(404, { detail: "case not found" })));
  await expect(streamExplanations("nope", () => {})).rejects.toThrow("case not found");
});
```

`web/src/lib/format.test.ts`:

```ts
import { expect, test } from "vitest";
import { humanizeAction, money, statusLabel } from "./format";

test("formats API money strings as USD", () => {
  expect(money("1234.5")).toBe("$1,234.50");
  expect(money("0.00")).toBe("$0.00");
});

test("labels statuses and actions", () => {
  expect(statusLabel("needs_review")).toBe("Needs review");
  expect(statusLabel("something_new")).toBe("Something new");
  expect(humanizeAction("letter_drafted")).toBe("Letter drafted");
});
```

`web/src/lib/route.test.ts`:

```ts
import { expect, test } from "vitest";
import { parseRoute, routeHref } from "./route";

test("round-trips routes through the query string", () => {
  for (const r of [{ name: "queue" }, { name: "case", id: "a b" }, { name: "log" }] as const) {
    expect(parseRoute(new URL(routeHref(r), "http://x").search)).toEqual(r);
  }
  expect(parseRoute("?unknown=1")).toEqual({ name: "queue" });
});
```

Run: `npm test` — Expected: FAIL (modules missing).

- [ ] **Step 2: Implement `web/src/lib/sse.ts`**

```ts
export type SseEvent = { event: string; data: unknown };

/** Returns a feed function; call it with each decoded text chunk. Complete events go to onEvent. */
export function createSseParser(onEvent: (e: SseEvent) => void): (chunk: string) => void {
  let buffer = "";
  return (chunk) => {
    buffer = (buffer + chunk).replace(/\r\n/g, "\n");
    let end: number;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length === 0) continue;
      try {
        onEvent({ event, data: JSON.parse(data.join("\n")) });
      } catch {
        // malformed data line: skip the event, keep the stream going
      }
    }
  };
}
```

- [ ] **Step 3: Implement `web/src/lib/api.ts`**

```ts
import { createSseParser, type SseEvent } from "./sse";

export type Severity = "error" | "outlier" | "lead" | "notice";
export type FlagStatus = "open" | "accepted" | "rejected";
export type ExplanationStatus = "pending" | "ready" | "unavailable" | "none";
export type PayerType = "medicare" | "commercial" | "unknown";

export interface Line {
  id: string;
  code: string;
  modifiers: string[];
  units: number;
  charge: string;
  date_of_service: string;
  place_of_service: string | null;
}

export interface Flag {
  id: string;
  rule_id: string;
  severity: Severity;
  line_ids: string[];
  evidence: Record<string, unknown>;
  est_overcharge: string;
  message: string;
  explanation: string | null;
  explanation_status: ExplanationStatus;
  status: FlagStatus;
  reject_reason: string | null;
}

export interface Letter {
  id: string;
  status: "draft" | "approved";
  body: string;
  flag_ids: string[];
  created_at: string;
  approved_at: string | null;
}

export interface CaseSummary {
  id: string;
  claim_id: string;
  provider: string | null;
  payer: string | null;
  payer_type: string;
  source: string;
  status: string;
  line_count: number;
  error_count: number;
  est_overcharge: string;
  outlier_amount: string;
  created_at: string;
}

export interface CaseDetail extends CaseSummary {
  lines: Line[];
  flags: Flag[];
  letter: Letter | null;
}

export interface UploadResult {
  cases: CaseSummary[];
  errors: { path: string; message: string }[];
}

export interface AuditEvent {
  id: number;
  case_id: string | null;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  ref_versions: string[];
  at: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join("; ");
    if (Array.isArray(body?.errors) && body.errors.length > 0)
      return body.errors.map((e: { path: string; message: string }) => `${e.path}: ${e.message}`).join("; ");
  } catch {
    // not JSON
  }
  return `Request failed (${res.status})`;
}

export function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

let workspace: Promise<void> | undefined;

/** Creates (or loads) this browser's workspace. Must resolve before any other call. */
export function ensureWorkspace(): Promise<void> {
  workspace ??= request("/api/workspace").then(
    () => undefined,
    (e) => {
      workspace = undefined;
      throw e;
    },
  );
  return workspace;
}

export const listCases = () => request<CaseSummary[]>("/api/cases");
export const getCase = (id: string) => request<CaseDetail>(`/api/cases/${id}`);
export const auditCase = (id: string) => request<CaseDetail>(`/api/cases/${id}/audit`, { method: "POST" });
export const resetDemo = () => request<{ cases: number }>("/api/demo/reset", { method: "POST" });
export const draftLetter = (caseId: string) => request<Letter>(`/api/cases/${caseId}/letter`, { method: "POST" });
export const editLetter = (id: string, body: string) => request<Letter>(`/api/letters/${id}`, jsonInit("PATCH", { body }));
export const approveLetter = (id: string) => request<Letter>(`/api/letters/${id}/approve`, { method: "POST" });
export const exportUrl = (id: string, format: "txt" | "docx") => `/api/letters/${id}/export?format=${format}`;
export const auditLog = (caseId?: string) =>
  request<AuditEvent[]>(caseId ? `/api/cases/${caseId}/audit-log` : "/api/audit-log");

export const updateFlag = (id: string, status: FlagStatus, rejectReason?: string) =>
  request<Flag>(`/api/flags/${id}`, jsonInit("PATCH", { status, reject_reason: rejectReason ?? null }));

export async function uploadCases(file: File, payerType: PayerType): Promise<UploadResult> {
  return request<UploadResult>(`/api/cases?payer_type=${payerType}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await file.text(),
  });
}

export async function streamExplanations(caseId: string, onEvent: (e: SseEvent) => void): Promise<void> {
  const res = await fetch(`/api/cases/${caseId}/explain`, { method: "POST", credentials: "same-origin" });
  if (!res.ok || !res.body) throw new ApiError(res.status, await errorMessage(res));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const feed = createSseParser(onEvent);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    feed(decoder.decode(value, { stream: true }));
  }
  feed(decoder.decode());
}
```

Before finishing, confirm the money assumption against a live local server: `curl -s localhost:8000/api/cases | head -c 400` must show `"est_overcharge":"..."` as a string. If it is a number, keep the `string | number` input of `money()` and change the `Line`/`Flag`/`CaseSummary` money fields to `string | number`.

- [ ] **Step 4: Implement `web/src/lib/format.ts`**

```ts
import type { Severity } from "./api";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export const money = (v: string | number) => usd.format(Number(v));

export const STATUS_LABEL: Record<string, string> = {
  uploaded: "Uploaded",
  needs_review: "Needs review",
  letter_ready: "Letter drafted",
  approved: "Letter approved",
  exported: "Exported",
};

const sentence = (s: string) => {
  const words = s.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

export const statusLabel = (s: string) => STATUS_LABEL[s] ?? sentence(s);
export const humanizeAction = sentence;

export const SEVERITY_GROUPS: { severity: Severity; title: string }[] = [
  { severity: "error", title: "Billing errors" },
  { severity: "outlier", title: "Price outliers" },
  { severity: "lead", title: "Leads (worth checking, not errors)" },
  { severity: "notice", title: "Notices" },
];
```

- [ ] **Step 5: Implement `web/src/lib/route.ts`**

```ts
import { useCallback, useEffect, useState } from "react";

export type Route = { name: "queue" } | { name: "case"; id: string } | { name: "log" };

export function parseRoute(search: string): Route {
  const params = new URLSearchParams(search);
  const id = params.get("case");
  if (id) return { name: "case", id };
  if (params.get("view") === "log") return { name: "log" };
  return { name: "queue" };
}

export function routeHref(r: Route): string {
  if (r.name === "case") return `/?case=${encodeURIComponent(r.id)}`;
  if (r.name === "log") return "/?view=log";
  return "/";
}

export function useRoute(): [Route, (r: Route) => void] {
  const [route, setRoute] = useState(() => parseRoute(window.location.search));
  useEffect(() => {
    const onPop = () => setRoute(parseRoute(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((r: Route) => {
    window.history.pushState(null, "", routeHref(r));
    setRoute(r);
  }, []);
  return [route, navigate];
}
```

- [ ] **Step 6: App shell** — replace `web/src/App.tsx`. Components from Tasks 4–7 don't exist yet, so this task renders placeholders for each screen (`<p>Case queue</p>` etc.); later tasks swap in the real component.

```tsx
import { useEffect, useState } from "react";
import { ensureWorkspace, messageOf } from "./lib/api";
import { routeHref, useRoute, type Route } from "./lib/route";

export default function App() {
  const [route, navigate] = useRoute();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ensureWorkspace().then(
      () => setReady(true),
      (e) => setError(messageOf(e)),
    );
  }, []);

  const link = (r: Route, label: string) => (
    <a
      href={routeHref(r)}
      onClick={(e) => {
        e.preventDefault();
        navigate(r);
      }}
      className={route.name === r.name ? "font-semibold underline" : "hover:underline"}
    >
      {label}
    </a>
  );

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-baseline gap-x-6 gap-y-1 px-4 py-3">
          <h1 className="text-xl font-semibold">PriorPath</h1>
          <span className="text-sm text-slate-500">Medical bill auditor · synthetic demo data · nothing is sent anywhere</span>
          <nav className="ml-auto flex gap-4 text-sm">
            {link({ name: "queue" }, "Cases")}
            {link({ name: "log" }, "Audit log")}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        {error ? (
          <p role="alert" className="text-red-700">Couldn't start the demo: {error}</p>
        ) : !ready ? (
          <p>Loading…</p>
        ) : route.name === "case" ? (
          <p>Case {route.id}</p>
        ) : route.name === "log" ? (
          <p>Audit log</p>
        ) : (
          <p>Case queue</p>
        )}
      </main>
    </div>
  );
}
```

Update `web/src/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

test("waits for the workspace, then shows the queue", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ id: "w", created_at: "x" }), { status: 200 })));
  const { default: App } = await import("./App");
  render(<App />);
  expect(screen.getByRole("heading", { name: "PriorPath" })).toBeInTheDocument();
  expect(await screen.findByText("Case queue")).toBeInTheDocument();
});

test("shows a readable error when the workspace can't be created", async () => {
  vi.resetModules();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "the demo is full right now" }), { status: 503 })));
  const { default: App } = await import("./App");
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("the demo is full right now");
});
```

- [ ] **Step 7: Run frontend chain** — `npm run lint && npm test && npm run build`. Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add web
git commit -m "feat(web): typed API client, SSE parser, query-string routing and app shell"
```

---

### Task 4: Case queue

**Files:**
- Create: `web/src/components/CaseQueue.tsx`, `web/src/components/CaseQueue.test.tsx`
- Modify: `web/src/App.tsx` (render `<CaseQueue onOpen={(id) => navigate({ name: "case", id })} />` for the queue route)

**Interfaces:**
- Consumes: `listCases`, `uploadCases`, `resetDemo`, `messageOf`, `CaseSummary`, `PayerType` (api.ts); `money`, `statusLabel`, `STATUS_LABEL` (format.ts); `routeHref` (route.ts).
- Produces: `export default function CaseQueue({ onOpen }: { onOpen: (id: string) => void })`.

- [ ] **Step 1: Write failing tests** — `web/src/components/CaseQueue.test.tsx`

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import CaseQueue from "./CaseQueue";
import type { CaseSummary } from "../lib/api";

const summary = (over: Partial<CaseSummary>): CaseSummary => ({
  id: "c1", claim_id: "C0001", provider: "Clinic A", payer: "Medicare", payer_type: "medicare", source: "fhir",
  status: "needs_review", line_count: 4, error_count: 1, est_overcharge: "30.00", outlier_amount: "0.00",
  created_at: "2026-10-02T10:00:00Z", ...over,
});

const ok = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stubFetch(routes: Record<string, () => Response>) {
  const fetchMock = vi.fn(async (url: string) => {
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    if (!key) throw new Error(`unexpected fetch ${url}`);
    return routes[key]();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const cases = [
  summary({ id: "a", claim_id: "C0001", est_overcharge: "30.00" }),
  summary({ id: "b", claim_id: "C0002", est_overcharge: "900.00", status: "exported" }),
];

test("lists cases and sorts by overcharge", async () => {
  stubFetch({ "/api/cases": () => ok(cases) });
  render(<CaseQueue onOpen={() => {}} />);
  await screen.findByText("C0001");
  await userEvent.selectOptions(screen.getByLabelText("Sort"), "overcharge");
  const rows = screen.getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("C0002")).toBeInTheDocument();
  expect(within(rows[0]).getByText("$900.00")).toBeInTheDocument();
});

test("filters by status", async () => {
  stubFetch({ "/api/cases": () => ok(cases) });
  render(<CaseQueue onOpen={() => {}} />);
  await screen.findByText("C0001");
  await userEvent.selectOptions(screen.getByLabelText("Status"), "exported");
  expect(screen.queryByText("C0001")).not.toBeInTheDocument();
  expect(screen.getByText("C0002")).toBeInTheDocument();
});

test("opens a case", async () => {
  stubFetch({ "/api/cases": () => ok(cases) });
  const onOpen = vi.fn();
  render(<CaseQueue onOpen={onOpen} />);
  await userEvent.click(await screen.findByRole("link", { name: "C0001" }));
  expect(onOpen).toHaveBeenCalledWith("a");
});

test("upload shows created cases and per-path errors", async () => {
  const fetchMock = stubFetch({
    "/api/cases?payer_type": () => ok({ cases: [summary({ id: "n", claim_id: "NEW-1" })], errors: [{ path: "$.entry[1]", message: "unknown code" }] }, 201),
    "/api/cases": () => ok(cases),
  });
  render(<CaseQueue onOpen={() => {}} />);
  await screen.findByText("C0001");
  const file = new File(['{"resourceType": "Bundle"}'], "claims.json", { type: "application/json" });
  await userEvent.upload(screen.getByLabelText("FHIR bundle (JSON, up to 4 MB)"), file);
  await userEvent.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByText(/1 case added/)).toBeInTheDocument();
  expect(screen.getByText("$.entry[1]: unknown code")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([u]) => String(u) === "/api/cases?payer_type=medicare")).toBe(true);
});

test("rejects files over 4 MB without calling the API", async () => {
  const fetchMock = stubFetch({ "/api/cases": () => ok(cases) });
  render(<CaseQueue onOpen={() => {}} />);
  await screen.findByText("C0001");
  const big = new File(["x".repeat(4 * 1024 * 1024 + 1)], "big.json", { type: "application/json" });
  await userEvent.upload(screen.getByLabelText("FHIR bundle (JSON, up to 4 MB)"), big);
  await userEvent.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("larger than 4 MB");
  expect(fetchMock).toHaveBeenCalledTimes(1); // only the initial list
});

test("shows API errors from upload", async () => {
  stubFetch({
    "/api/cases?payer_type": () => ok({ cases: [], errors: [{ path: "$", message: "body is not valid JSON" }] }, 422),
    "/api/cases": () => ok(cases),
  });
  render(<CaseQueue onOpen={() => {}} />);
  await screen.findByText("C0001");
  await userEvent.upload(screen.getByLabelText("FHIR bundle (JSON, up to 4 MB)"), new File(["nope"], "x.json"));
  await userEvent.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("$: body is not valid JSON");
});
```

Run: `npm test` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement `web/src/components/CaseQueue.tsx`**

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { listCases, messageOf, resetDemo, uploadCases, type CaseSummary, type PayerType, type UploadResult } from "../lib/api";
import { money, STATUS_LABEL, statusLabel } from "../lib/format";
import { routeHref } from "../lib/route";

const MAX_UPLOAD = 4 * 1024 * 1024;

export default function CaseQueue({ onOpen }: { onOpen: (id: string) => void }) {
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState<"newest" | "overcharge">("newest");
  const [file, setFile] = useState<File | null>(null);
  const [payerType, setPayerType] = useState<PayerType>("medicare");
  const [busy, setBusy] = useState(false);
  const [uploaded, setUploaded] = useState<UploadResult | null>(null);

  const load = useCallback(() => {
    listCases().then(setCases, (e) => setError(messageOf(e)));
  }, []);
  useEffect(load, [load]);

  const shown = useMemo(() => {
    const rows = (cases ?? []).filter((c) => status === "all" || c.status === status);
    return sort === "overcharge"
      ? [...rows].sort((a, b) => Number(b.est_overcharge) - Number(a.est_overcharge))
      : rows;
  }, [cases, status, sort]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  const upload = () =>
    run(async () => {
      setUploaded(null);
      if (!file) throw new Error("Choose a FHIR JSON file first.");
      if (file.size > MAX_UPLOAD) throw new Error("That file is larger than 4 MB; split the bundle and try again.");
      setUploaded(await uploadCases(file, payerType));
      load();
    });

  const reset = () =>
    run(async () => {
      await resetDemo();
      setUploaded(null);
      load();
    });

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-end gap-4 rounded border bg-white p-4">
        <label className="flex flex-col text-sm">
          FHIR bundle (JSON, up to 4 MB)
          <input type="file" accept=".json,application/json" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label className="flex flex-col text-sm">
          Payer type
          <select value={payerType} onChange={(e) => setPayerType(e.target.value as PayerType)} className="rounded border px-2 py-1">
            <option value="medicare">Medicare</option>
            <option value="commercial">Commercial</option>
            <option value="unknown">Unknown</option>
          </select>
        </label>
        <button type="button" onClick={upload} disabled={busy} className="rounded bg-slate-900 px-3 py-1.5 text-white disabled:opacity-50">
          Upload
        </button>
        <button type="button" onClick={reset} disabled={busy} className="ml-auto rounded border px-3 py-1.5 disabled:opacity-50">
          Reset demo data
        </button>
      </div>

      {error && <p role="alert" className="text-red-700">{error}</p>}
      {uploaded && (
        <div className="rounded border border-emerald-300 bg-emerald-50 p-3 text-sm" role="status">
          <p>{uploaded.cases.length} case{uploaded.cases.length === 1 ? "" : "s"} added.</p>
          {uploaded.errors.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-amber-800">
              {uploaded.errors.map((e) => (
                <li key={`${e.path}:${e.message}`}>{`${e.path}: ${e.message}`}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex gap-4 text-sm">
        <label>
          Status{" "}
          <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border px-2 py-1">
            <option value="all">All</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Sort{" "}
          <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as "newest" | "overcharge")} className="rounded border px-2 py-1">
            <option value="newest">Newest first</option>
            <option value="overcharge">Highest est. overcharge</option>
          </select>
        </label>
      </div>

      {cases === null && !error ? (
        <p>Loading cases…</p>
      ) : (
        <div className="overflow-x-auto rounded border bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-100">
              <tr>
                <th className="p-2">Claim</th>
                <th className="p-2">Provider</th>
                <th className="p-2 text-right">Lines</th>
                <th className="p-2 text-right">Billing errors</th>
                <th className="p-2 text-right">Est. overcharge</th>
                <th className="p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((c) => (
                <tr key={c.id} className="border-t">
                  <td className="p-2">
                    <a
                      href={routeHref({ name: "case", id: c.id })}
                      onClick={(e) => {
                        e.preventDefault();
                        onOpen(c.id);
                      }}
                      className="font-mono text-blue-700 hover:underline"
                    >
                      {c.claim_id}
                    </a>
                  </td>
                  <td className="p-2">{c.provider ?? "—"}</td>
                  <td className="p-2 text-right">{c.line_count}</td>
                  <td className="p-2 text-right">{c.error_count}</td>
                  <td className="p-2 text-right">{money(c.est_overcharge)}</td>
                  <td className="p-2">{statusLabel(c.status)}</td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-4 text-center text-slate-500">No cases match.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
```

Wire it into `App.tsx` in place of the `<p>Case queue</p>` placeholder, and update the first `App.test.tsx` test to stub `/api/cases` (return `[]`) and assert the "No cases match." text instead of "Case queue".

- [ ] **Step 3: Run frontend chain.** Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web
git commit -m "feat(web): case queue with status filter, overcharge sort, FHIR upload and demo reset"
```

---

### Task 5: Case detail — lines, flags, review, explanations

**Files:**
- Create: `web/src/components/CaseDetail.tsx`, `web/src/components/FlagCard.tsx`, `web/src/components/CaseDetail.test.tsx`
- Modify: `web/src/App.tsx` (render `<CaseDetail id={route.id} onBack={() => navigate({ name: "queue" })} />`)

**Interfaces:**
- Consumes: `getCase`, `auditCase`, `updateFlag`, `streamExplanations`, `ApiError`, `messageOf`, types (api.ts); `money`, `statusLabel`, `SEVERITY_GROUPS` (format.ts).
- Produces: `export default function CaseDetail({ id, onBack }: { id: string; onBack: () => void })`; `export default function FlagCard({ flag, locked, onChange }: { flag: Flag; locked: boolean; onChange: (f: Flag) => void })`. `CaseDetail` renders a `{children}`-free slot for the letter panel: Task 6 adds `<LetterPanel caseDetail={detail} onChange={reload} />` below the flags, and Task 7 adds `<AuditLog caseId={id} refreshKey={...} />`. Keep the `reload()` function and the `detail` state names exactly so those tasks can wire in.

- [ ] **Step 1: Write failing tests** — `web/src/components/CaseDetail.test.tsx`

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import CaseDetail from "./CaseDetail";
import type { CaseDetail as Detail, Flag } from "../lib/api";

const flag = (over: Partial<Flag>): Flag => ({
  id: "f1", rule_id: "R1", severity: "error", line_ids: ["L2"], evidence: { table: "claim", ref_version: "NCCI-2026Q4" },
  est_overcharge: "30.00", message: "Duplicate of line L1", explanation: "Billed twice on the same day.",
  explanation_status: "ready", status: "open", reject_reason: null, ...over,
});

const detail = (over: Partial<Detail> = {}): Detail => ({
  id: "c1", claim_id: "C0001", provider: "Clinic A", payer: "Medicare", payer_type: "medicare", source: "fhir",
  status: "needs_review", line_count: 2, error_count: 1, est_overcharge: "30.00", outlier_amount: "120.00",
  created_at: "2026-10-02T10:00:00Z",
  lines: [
    { id: "L1", code: "96372", modifiers: [], units: 1, charge: "30.00", date_of_service: "2026-11-03", place_of_service: "11" },
    { id: "L2", code: "96372", modifiers: [], units: 1, charge: "30.00", date_of_service: "2026-11-03", place_of_service: "11" },
  ],
  flags: [
    flag({}),
    flag({ id: "f2", rule_id: "R5", severity: "outlier", line_ids: ["L1"], message: "Charge is 4x the Medicare rate", explanation: null, explanation_status: "pending", est_overcharge: "120.00" }),
    flag({ id: "f3", rule_id: "R4", severity: "lead", line_ids: [], message: "Code not valid for Medicare", explanation_status: "none", explanation: null, est_overcharge: "0.00" }),
  ],
  letter: null,
  ...over,
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("shows totals, groups flags separately and badges flagged lines", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(detail())));
  render(<CaseDetail id="c1" onBack={() => {}} />);
  expect(await screen.findByRole("heading", { name: /C0001/ })).toBeInTheDocument();
  expect(screen.getByText("$30.00", { selector: "[data-total=errors]" })).toBeInTheDocument();
  expect(screen.getByText("$120.00", { selector: "[data-total=outliers]" })).toBeInTheDocument();
  const errors = screen.getByRole("region", { name: "Billing errors" });
  expect(within(errors).getByText("Duplicate of line L1")).toBeInTheDocument();
  const outliers = screen.getByRole("region", { name: "Price outliers" });
  expect(within(outliers).getByText(/4x the Medicare rate/)).toBeInTheDocument();
  expect(within(screen.getByRole("row", { name: /L2/ })).getByText("R1")).toBeInTheDocument();
});

test("rejecting requires a reason and shows API errors", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/flags/f1") {
      const body = JSON.parse(String(init?.body));
      return json({ ...flag({}), status: body.status, reject_reason: body.reject_reason });
    }
    return json(detail());
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<CaseDetail id="c1" onBack={() => {}} />);
  const card = within(await screen.findByRole("article", { name: /R1/ }));
  await userEvent.click(card.getByRole("button", { name: "Reject" }));
  expect(card.getByRole("button", { name: "Confirm reject" })).toBeDisabled();
  await userEvent.type(card.getByLabelText("Reason"), "separate visits");
  await userEvent.click(card.getByRole("button", { name: "Confirm reject" }));
  expect(await card.findByText(/Rejected: separate visits/)).toBeInTheDocument();
});

test("accept shows server errors next to the flag", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) =>
    url === "/api/flags/f1" ? json({ detail: "flag not found" }, 404) : json(detail())));
  render(<CaseDetail id="c1" onBack={() => {}} />);
  const card = within(await screen.findByRole("article", { name: /R1/ }));
  await userEvent.click(card.getByRole("button", { name: "Accept" }));
  expect(await card.findByRole("alert")).toHaveTextContent("flag not found");
});

test("streams explanations, shows progress result and reloads", async () => {
  const sse = [
    'event: start\ndata: {"pending": 1}\n\n',
    'event: explanation\ndata: {"index": 1, "total": 1, "flag_id": "f2", "status": "ready", "explanation": "Price is high.", "reason": null}\n\n',
    'event: error\ndata: {"flag_id": "f9", "reason": "internal error"}\n\n',
    'event: done\ndata: {"ready": 1, "unavailable": 0, "remaining": 0}\n\n',
  ].join("");
  let gets = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/cases/c1/explain") return new Response(sse, { status: 200 });
    if (url === "/api/cases/c1") gets += 1;
    return json(detail());
  }));
  render(<CaseDetail id="c1" onBack={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Generate explanations" }));
  expect(await screen.findByText(/1 ready, 0 unavailable, 1 failed/)).toBeInTheDocument();
  expect(gets).toBe(2); // initial load + reload after done
});

test("a stream that ends without done is reported", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) =>
    url.endsWith("/explain") ? new Response('event: start\ndata: {"pending": 1}\n\n', { status: 200 }) : json(detail())));
  render(<CaseDetail id="c1" onBack={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Generate explanations" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/stopped early/);
});

test("unknown case shows not found with a way back", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "case not found" }, 404)));
  const onBack = vi.fn();
  render(<CaseDetail id="nope" onBack={onBack} />);
  expect(await screen.findByText("Case not found.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Back to cases" }));
  expect(onBack).toHaveBeenCalled();
});
```

Run: `npm test` — Expected: FAIL.

- [ ] **Step 2: Implement `web/src/components/FlagCard.tsx`**

```tsx
import { useState } from "react";
import { messageOf, updateFlag, type Flag, type FlagStatus } from "../lib/api";
import { money } from "../lib/format";

const EXPLANATION_TEXT: Record<string, string> = {
  pending: "Explanation not generated yet.",
  unavailable: "Explanation unavailable right now — try Generate explanations again.",
  none: "",
};

export default function FlagCard({ flag, locked, onChange }: { flag: Flag; locked: boolean; onChange: (f: Flag) => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reviewable = flag.severity !== "notice";

  async function save(status: FlagStatus, rejectReason?: string) {
    setBusy(true);
    setError(null);
    try {
      onChange(await updateFlag(flag.id, status, rejectReason));
      setRejecting(false);
      setReason("");
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article aria-label={`${flag.rule_id}: ${flag.message}`} className="space-y-2 rounded border bg-white p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="rounded bg-slate-200 px-1.5 font-mono text-xs">{flag.rule_id}</span>
        <p className="font-medium">{flag.message}</p>
        {Number(flag.est_overcharge) > 0 && (
          <span className="ml-auto text-sm">Est. {flag.severity === "outlier" ? "excess" : "overcharge"}: {money(flag.est_overcharge)}</span>
        )}
      </div>
      {flag.line_ids.length > 0 && <p className="text-xs text-slate-500">Lines: {flag.line_ids.join(", ")}</p>}
      <p className="text-sm">{flag.explanation ?? EXPLANATION_TEXT[flag.explanation_status] ?? ""}</p>
      <details className="text-xs">
        <summary className="cursor-pointer text-slate-600">Evidence</summary>
        <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3">
          {Object.entries(flag.evidence).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-mono text-slate-500">{k}</dt>
              <dd className="font-mono break-all">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
            </div>
          ))}
        </dl>
      </details>
      {reviewable && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {flag.status === "open" ? (
            <>
              <button type="button" disabled={locked || busy} onClick={() => save("accepted")} className="rounded bg-emerald-700 px-2 py-1 text-white disabled:opacity-50">
                Accept
              </button>
              <button type="button" disabled={locked || busy} onClick={() => setRejecting(true)} className="rounded border px-2 py-1 disabled:opacity-50">
                Reject
              </button>
            </>
          ) : (
            <>
              <span className={flag.status === "accepted" ? "text-emerald-700" : "text-slate-600"}>
                {flag.status === "accepted" ? "Accepted" : `Rejected: ${flag.reject_reason ?? ""}`}
              </span>
              <button type="button" disabled={locked || busy} onClick={() => save("open")} className="rounded border px-2 py-1 disabled:opacity-50">
                Undo
              </button>
            </>
          )}
        </div>
      )}
      {rejecting && (
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <label className="flex grow flex-col">
            Reason
            <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} className="rounded border px-2 py-1" />
          </label>
          <button type="button" disabled={busy || !reason.trim()} onClick={() => save("rejected", reason.trim())} className="rounded bg-slate-900 px-2 py-1 text-white disabled:opacity-50">
            Confirm reject
          </button>
          <button type="button" onClick={() => setRejecting(false)} className="rounded border px-2 py-1">
            Cancel
          </button>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </article>
  );
}
```

- [ ] **Step 3: Implement `web/src/components/CaseDetail.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { ApiError, auditCase, getCase, messageOf, streamExplanations, type CaseDetail as Detail, type Flag } from "../lib/api";
import { money, SEVERITY_GROUPS, statusLabel } from "../lib/format";
import FlagCard from "./FlagCard";

const EXPLAINABLE = new Set(["pending", "unavailable"]);

export default function CaseDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setDetail(await getCase(id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNotFound(true);
      else setError(messageOf(e));
    }
  }, [id]);
  useEffect(() => {
    void reload();
  }, [reload]);

  if (notFound)
    return (
      <div className="space-y-2">
        <p>Case not found.</p>
        <button type="button" onClick={onBack} className="rounded border px-3 py-1.5">Back to cases</button>
      </div>
    );
  if (!detail) return error ? <p role="alert" className="text-red-700">{error}</p> : <p>Loading case…</p>;

  const locked = detail.letter?.status === "approved";
  const replaceFlag = (f: Flag) => setDetail((d) => d && { ...d, flags: d.flags.map((x) => (x.id === f.id ? f : x)) });
  const flagsByLine = new Map<string, string[]>();
  for (const f of detail.flags) for (const l of f.line_ids) flagsByLine.set(l, [...(flagsByLine.get(l) ?? []), f.rule_id]);
  const canExplain = detail.flags.some((f) => EXPLAINABLE.has(f.explanation_status));

  async function runAudit() {
    setBusy(true);
    setError(null);
    try {
      setDetail(await auditCase(id));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  async function explain() {
    setBusy(true);
    setError(null);
    setProgress("Starting…");
    let failed = 0;
    let finished = false;
    try {
      await streamExplanations(id, ({ event, data }) => {
        const d = data as Record<string, unknown>;
        if (event === "start") setProgress(`Explaining 0/${d.pending}…`);
        if (event === "explanation") {
          setProgress(`Explaining ${d.index}/${d.total}…`);
          setDetail((cur) => cur && {
            ...cur,
            flags: cur.flags.map((f) =>
              f.id === d.flag_id ? { ...f, explanation: (d.explanation as string | null) ?? null, explanation_status: d.status as Flag["explanation_status"] } : f,
            ),
          });
        }
        if (event === "error") failed += 1;
        if (event === "done") {
          finished = true;
          const more = Number(d.remaining) > 0 ? ` ${d.remaining} left — run again to finish.` : "";
          setProgress(`Done: ${d.ready} ready, ${d.unavailable} unavailable, ${failed} failed.${more}`);
        }
      });
      if (!finished) {
        setProgress(null);
        setError("The explanation stream stopped early. Try again.");
      }
    } catch (e) {
      setProgress(null);
      setError(messageOf(e));
    } finally {
      setBusy(false);
      await reload();
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-4">
        <h2 className="text-xl font-semibold">Claim <span className="font-mono">{detail.claim_id}</span></h2>
        <span className="text-sm text-slate-600">{detail.provider ?? "Unknown provider"} · {detail.payer ?? "Unknown payer"} ({detail.payer_type}) · {statusLabel(detail.status)}</span>
      </div>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded border bg-white p-3"><dt className="text-xs text-slate-500">Billing errors</dt><dd className="text-lg">{detail.error_count}</dd></div>
        <div className="rounded border bg-white p-3"><dt className="text-xs text-slate-500">Est. overcharge (errors)</dt><dd className="text-lg" data-total="errors">{money(detail.est_overcharge)}</dd></div>
        <div className="rounded border bg-white p-3"><dt className="text-xs text-slate-500">Above benchmark (outliers)</dt><dd className="text-lg" data-total="outliers">{money(detail.outlier_amount)}</dd></div>
      </dl>

      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy || locked} onClick={runAudit} className="rounded border px-3 py-1.5 disabled:opacity-50">Run audit</button>
        {canExplain && (
          <button type="button" disabled={busy} onClick={explain} className="rounded bg-slate-900 px-3 py-1.5 text-white disabled:opacity-50">Generate explanations</button>
        )}
        {progress && <span role="status" className="self-center text-sm text-slate-600">{progress}</span>}
      </div>
      {error && <p role="alert" className="text-red-700">{error}</p>}

      <div className="overflow-x-auto rounded border bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-100">
            <tr>
              <th className="p-2">Line</th><th className="p-2">Code</th><th className="p-2">Modifiers</th>
              <th className="p-2 text-right">Units</th><th className="p-2 text-right">Charge</th><th className="p-2">Date</th><th className="p-2">Flags</th>
            </tr>
          </thead>
          <tbody>
            {detail.lines.map((l) => (
              <tr key={l.id} className="border-t">
                <td className="p-2 font-mono">{l.id}</td>
                <td className="p-2 font-mono">{l.code}</td>
                <td className="p-2 font-mono">{l.modifiers.join(", ") || "—"}</td>
                <td className="p-2 text-right">{l.units}</td>
                <td className="p-2 text-right">{money(l.charge)}</td>
                <td className="p-2">{l.date_of_service}</td>
                <td className="p-2">
                  {(flagsByLine.get(l.id) ?? []).map((r) => (
                    <span key={r} className="mr-1 rounded bg-amber-100 px-1.5 font-mono text-xs">{r}</span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detail.flags.length === 0 && <p className="text-slate-600">No flags. Run the audit to check this claim.</p>}
      {SEVERITY_GROUPS.map(({ severity, title }) => {
        const flags = detail.flags.filter((f) => f.severity === severity);
        if (flags.length === 0) return null;
        const headingId = `group-${severity}`;
        return (
          <section key={severity} aria-labelledby={headingId} className="space-y-2">
            <h3 id={headingId} className="font-semibold">{title}</h3>
            {flags.map((f) => <FlagCard key={f.id} flag={f} locked={locked} onChange={replaceFlag} />)}
          </section>
        );
      })}
    </div>
  );
}
```

`aria-labelledby` on `<section>` gives it the `region` role the tests query. The region name must equal the `title` exactly, so the test queries "Billing errors" and "Price outliers".

Wire it into `App.tsx` in place of the case placeholder.

- [ ] **Step 4: Run frontend chain.** Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat(web): case detail with line badges, grouped flags, accept/reject and streamed explanations"
```

---

### Task 6: Letter review and export

**Files:**
- Create: `web/src/components/LetterPanel.tsx`, `web/src/components/LetterPanel.test.tsx`
- Modify: `web/src/components/CaseDetail.tsx` (render `<LetterPanel caseDetail={detail} onChange={reload} />` after the flag groups)

**Interfaces:**
- Consumes: `draftLetter`, `editLetter`, `approveLetter`, `exportUrl`, `messageOf`, `CaseDetail`, `Letter` (api.ts).
- Produces (mounted with a `key` of letter id + body, see Step 2): `export default function LetterPanel({ caseDetail, onChange }: { caseDetail: CaseDetail; onChange: () => Promise<void> | void })`.

- [ ] **Step 1: Write failing tests** — `web/src/components/LetterPanel.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import LetterPanel from "./LetterPanel";
import type { CaseDetail, Flag, Letter } from "../lib/api";

const accepted: Flag = {
  id: "f1", rule_id: "R1", severity: "error", line_ids: ["L2"], evidence: {}, est_overcharge: "30.00",
  message: "Duplicate", explanation: null, explanation_status: "ready", status: "accepted", reject_reason: null,
};
const letter = (over: Partial<Letter> = {}): Letter => ({
  id: "l1", status: "draft", body: "Dear provider,\nLine L2 duplicates L1: $30.00.", flag_ids: ["f1"],
  created_at: "2026-10-02T10:00:00Z", approved_at: null, ...over,
});
const caseWith = (over: Partial<CaseDetail>): CaseDetail => ({
  id: "c1", claim_id: "C0001", provider: null, payer: null, payer_type: "medicare", source: "fhir", status: "needs_review",
  line_count: 2, error_count: 1, est_overcharge: "30.00", outlier_amount: "0.00", created_at: "x", lines: [],
  flags: [accepted], letter: null, ...over,
});
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("draft button needs an accepted error or outlier", () => {
  render(<LetterPanel caseDetail={caseWith({ flags: [{ ...accepted, status: "open" }] })} onChange={() => {}} />);
  expect(screen.getByRole("button", { name: "Draft dispute letter" })).toBeDisabled();
  expect(screen.getByText(/Accept at least one billing error or price outlier/)).toBeInTheDocument();
});

test("drafting calls the API and refreshes the case", async () => {
  const fetchMock = vi.fn(async () => json(letter(), 201));
  vi.stubGlobal("fetch", fetchMock);
  const onChange = vi.fn();
  render(<LetterPanel caseDetail={caseWith({})} onChange={onChange} />);
  await userEvent.click(screen.getByRole("button", { name: "Draft dispute letter" }));
  expect(fetchMock).toHaveBeenCalledWith("/api/cases/c1/letter", expect.objectContaining({ method: "POST" }));
  expect(onChange).toHaveBeenCalled();
});

test("an edit that adds a number shows the server's reason", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "edit adds numbers not in the findings: $999.00" }, 422)));
  render(<LetterPanel caseDetail={caseWith({ letter: letter() })} onChange={() => {}} />);
  const box = screen.getByLabelText("Letter text");
  await userEvent.type(box, " Pay $999.00 now.");
  await userEvent.click(screen.getByRole("button", { name: "Save edits" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("edit adds numbers not in the findings: $999.00");
});

test("approving saves unsaved edits first", async () => {
  const fetchMock = vi.fn(async (url: string) => json(url.endsWith("/approve") ? letter({ status: "approved" }) : letter()));
  vi.stubGlobal("fetch", fetchMock);
  render(<LetterPanel caseDetail={caseWith({ letter: letter() })} onChange={() => {}} />);
  await userEvent.type(screen.getByLabelText("Letter text"), " Thank you.");
  await userEvent.click(screen.getByRole("button", { name: "Approve letter" }));
  const urls = fetchMock.mock.calls.map(([u]) => String(u));
  expect(urls).toEqual(["/api/letters/l1", "/api/letters/l1/approve"]);
});

test("approved letters are read-only with export links", () => {
  render(<LetterPanel caseDetail={caseWith({ status: "approved", letter: letter({ status: "approved", approved_at: "2026-10-02T11:00:00Z" }) })} onChange={() => {}} />);
  expect(screen.getByLabelText("Letter text")).toHaveAttribute("readonly");
  expect(screen.queryByRole("button", { name: "Approve letter" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Draft|Redraft/ })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Download .txt" })).toHaveAttribute("href", "/api/letters/l1/export?format=txt");
  expect(screen.getByRole("link", { name: "Download .docx" })).toHaveAttribute("href", "/api/letters/l1/export?format=docx");
});
```

Run: `npm test` — Expected: FAIL.

- [ ] **Step 2: Implement `web/src/components/LetterPanel.tsx`**

```tsx
import { useState } from "react";
import { approveLetter, draftLetter, editLetter, exportUrl, messageOf, type CaseDetail } from "../lib/api";

export default function LetterPanel({ caseDetail, onChange }: { caseDetail: CaseDetail; onChange: () => Promise<void> | void }) {
  const letter = caseDetail.letter;
  const [body, setBody] = useState(letter?.body ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const approved = letter?.status === "approved";
  const canDraft = caseDetail.flags.some((f) => f.status === "accepted" && (f.severity === "error" || f.severity === "outlier"));
  const dirty = letter !== null && body !== letter.body;

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChange();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="letter-heading" className="space-y-3 rounded border bg-white p-4">
      <h3 id="letter-heading" className="font-semibold">Dispute letter</h3>
      <p className="text-sm text-slate-600">
        Built only from accepted billing errors and price outliers. Price outliers ask for an itemized justification; they are not claimed as errors. Nothing is sent anywhere — you download the letter.
      </p>
      {!approved && (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy || !canDraft} onClick={() => run(() => draftLetter(caseDetail.id))} className="rounded border px-3 py-1.5 disabled:opacity-50">
            {letter ? "Redraft from accepted flags" : "Draft dispute letter"}
          </button>
          {!canDraft && <span className="text-sm text-slate-600">Accept at least one billing error or price outlier to draft a letter.</span>}
        </div>
      )}
      {letter && (
        <>
          <label className="flex flex-col text-sm">
            Letter text
            <textarea
              value={body}
              readOnly={approved}
              onChange={(e) => setBody(e.target.value)}
              rows={14}
              maxLength={20000}
              className="mt-1 rounded border p-2 font-mono text-sm read-only:bg-slate-50"
            />
          </label>
          {approved ? (
            <div className="flex flex-wrap gap-3 text-sm">
              <span className="text-emerald-700">Approved {letter.approved_at ? new Date(letter.approved_at).toLocaleString() : ""}</span>
              <a href={exportUrl(letter.id, "txt")} download className="text-blue-700 underline">Download .txt</a>
              <a href={exportUrl(letter.id, "docx")} download className="text-blue-700 underline">Download .docx</a>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={busy || !dirty} onClick={() => run(() => editLetter(letter.id, body))} className="rounded border px-3 py-1.5 disabled:opacity-50">
                Save edits
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    if (dirty) await editLetter(letter.id, body);
                    await approveLetter(letter.id);
                  })
                }
                className="rounded bg-emerald-700 px-3 py-1.5 text-white disabled:opacity-50"
              >
                Approve letter
              </button>
            </div>
          )}
        </>
      )}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  );
}
```

Add it to `CaseDetail.tsx` after the flag groups as `<LetterPanel key={`${detail.letter?.id}:${detail.letter?.body}`} caseDetail={detail} onChange={reload} />` — the `key` remounts the panel when the server's letter changes, which resets the textarea without a state-syncing effect (the Vite template's `react-hooks` lint config rejects `setState` inside effects). Add one test to `CaseDetail.test.tsx` for Review Focus 4: with `letter: { ...approved }`, the `Run audit` button and every `Accept` button are disabled.

- [ ] **Step 3: Run frontend chain.** Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web
git commit -m "feat(web): letter review — draft, number-locked edits, approve, TXT/DOCX export"
```

---

### Task 7: Audit log screens

**Files:**
- Create: `web/src/components/AuditLog.tsx`, `web/src/components/AuditLog.test.tsx`
- Modify: `web/src/App.tsx` (log route renders `<AuditLog onOpenCase={(id) => navigate({ name: "case", id })} />`), `web/src/components/CaseDetail.tsx` (render `<AuditLog caseId={id} refreshKey={detail.status + detail.flags.map((f) => f.status).join()} />` at the bottom under an `<h3>Activity</h3>`)

**Interfaces:**
- Consumes: `auditLog(caseId?)`, `AuditEvent`, `messageOf` (api.ts); `humanizeAction` (format.ts); `routeHref` (route.ts).
- Produces: `export default function AuditLog({ caseId, refreshKey, onOpenCase }: { caseId?: string; refreshKey?: string; onOpenCase?: (id: string) => void })`.

- [ ] **Step 1: Write failing tests** — `web/src/components/AuditLog.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import AuditLog from "./AuditLog";
import type { AuditEvent } from "../lib/api";

const ev = (over: Partial<AuditEvent>): AuditEvent => ({
  id: 1, case_id: "c1", actor: "reviewer", action: "flag_reviewed", detail: { rule_id: "R1", from: "open", to: "accepted" },
  ref_versions: [], at: "2026-10-02T10:00:00Z", ...over,
});
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("case timeline uses the case endpoint and summarizes details", async () => {
  const fetchMock = vi.fn(async () => json([ev({})]));
  vi.stubGlobal("fetch", fetchMock);
  render(<AuditLog caseId="c1" />);
  expect(await screen.findByText("Flag reviewed")).toBeInTheDocument();
  expect(screen.getByText(/rule_id: R1/)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/cases/c1/audit-log", expect.anything());
});

test("global view links events to their case", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json([ev({ case_id: "c9", action: "letter_exported" })])));
  const onOpenCase = vi.fn();
  render(<AuditLog onOpenCase={onOpenCase} />);
  await userEvent.click(await screen.findByRole("link", { name: "Open case" }));
  expect(onOpenCase).toHaveBeenCalledWith("c9");
});

test("empty and error states", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json([])));
  const { unmount } = render(<AuditLog caseId="c1" />);
  expect(await screen.findByText("No activity yet.")).toBeInTheDocument();
  unmount();
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "case not found" }, 404)));
  render(<AuditLog caseId="c1" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("case not found");
});
```

Run: `npm test` — Expected: FAIL.

- [ ] **Step 2: Implement `web/src/components/AuditLog.tsx`**

```tsx
import { useEffect, useState } from "react";
import { auditLog, messageOf, type AuditEvent } from "../lib/api";
import { humanizeAction } from "../lib/format";
import { routeHref } from "../lib/route";

const summarize = (detail: Record<string, unknown>) =>
  Object.entries(detail)
    .filter(([, v]) => v !== null && v !== "")
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");

export default function AuditLog({ caseId, refreshKey, onOpenCase }: { caseId?: string; refreshKey?: string; onOpenCase?: (id: string) => void }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    auditLog(caseId).then(
      (e) => live && setEvents(e),
      (e) => live && setError(messageOf(e)),
    );
    return () => {
      live = false;
    };
  }, [caseId, refreshKey]);

  if (error) return <p role="alert" className="text-red-700">{error}</p>;
  if (!events) return <p>Loading activity…</p>;
  if (events.length === 0) return <p className="text-slate-600">No activity yet.</p>;

  return (
    <section className="space-y-2">
      {!caseId && <h2 className="text-xl font-semibold">Audit log</h2>}
      <ol className="space-y-2">
        {events.map((e) => (
          <li key={e.id} className="rounded border bg-white p-2 text-sm">
            <div className="flex flex-wrap gap-3">
              <time dateTime={e.at} className="text-slate-500">{new Date(e.at).toLocaleString()}</time>
              <span className="font-medium">{humanizeAction(e.action)}</span>
              <span className="text-slate-500">by {e.actor}</span>
              {!caseId && e.case_id && onOpenCase && (
                <a
                  href={routeHref({ name: "case", id: e.case_id })}
                  onClick={(ev) => {
                    ev.preventDefault();
                    onOpenCase(e.case_id as string);
                  }}
                  className="ml-auto text-blue-700 hover:underline"
                >
                  Open case
                </a>
              )}
            </div>
            {Object.keys(e.detail).length > 0 && <p className="mt-1 font-mono text-xs text-slate-600 break-all">{summarize(e.detail)}</p>}
            {e.ref_versions.length > 0 && <p className="text-xs text-slate-500">Reference: {e.ref_versions.join(", ")}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}
```

Wire into `App.tsx` and `CaseDetail.tsx` as listed under **Files**. In `CaseDetail.test.tsx`, the existing `fetch` stubs return the case detail for every unmatched URL; make them return `[]` for URLs ending in `/audit-log` so the new child doesn't break those tests.

- [ ] **Step 3: Run frontend chain.** Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web
git commit -m "feat(web): per-case activity timeline and workspace audit log"
```

---

### Task 8: Playwright smoke E2E, CI job, docs, production deploy

**Files:**
- Create: `web/playwright.config.ts`, `web/e2e/smoke.spec.ts`
- Modify: `web/vite.config.ts` (exclude `e2e/` from Vitest — already excluded by the `include` glob; verify), `.github/workflows/ci.yml`, `README.md`, `docs/PROGRESS.md`

**Interfaces:**
- Consumes: the whole app; backend demo seeding (`PRIORPATH_DEMO` defaults to on; 10 demo cases).
- Produces: `npm run e2e` (local stack) and `PLAYWRIGHT_BASE_URL=https://priorpath.vercel.app npm run e2e` (production, no local servers).

- [ ] **Step 1: Write the E2E test** — `web/e2e/smoke.spec.ts`

```ts
import { expect, test } from "@playwright/test";

test("demo case: accept a flag, approve the letter, export it", async ({ page }) => {
  await page.goto("/");
  const rows = page.getByRole("row").filter({ has: page.getByRole("link") });
  await expect(rows).toHaveCount(10);

  await page.getByLabel("Sort").selectOption("overcharge");
  await rows.first().getByRole("link").click();
  await expect(page.getByRole("heading", { name: /Claim/ })).toBeVisible();

  const errors = page.getByRole("region", { name: "Billing errors" });
  const firstOpen = errors.getByRole("button", { name: "Accept" }).first();
  if (await firstOpen.isVisible()) await firstOpen.click();
  await expect(errors.getByText("Accepted").first()).toBeVisible();

  const letter = page.getByRole("region", { name: "Dispute letter" });
  await letter.getByRole("button", { name: /Draft dispute letter|Redraft/ }).click();
  await expect(letter.getByLabel("Letter text")).not.toHaveValue("");
  await letter.getByRole("button", { name: "Approve letter" }).click();

  const download = page.waitForEvent("download");
  await letter.getByRole("link", { name: "Download .txt" }).click();
  expect((await download).suggestedFilename()).toMatch(/^dispute-letter-.*\.txt$/);
});
```

The highest-overcharge demo case always has at least one billing error (demo claims are generated with planted errors). If the top case has no "Billing errors" region, pick the first row whose "Billing errors" cell is > 0 instead; do not weaken the assertions.

- [ ] **Step 2: Config** — `web/playwright.config.ts`

```ts
import { defineConfig, devices } from "@playwright/test";

const remote = process.env.PLAYWRIGHT_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: remote ?? "http://127.0.0.1:4173", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: remote
    ? undefined
    : [
        {
          command: "cd .. && uvicorn app.main:app --port 8000",
          url: "http://127.0.0.1:8000/api/health",
          reuseExistingServer: !process.env.CI,
        },
        {
          command: "npm run build && npx vite preview --port 4173 --strictPort",
          url: "http://127.0.0.1:4173",
          reuseExistingServer: !process.env.CI,
        },
      ],
});
```

Local run (Docker Postgres up and migrated; uses the test database, so truncate afterwards like the Plan 2 smoke run):

```bash
cd ~/Desktop/portfolio/projects/PriorPath && source .venv/bin/activate
export DATABASE_URL=postgresql+psycopg://priorpath:priorpath@localhost:5433/priorpath_test SESSION_SECRET=local CRON_SECRET=local
cd web && npx playwright install chromium && npm run e2e
docker compose exec -T db psql -U priorpath -d priorpath_test -c "TRUNCATE workspaces, cases, flags, letters, audit_events, llm_usage RESTART IDENTITY CASCADE"
```

Expected: 1 passed. No `OPENROUTER_API_KEY` is needed: demo explanations are precomputed and the test never clicks "Generate explanations".

- [ ] **Step 3: CI job** — append to `.github/workflows/ci.yml`:

```yaml
  e2e:
    runs-on: ubuntu-latest
    needs: [backend, frontend]
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: priorpath
          POSTGRES_PASSWORD: priorpath
          POSTGRES_DB: priorpath_test
        ports:
          - 5433:5432
        options: >-
          --health-cmd "pg_isready -U priorpath"
          --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      DATABASE_URL: postgresql+psycopg://priorpath:priorpath@localhost:5433/priorpath_test
      SESSION_SECRET: ci-session-secret
      CRON_SECRET: ci-cron-secret
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version-file: .python-version
          cache: pip
          cache-dependency-path: requirements-dev.txt
      - run: pip install -r requirements-dev.txt
      - run: alembic upgrade head
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm ci
        working-directory: web
      - run: npx playwright install --with-deps chromium
        working-directory: web
      - run: npm run e2e
        working-directory: web
      - uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: playwright-report
          path: web/test-results
```

- [ ] **Step 4: Docs.** In `README.md`, replace the "Try the v2 API" blockquote with:

```markdown
> **Try v2:** open https://priorpath.vercel.app — your browser gets its own demo workspace with 10 synthetic claims.
> Open a case, accept a billing error, draft and approve the dispute letter, then download it. The API is documented at `/api/docs`.
```

In `docs/PROGRESS.md`: add a "Plan 3 — Reviewer UI" section in the same shape as Plans 1–2 (what was built table, decisions, review catches, verification), update the header line, the "At a glance" table and "What's next".

- [ ] **Step 5: Run backend chain, frontend chain and the local E2E. Commit:**

```bash
git add web/e2e web/playwright.config.ts .github/workflows/ci.yml README.md docs/PROGRESS.md
git commit -m "test(e2e): Playwright demo smoke in CI; README and progress log for the UI"
```

- [ ] **Step 6 (controller): ship.** Push `v2-bill-audit` (user approval required for the push), wait for all three CI jobs green, `npx vercel deploy --prod`, then:

```bash
curl -s https://priorpath.vercel.app/api/health
cd web && PLAYWRIGHT_BASE_URL=https://priorpath.vercel.app npm run e2e
cd .. && python scripts/smoke.py https://priorpath.vercel.app --require-explanations
```

Expected: `{"status":"ok"}`, 1 passed, `ok: ...`. Each run leaves one throwaway workspace, removed by the daily cleanup.
