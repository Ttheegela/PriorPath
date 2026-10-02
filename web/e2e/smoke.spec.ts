import { expect, test } from "@playwright/test";

test("demo case: accept a flag, approve the letter, export it", async ({ page }) => {
  await page.goto("/");
  const rows = page.getByRole("row").filter({ has: page.getByRole("link") });
  await expect(rows).toHaveCount(10);

  await page.getByLabel("Sort").selectOption("overcharge");
  await rows.first().getByRole("link").click();
  await expect(page.getByRole("heading", { name: /Claim/ })).toBeVisible();

  const errors = page.getByRole("region", { name: "Billing errors" });
  const firstOpen = errors.getByRole("button", { name: "Accept" }).first();
  if (await firstOpen.isVisible()) await firstOpen.click();
  await expect(errors.getByText("Accepted").first()).toBeVisible();

  const letter = page.getByRole("region", { name: "Dispute letter" });
  await letter.getByRole("button", { name: /Draft dispute letter|Redraft/ }).click();
  await expect(letter.getByLabel("Letter text")).not.toHaveValue("");
  await letter.getByRole("button", { name: "Approve letter" }).click();

  const download = page.waitForEvent("download");
  await letter.getByRole("link", { name: "Download .txt" }).click();
  expect((await download).suggestedFilename()).toMatch(/^dispute-letter-.*\.txt$/);
});
