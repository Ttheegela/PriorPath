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

test("redraft is disabled with a hint while there are unsaved edits", async () => {
  render(<LetterPanel caseDetail={caseWith({ letter: letter() })} onChange={() => {}} />);
  const redraft = screen.getByRole("button", { name: "Redraft from accepted flags" });
  expect(redraft).toBeEnabled();
  await userEvent.type(screen.getByLabelText("Letter text"), " more");
  expect(redraft).toBeDisabled();
  expect(screen.getByText("Save or undo your edits first.")).toBeInTheDocument();
});
