import { fireEvent, render, screen, within } from "@testing-library/react";
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
  const big = new File(["x".repeat(4_000_001)], "big.json", { type: "application/json" });
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

test("a failed list shows the error and no empty-state claim", async () => {
  stubFetch({ "/api/cases": () => ok({ detail: "boom" }, 503) });
  render(<CaseQueue onOpen={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  expect(screen.queryByText("No cases match.")).not.toBeInTheDocument();
  expect(screen.queryByText("Loading cases…")).not.toBeInTheDocument();
});

test("modifier-click on a case link leaves navigation to the browser", async () => {
  stubFetch({ "/api/cases": () => ok(cases) });
  const onOpen = vi.fn();
  render(<CaseQueue onOpen={onOpen} />);
  const link = await screen.findByRole("link", { name: "C0001" });
  fireEvent.click(link, { ctrlKey: true });
  expect(onOpen).not.toHaveBeenCalled();
});
