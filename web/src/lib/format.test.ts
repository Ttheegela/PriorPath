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
