import { expect, test } from "@playwright/test";

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
