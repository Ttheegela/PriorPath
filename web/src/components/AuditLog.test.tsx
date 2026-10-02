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

test("a new refreshKey refetches and a later success clears the error", async () => {
  let n = 0;
  const fetchMock = vi.fn(async () => (++n === 1 ? json({ detail: "boom" }, 500) : json([ev({})])));
  vi.stubGlobal("fetch", fetchMock);
  const { rerender } = render(<AuditLog caseId="c1" refreshKey="a" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  rerender(<AuditLog caseId="c1" refreshKey="b" />);
  expect(await screen.findByText("Flag reviewed")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("a failed refresh keeps the loaded events visible", async () => {
  let n = 0;
  vi.stubGlobal("fetch", vi.fn(async () => (++n === 1 ? json([ev({})]) : json({ detail: "boom" }, 500))));
  const { rerender } = render(<AuditLog caseId="c1" refreshKey="a" />);
  expect(await screen.findByText("Flag reviewed")).toBeInTheDocument();
  rerender(<AuditLog caseId="c1" refreshKey="b" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  expect(screen.getByText("Flag reviewed")).toBeInTheDocument();
});
