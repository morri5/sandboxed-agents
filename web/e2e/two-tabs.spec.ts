import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// The scripted harness: a fake agent in a real VM, no model and no paid API.
const CALC_REPO = path.resolve(__dirname, "../../fixtures/calc-repo");

const shown = (page: Page) =>
  page
    .locator("li[data-seq]")
    .evaluateAll((items) =>
      items.map((li) => `${(li as HTMLElement).dataset.seq}:${(li as HTMLElement).dataset.type}`),
    );

const createSession = async (page: Page) => {
  await page.goto("/");
  await page.getByLabel("Repo URL").fill(CALC_REPO);
  await page.getByLabel("Harness").selectOption("scripted");
  await page.getByRole("button", { name: "Create" }).click();
  await page.waitForURL(/\?session=/);
  return new URL(page.url()).searchParams.get("session")!;
};

test("two tabs on one session see the same events in the same order", async ({
  context,
  request,
}) => {
  const tabA = await context.newPage();
  const sessionId = await createSession(tabA);

  try {
    const tabB = await context.newPage();
    await tabB.goto(`/?session=${sessionId}`);
    for (const tab of [tabA, tabB]) await expect(tab.getByText("live", { exact: true })).toBeVisible();

    await tabA.getByPlaceholder("Ask the agent").fill("Do the tests pass?");
    // A double click sends the same idem_key twice: one message, one run.
    await tabA.getByRole("button", { name: "Send" }).dblclick();

    for (const tab of [tabA, tabB]) {
      await expect(tab.locator('li[data-type="run_finished"]')).toHaveCount(1, {
        timeout: 540_000,
      });
    }

    // A workspace snapshot may follow the run's end: compare once the log is quiet.
    let log: string[] = [];
    await expect(async () => {
      const r = await request.get(`/api/sessions/${sessionId}/events`);
      const events: { seq: number; type: string }[] = await r.json();
      const now = events.map((e) => `${e.seq}:${e.type}`);
      const stable = JSON.stringify(now) === JSON.stringify(log);
      log = now;
      expect(stable).toBe(true);
      expect(await shown(tabA)).toEqual(log);
      expect(await shown(tabB)).toEqual(log);
    }).toPass({ intervals: [1_000], timeout: 30_000 });

    expect(log.filter((e) => e.endsWith(":user_message"))).toHaveLength(1);
    expect(log.filter((e) => e.endsWith(":run_started"))).toHaveLength(1);
    expect(log.map((e) => Number(e.split(":")[0]))).toEqual(log.map((_, i) => i + 1));
  } finally {
    // Destroys the session's VM; wait for it, or the API may stop before it is gone.
    await request.delete(`/api/sessions/${sessionId}`);
    await expect(async () => {
      const r = await request.get(`/api/sessions/${sessionId}/events`);
      const events: { type: string }[] = await r.json();
      expect(events.some((e) => e.type === "session_closed")).toBe(true);
    }).toPass({ intervals: [1_000], timeout: 60_000 });
  }
});

test("a reload in the middle of a run catches up without gaps or duplicates", async ({
  page,
  request,
}) => {
  const sessionId = await createSession(page);
  try {
    await expect(page.getByText("live", { exact: true })).toBeVisible();
    await page.getByPlaceholder("Ask the agent").fill("Do the tests pass?");
    await page.getByRole("button", { name: "Send" }).click();

    // Reload once the run has started but before it ends.
    await expect(page.locator('li[data-type="tool_call"]').first()).toBeVisible({ timeout: 540_000 });
    expect(await page.locator('li[data-type="run_finished"]').count()).toBe(0);
    await page.reload();

    await expect(page.locator('li[data-type="run_finished"]')).toHaveCount(1, { timeout: 540_000 });
    let log: string[] = [];
    await expect(async () => {
      const r = await request.get(`/api/sessions/${sessionId}/events`);
      const events: { seq: number; type: string }[] = await r.json();
      const now = events.map((e) => `${e.seq}:${e.type}`);
      const stable = JSON.stringify(now) === JSON.stringify(log);
      log = now;
      expect(stable).toBe(true);
      expect(await shown(page)).toEqual(log);
    }).toPass({ intervals: [1_000], timeout: 30_000 });
    expect(log.map((e) => Number(e.split(":")[0]))).toEqual(log.map((_, i) => i + 1));
  } finally {
    await request.delete(`/api/sessions/${sessionId}`);
    await expect(async () => {
      const r = await request.get(`/api/sessions/${sessionId}/events`);
      const events: { type: string }[] = await r.json();
      expect(events.some((e) => e.type === "session_closed")).toBe(true);
    }).toPass({ intervals: [1_000], timeout: 60_000 });
  }
});
