import { defineConfig, devices } from "@playwright/test";

const remote = process.env.PLAYWRIGHT_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: remote ?? "http://127.0.0.1:8000", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // One server: FastAPI serves the built UI (public/) and the API. Build first so it mounts.
  webServer: remote
    ? undefined
    : {
        command: "npm run build && cd .. && uvicorn app.main:app --port 8000",
        url: "http://127.0.0.1:8000/api/health",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
