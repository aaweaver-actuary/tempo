import { expect, test } from "@playwright/test";

test("defensive daily stack setting persists across navigation and reload", async ({ page, request }) => {
  const api = process.env.TEMPO_DOCKER_URL
    ? `${process.env.TEMPO_DOCKER_URL}/api`
    : "http://127.0.0.1:8001/api";
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
    await expect(page.locator(".settings-status")).toHaveText("Saved.");
    await expect.poll(async () =>
      (await (await request.get(`${api}/settings`)).json()).include_defensive_cards_in_daily_stack,
    ).toBe(false);
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(toggle).not.toBeChecked();
  } finally {
    await request.put(`${api}/settings`, { data: originalSettings });
  }
});
