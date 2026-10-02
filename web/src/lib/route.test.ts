import { expect, test } from "vitest";
import { parseRoute, routeHref } from "./route";

test("round-trips routes through the query string", () => {
  for (const r of [{ name: "queue" }, { name: "case", id: "a b" }, { name: "log" }] as const) {
    expect(parseRoute(new URL(routeHref(r), "http://x").search)).toEqual(r);
  }
  expect(parseRoute("?unknown=1")).toEqual({ name: "queue" });
});
