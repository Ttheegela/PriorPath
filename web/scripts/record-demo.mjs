// Records the README demo (docs/demo.gif) with Playwright against a running PriorPath.
//
//   1. cd web && npm run build && cd .. && DATABASE_URL=... SESSION_SECRET=local CRON_SECRET=local \
//        PRIORPATH_DEMO=1 uvicorn app.main:app --port 8000          (or set BASE_URL to a deployed copy)
//   2. cd web && npm run demo:record      -> demo-video/demo.webm (1280x800, under 60 s)
//   3. ffmpeg -y -i demo-video/demo.webm -vf "fps=10,scale=960:-1:flags=lanczos,palettegen=stats_mode=diff" demo-video/palette.png
//      ffmpeg -y -i demo-video/demo.webm -i demo-video/palette.png \
//        -lavfi "fps=10,scale=960:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5" ../docs/demo.gif
//
// The live PDF upload runs only when OPENROUTER_API_KEY is set (it calls the vision model through the
// server); otherwise the pre-extracted demo bill case (B0001) is opened instead.
import { mkdirSync, renameSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://127.0.0.1:8000";
const OUT = "demo-video";
const pause = (ms = 1500) => new Promise((r) => setTimeout(r, ms));

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  acceptDownloads: true,
  recordVideo: { dir: OUT, size: { width: 1280, height: 800 } },
});
const page = await context.newPage();
const video = page.video();

await page.goto(BASE_URL);
const rows = page.getByRole("row").filter({ has: page.getByRole("link") });
await rows.first().waitFor();
await pause(2500);

await page.getByLabel("Sort").selectOption("overcharge");
await pause();
await rows.first().getByRole("link").click();
await page.getByRole("heading", { name: /Claim/ }).waitFor();
await pause(2000);

const errors = page.getByRole("region", { name: "Billing errors" });
await errors.scrollIntoViewIfNeeded();
await pause(2500); // evidence row and explanation
const accept = errors.getByRole("button", { name: "Accept" }).first();
if (await accept.isVisible()) await accept.click();
await errors.getByText("Accepted").first().waitFor();
await pause(1500);

const letter = page.getByRole("region", { name: "Dispute letter" });
await letter.getByRole("button", { name: /Draft dispute letter|Redraft/ }).click();
await letter.scrollIntoViewIfNeeded();
await pause(3000);
await letter.getByRole("button", { name: "Approve letter" }).click();
await pause(1500);
const download = page.waitForEvent("download");
await letter.getByRole("button", { name: "Download .txt" }).click();
await download;
await pause(2000);

await page.getByRole("link", { name: "Cases" }).click();
await rows.first().waitFor();
await pause(1500);

if (process.env.OPENROUTER_API_KEY) {
  await page.getByLabel(/Claim file/).setInputFiles("../data/demo/bills/B0001.pdf");
  await page.getByLabel(/synthetic or test bill/).check();
  await pause(1500);
  await page.getByRole("button", { name: "Upload" }).click();
  await page.getByRole("link", { name: "Open" }).first().click();
} else {
  await page.getByRole("link", { name: /^B\d{4}/ }).first().click();
}
await page.getByRole("heading", { name: /Claim|Review extracted lines/ }).first().waitFor();
await pause(4000);

await context.close(); // flushes the video
renameSync(await video.path(), `${OUT}/demo.webm`);
await browser.close();
