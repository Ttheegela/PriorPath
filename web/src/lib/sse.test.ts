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
