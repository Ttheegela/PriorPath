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
  source: "structured" | "extracted";
  confidence: number | null;
  field_confidence: Record<string, number>;
}

export interface LineEdit {
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
  page_count: number | null;
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
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
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
let workspaceCreatedAt = NaN;
const DAY_MS = 24 * 60 * 60 * 1000;

/** Test-only: forget the memoized workspace so each test sets its own age. */
export function resetWorkspaceForTests() {
  workspace = undefined;
  workspaceCreatedAt = NaN;
}

/** True when the workspace was created over 24 h ago, so a 404 most likely means it expired. */
export const workspaceLikelyExpired = () => Date.now() - workspaceCreatedAt > DAY_MS;

/** Creates (or loads) this browser's workspace. Must resolve before any other call. */
export function ensureWorkspace(): Promise<void> {
  workspace ??= request<{ created_at: string }>("/api/workspace").then(
    (w) => {
      workspaceCreatedAt = Date.parse(w.created_at);
    },
    (e) => {
      workspace = undefined;
      throw e;
    },
  );
  return workspace;
}

export const listCases = () => request<CaseSummary[]>("/api/cases");
export const getCase = (id: string) => request<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`);
export const auditCase = (id: string) => request<CaseDetail>(`/api/cases/${encodeURIComponent(id)}/audit`, { method: "POST" });
export const resetDemo = () => request<{ cases: number }>("/api/demo/reset", { method: "POST" });
export const draftLetter = (caseId: string) => request<Letter>(`/api/cases/${encodeURIComponent(caseId)}/letter`, { method: "POST" });
export const editLetter = (id: string, body: string) => request<Letter>(`/api/letters/${encodeURIComponent(id)}`, jsonInit("PATCH", { body }));
export const approveLetter = (id: string) => request<Letter>(`/api/letters/${encodeURIComponent(id)}/approve`, { method: "POST" });

const decodeURIComponentSafe = (v: string) => {
  try {
    return decodeURIComponent(v);
  } catch {
    return null;
  }
};

export async function exportLetter(id: string, format: "txt" | "docx"): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`/api/letters/${encodeURIComponent(id)}/export?format=${format}`, { credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  const plain = /filename="?([^";]+)"?/.exec(disposition)?.[1];
  const filename = (star && decodeURIComponentSafe(star)) || plain || `dispute-letter.${format}`;
  return { blob: await res.blob(), filename };
}
export const auditLog = (caseId?: string) =>
  request<AuditEvent[]>(caseId ? `/api/cases/${encodeURIComponent(caseId)}/audit-log` : "/api/audit-log");

export const updateFlag = (id: string, status: FlagStatus, rejectReason?: string) =>
  request<Flag>(`/api/flags/${encodeURIComponent(id)}`, jsonInit("PATCH", { status, reject_reason: rejectReason ?? null }));

export const updateLines = (caseId: string, lines: LineEdit[]) =>
  request<CaseDetail>(`/api/cases/${encodeURIComponent(caseId)}/lines`, jsonInit("PATCH", { lines }));
export const pageUrl = (caseId: string, n: number) => `/api/cases/${encodeURIComponent(caseId)}/pages/${n}`;

export const isPdf = (file: File) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

export async function uploadCases(file: File, payerType: PayerType, { confirmSynthetic = false } = {}): Promise<UploadResult> {
  const pdf = isPdf(file);
  return request<UploadResult>(`/api/cases?payer_type=${payerType}${pdf && confirmSynthetic ? "&confirm_synthetic=true" : ""}`, {
    method: "POST",
    headers: { "Content-Type": pdf ? "application/pdf" : "application/json" },
    body: pdf ? file : await file.text(),
  });
}

export async function streamExplanations(caseId: string, onEvent: (e: SseEvent) => void): Promise<void> {
  const res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/explain`, { method: "POST", credentials: "same-origin" });
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
