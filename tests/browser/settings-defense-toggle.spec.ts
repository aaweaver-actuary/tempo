import { expect, test } from "@playwright/test";
import type { APIResponse } from "@playwright/test";
import type { ActivityItem } from "../../app/lib/service-status";
import { assertDisposableTarget } from "./disposable-target";
import { navigate, noPageOverflow, prepareUI } from "./ui-fixtures";

test("defensive daily stack setting persists across navigation and reload", async ({ page, request }) => {
  const api = process.env.TEMPO_BROWSER_API_URL ?? (process.env.TEMPO_DOCKER_URL
    ? `${process.env.TEMPO_DOCKER_URL}/api` : "http://127.0.0.1:8001/api");
  const originalSettings = await (await request.get(`${api}/settings`)).json();
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const toggle = page.getByRole("checkbox", { name: /Defensive cards in daily stack/ });
    const saveButton = page.getByRole("button", { name: "Save settings" });
    await expect(saveButton).toBeEnabled();
    await expect(toggle).toBeChecked();
    await toggle.uncheck();
    await saveButton.click();
    await expect.poll(async () =>
      (await (await request.get(`${api}/settings`)).json()).include_defensive_cards_in_daily_stack,
    ).toBe(false);
    await expect(page.locator(".notification-toast.notification-info, .notification-toast.notification-success")).toHaveCount(0);
    await page.getByRole("button", { name: "Notifications" }).click();
    await page.getByRole("button", { name: "All", exact: true }).click();
    await expect(page.locator(".notification-list")).toContainText("Saved.");
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(toggle).not.toBeChecked();
  } finally {
    await request.put(`${api}/settings`, { data: originalSettings });
  }
});

test("defensive analysis pause persists independently of defensive cards", async ({ page, request }) => {
  const api = process.env.TEMPO_BROWSER_API_URL ?? (process.env.TEMPO_DOCKER_URL
    ? `${process.env.TEMPO_DOCKER_URL}/api` : "http://127.0.0.1:8001/api");
  const originalSettings = await (await request.get(`${api}/settings`)).json();
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const analysisToggle = page.getByRole("checkbox", { name: /^Defensive analysis/ });
    const cardsToggle = page.getByRole("checkbox", { name: /Defensive cards in daily stack/ });
    const saveButton = page.getByRole("button", { name: "Save settings" });
    await expect(analysisToggle).not.toBeChecked();
    await analysisToggle.check();
    await cardsToggle.uncheck();
    await saveButton.click();
    await expect.poll(async () => {
      const settings = await (await request.get(`${api}/settings`)).json();
      return [settings.defensive_analysis_enabled, settings.include_defensive_cards_in_daily_stack];
    }).toEqual([true, false]);
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(analysisToggle).toBeChecked();
    await expect(cardsToggle).not.toBeChecked();
    await analysisToggle.uncheck();
    await saveButton.click();
    await expect.poll(async () => (await (await request.get(`${api}/settings`)).json()).defensive_analysis_enabled).toBe(false);
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(analysisToggle).not.toBeChecked();
    await expect(cardsToggle).not.toBeChecked();
  } finally {
    await request.put(`${api}/settings`, { data: originalSettings });
  }
});

test("combined defensive pauses retain individual Resume until Settings allows it", async ({ page, request }, testInfo) => {
  const api = process.env.TEMPO_BROWSER_API_URL ?? (process.env.TEMPO_DOCKER_URL
    ? `${process.env.TEMPO_DOCKER_URL}/api` : "http://127.0.0.1:8001/api");
  assertDisposableTarget(await (await request.get(`${api}/health`)).json());
  const originalSettings = await (await request.get(`${api}/settings`)).json();
  const completeCommand = async (response: APIResponse) => {
    expect(response.ok()).toBe(true);
    const envelope = await response.json();
    if (response.status() !== 202) return envelope;
    let receipt;
    await expect.poll(async () => {
      receipt = await (await request.get(`${api}/operations/${encodeURIComponent(envelope.operation_id)}`)).json();
      return receipt.state;
    }).toBe("complete");
    return receipt.response;
  };
  try {
    await completeCommand(await request.put(`${api}/settings`, { data: { ...originalSettings, defensive_analysis_enabled: false } }));
    const queued = await completeCommand(await request.post(`${api}/defensive-threats/backfill`));
    expect(typeof queued.task_id).toBe("string");
    await completeCommand(await request.post(`${api}/system/activity/control`, { data: {
      source: "durable", id: queued.task_id, action: "pause",
    } }));
    const activityItem = async (): Promise<ActivityItem> => {
      const response = await (await request.get(`${api}/system/activity?limit=100`)).json();
      return response.items.find((item: ActivityItem) => item.source === "durable" && item.id === queued.task_id);
    };
    await expect.poll(activityItem).toMatchObject({ state: "paused", paused: true, paused_by_settings: true });
    await page.setViewportSize({ width: 320, height: 700 });
    await prepareUI(page);
    await page.getByRole("button", { name: "Analysis activity" }).click();
    const work = page.locator(".tempo-activity-item").filter({ hasText: "Defensive Threat Backfill" });
    await expect(work).toContainText("Paused in Settings. Enable Defensive analysis in Settings to allow this work.");
    await expect(work.getByRole("button", { name: "Resume", exact: true })).toHaveCount(0);
    await expect(work.getByRole("button", { name: "Pause", exact: true })).toHaveCount(0);
    await expect(work.getByRole("button", { name: "Prioritize", exact: true })).toBeVisible();
    await noPageOverflow(page);
    await page.locator(".tempo-activity-content").screenshot({ path: testInfo.outputPath("combined-defensive-pauses.png") });
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await navigate(page, "Settings");
    await page.getByRole("checkbox", { name: /^Defensive analysis/ }).check();
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect.poll(async () => (await (await request.get(`${api}/settings`)).json()).defensive_analysis_enabled).toBe(true);
    await expect.poll(activityItem).toMatchObject({ state: "paused", paused: true, paused_by_settings: false });
    await page.getByRole("button", { name: "Analysis activity" }).click();
    await expect(work).not.toContainText("Paused in Settings.");
    await work.getByRole("button", { name: "Resume", exact: true }).click();
    await expect.poll(activityItem).toMatchObject({ paused: false, paused_by_settings: false });
    const settings = await (await request.get(`${api}/settings`)).json();
    expect(settings.defensive_analysis_enabled).toBe(true);
    expect(settings.include_defensive_cards_in_daily_stack).toBe(originalSettings.include_defensive_cards_in_daily_stack);
  } finally {
    await completeCommand(await request.put(`${api}/settings`, { data: originalSettings }));
  }
});
