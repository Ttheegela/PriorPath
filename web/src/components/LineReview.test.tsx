import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import LineReview from "./LineReview";
import type { CaseDetail, Line } from "../lib/api";

const line = (over: Partial<Line> = {}): Line => ({
  id: "P1-L1", code: "99213", modifiers: ["25"], units: 1, charge: "120.00", date_of_service: "2026-11-03",
  place_of_service: "11", source: "extracted", confidence: 0.9, field_confidence: {}, ...over,
});
const caseWith = (lines: Line[]): CaseDetail => ({
  id: "c1", claim_id: "B1", provider: null, payer: null, payer_type: "medicare", source: "pdf", status: "needs_line_review",
  line_count: lines.length, page_count: 2, error_count: 0, est_overcharge: "0.00", outlier_amount: "0.00", created_at: "x",
  lines, flags: [], letter: null,
});
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const save = () => userEvent.click(screen.getByRole("button", { name: "Save lines and run audit" }));

test("shows one image per page and labelled inputs per line", () => {
  render(<LineReview caseDetail={caseWith([line()])} onSaved={() => {}} />);
  expect(screen.getByAltText("Bill page 1")).toHaveAttribute("src", "/api/cases/c1/pages/1");
  expect(screen.getByAltText("Bill page 2")).toHaveAttribute("src", "/api/cases/c1/pages/2");
  for (const f of ["Code", "Units", "Charge", "Date", "Modifiers"]) expect(screen.getByLabelText(`${f} for P1-L1`)).toBeInTheDocument();
});

test("low-confidence fields are marked with words and a dashed border, not color", () => {
  render(<LineReview caseDetail={caseWith([line({ field_confidence: { code: 0.6 } })])} onSaved={() => {}} />);
  const code = screen.getByLabelText("Code for P1-L1");
  expect(code).toHaveAttribute("data-low-confidence", "true");
  expect(code).toHaveAttribute("aria-invalid", "false");
  expect(screen.getByText("check")).toBeInTheDocument();
  expect(screen.getByLabelText("Units for P1-L1")).not.toHaveAttribute("data-low-confidence");
});

test("saving patches the edited lines, then audits, then calls onSaved", async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => json(caseWith([line()])));
  vi.stubGlobal("fetch", fetchMock);
  const onSaved = vi.fn();
  render(<LineReview caseDetail={caseWith([line()])} onSaved={onSaved} />);
  const units = screen.getByLabelText("Units for P1-L1");
  await userEvent.clear(units);
  await userEvent.type(units, "3");
  await save();
  expect(fetchMock.mock.calls.map(([u, i]) => `${i?.method} ${u}`)).toEqual([
    "PATCH /api/cases/c1/lines",
    "POST /api/cases/c1/audit",
  ]);
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
    lines: [{ id: "P1-L1", code: "99213", modifiers: ["25"], units: 3, charge: "120.00", date_of_service: "2026-11-03", place_of_service: "11" }],
  });
  expect(onSaved).toHaveBeenCalled();
});

test("a rejected save shows the server message and skips the audit", async () => {
  const fetchMock = vi.fn(async () => json({ detail: "lines[0].units: must be positive" }, 422));
  vi.stubGlobal("fetch", fetchMock);
  const onSaved = vi.fn();
  render(<LineReview caseDetail={caseWith([line()])} onSaved={onSaved} />);
  await save();
  expect(await screen.findByRole("alert")).toHaveTextContent("lines[0].units: must be positive");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(onSaved).not.toHaveBeenCalled();
});

test("add and remove lines; saving with none is blocked", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  render(<LineReview caseDetail={caseWith([line()])} onSaved={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Add line" }));
  expect(screen.getByLabelText("Code for NEW-1")).toHaveValue("");
  await userEvent.click(screen.getByRole("button", { name: "Remove NEW-1" }));
  expect(screen.queryByLabelText("Code for NEW-1")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Remove P1-L1" }));
  await save();
  expect(screen.getByRole("alert")).toHaveTextContent("Add at least one line");
  expect(fetchMock).not.toHaveBeenCalled();
});
