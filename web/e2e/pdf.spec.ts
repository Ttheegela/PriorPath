import { existsSync } from "node:fs";
import { expect, test } from "@playwright/test";

// Once the recorded extraction is committed, a missing PDF case is a seeding regression, not a skip.
const recorded = existsSync("../data/demo/pdf_extractions.json");

test("PDF demo case: review lines, see the bill page, save, see flags", async ({ page }) => {
  await page.goto("/");
  const link = page.getByRole("link", { name: /^B\d{4}/ }).first();
  await expect(page.getByRole("row").first()).toBeVisible();
  if (recorded) await expect(link).toBeVisible();
  else test.skip((await link.count()) === 0, "no PDF demo case until the recorded extraction is committed");
  await link.click();

  const review = page.getByRole("heading", { name: "Review extracted lines" });
  const collapsed = page.getByText("Extracted lines and bill pages");
  await expect(review.or(collapsed)).toBeVisible(); // isVisible() alone does not wait for the case to load
  if (await collapsed.isVisible()) await collapsed.click();
  await expect(page.getByRole("img", { name: "Bill page 1" })).toBeVisible();

  await page.getByRole("button", { name: "Save lines and run audit" }).click();
  await expect(page.getByText("No flags. Run the audit to check this claim.")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Billing errors" })).toBeVisible();
});
