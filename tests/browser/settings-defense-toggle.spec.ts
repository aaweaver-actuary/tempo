import { expect, test } from "@playwright/test";

test("settings offer current Study and Game sync without retired engine controls", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save settings" })).toBeEnabled();
  await expect(page.getByRole("spinbutton", { name: /Default initial prefix length/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Defensive/ })).toHaveCount(0);
  await expect(page.getByRole("spinbutton", { name: /New defensive exercises/ })).toHaveCount(0);
  await page.getByRole("tab", { name: "Games" }).click();
  await expect(page.getByRole("region", { name: "Game sync settings" })).toBeVisible();
  await expect(page.getByRole("spinbutton", { name: /Major mistake threshold/ })).toHaveCount(0);
});
