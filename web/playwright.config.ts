import { defineConfig, devices } from "@playwright/test";

// Ports of their own, so a running `make api` (8000) or `npm run dev` (3000) is not reused.
const API_PORT = 8765;
const WEB_PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  timeout: 600_000, // a first run may pull the VM image
  use: { baseURL: `http://localhost:${WEB_PORT}` },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // The real API on the emptied lab_test database (see scripts/e2e_api.py).
      command: `cd .. && uv run python scripts/e2e_api.py ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/docs`,
      // Slow scripted steps leave time to reload the page in the middle of a run.
      env: { LAB_SCRIPTED_STEP_DELAY_S: "1" },
      timeout: 60_000,
      stdout: "pipe",
    },
    {
      command: `npx next dev --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      env: { LAB_API_URL: `http://127.0.0.1:${API_PORT}` },
      timeout: 120_000,
    },
  ],
});
