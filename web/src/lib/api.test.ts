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

test("ids are encoded in request paths", async () => {
  const fetchMock = vi.fn(async () => json(200, {}));
  vi.stubGlobal("fetch", fetchMock);
  const { getCase } = await import("./api");
  await getCase("a/b");
  expect(fetchMock.mock.calls[0]).toEqual(["/api/cases/a%2Fb", expect.anything()]);
});
