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

// the Activity timeline fetches /audit-log; keep it empty so tests only see case data
const stubFetch = (h: (url: string, init?: RequestInit) => Promise<Response>) =>
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => (url.endsWith("/audit-log") ? Promise.resolve(json([])) : h(url, init)));
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("shows totals, groups flags separately and badges flagged lines", async () => {
  stubFetch(vi.fn(async () => json(detail())));
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
  stubFetch(fetchMock);
  render(<CaseDetail id="c1" onBack={() => {}} />);
  const card = within(await screen.findByRole("article", { name: /R1/ }));
  await userEvent.click(card.getByRole("button", { name: "Reject" }));
  expect(card.getByRole("button", { name: "Confirm reject" })).toBeDisabled();
  await userEvent.type(card.getByLabelText("Reason"), "separate visits");
  await userEvent.click(card.getByRole("button", { name: "Confirm reject" }));
  expect(await card.findByText(/Rejected: separate visits/)).toBeInTheDocument();
});

test("accept shows server errors next to the flag", async () => {
  stubFetch(vi.fn(async (url: string) =>
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
  stubFetch(vi.fn(async (url: string) => {
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
  stubFetch(vi.fn(async (url: string) =>
    url.endsWith("/explain") ? new Response('event: start\ndata: {"pending": 1}\n\n', { status: 200 }) : json(detail())));
  render(<CaseDetail id="c1" onBack={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Generate explanations" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/stopped early/);
});

test("unknown case shows not found with a way back", async () => {
  stubFetch(vi.fn(async () => json({ detail: "case not found" }, 404)));
  const onBack = vi.fn();
  render(<CaseDetail id="nope" onBack={onBack} />);
  expect(await screen.findByText("Case not found.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Back to cases" }));
  expect(onBack).toHaveBeenCalled();
});

test("done with remaining shows the run-again hint", async () => {
  const sse = 'event: start\ndata: {"pending": 2}\n\nevent: done\ndata: {"ready": 1, "unavailable": 0, "remaining": 1}\n\n';
  stubFetch(vi.fn(async (url: string) =>
    url.endsWith("/explain") ? new Response(sse, { status: 200 }) : json(detail())));
  render(<CaseDetail id="c1" onBack={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Generate explanations" }));
  expect(await screen.findByText(/1 left — run again to finish/)).toBeInTheDocument();
});

test("a different case after a 404 loads normally", async () => {
  stubFetch(vi.fn(async (url: string) =>
    url === "/api/cases/nope" ? json({ detail: "case not found" }, 404) : json(detail())));
  const { rerender } = render(<CaseDetail key="nope" id="nope" onBack={() => {}} />);
  expect(await screen.findByText("Case not found.")).toBeInTheDocument();
  rerender(<CaseDetail key="c1" id="c1" onBack={() => {}} />);
  expect(await screen.findByRole("heading", { name: /C0001/ })).toBeInTheDocument();
});

test("cancel clears the reject reason", async () => {
  stubFetch(vi.fn(async () => json(detail())));
  render(<CaseDetail id="c1" onBack={() => {}} />);
  const card = within(await screen.findByRole("article", { name: /R1/ }));
  await userEvent.click(card.getByRole("button", { name: "Reject" }));
  await userEvent.type(card.getByLabelText("Reason"), "oops");
  await userEvent.click(card.getByRole("button", { name: "Cancel" }));
  await userEvent.click(card.getByRole("button", { name: "Reject" }));
  expect(card.getByLabelText("Reason")).toHaveValue("");
});

test("an approved letter locks Run audit and every Accept", async () => {
  const approved = { id: "l1", status: "approved" as const, body: "Dear provider", flag_ids: ["f1"], created_at: "2026-10-02T10:00:00Z", approved_at: "2026-10-02T11:00:00Z" };
  stubFetch(vi.fn(async () => json(detail({ letter: approved }))));
  render(<CaseDetail id="c1" onBack={() => {}} />);
  expect(await screen.findByRole("button", { name: "Run audit" })).toBeDisabled();
  const accepts = screen.getAllByRole("button", { name: "Accept" });
  expect(accepts.length).toBeGreaterThan(0);
  for (const b of accepts) expect(b).toBeDisabled();
});
