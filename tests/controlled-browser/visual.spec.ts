import { test, expect, prepareControlledUI, openingCard } from "./fixtures";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(import.meta.url);
for (const [title, viewport, menu] of [
  ["Controlled visual board 1280", { width: 1280, height: 900 }, false],
  ["Controlled visual phone 390", { width: 390, height: 844 }, false],
  ["Controlled visual tablet menu 768", { width: 768, height: 1024 }, true],
] as const) {
  test(title, async ({ page }) => {
    expect(process.platform, "Reviewed snapshots require macOS ARM64").toBe("darwin");
    expect(process.arch).toBe("arm64");
    expect(require("@playwright/test/package.json").version).toBe("1.63.0");
    const revisions = JSON.parse(readFileSync(require.resolve("playwright-core/package.json").replace(/package\.json$/, "browsers.json"), "utf8"));
    expect(revisions.browsers.find((browser: {name: string}) => browser.name === "chromium").revision).toBe("1243");
    await page.setViewportSize(viewport);
    await prepareControlledUI(page, [openingCard("london", "London System", "d2d4", 1)]);
    await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
    await expect(page.getByRole("heading", { name: "London System", exact: true })).toBeVisible();
    await expect(page.locator(".board-frame piece")).toHaveCount(32);
    await page.evaluate(() => document.fonts.ready);
    if (menu) {
      await page.locator(".tablet-navigation").click();
      await expect(page.locator("#workspace-menu")).toBeVisible();
    }
    await expect(page).toHaveScreenshot(`${title.toLowerCase().replaceAll(" ", "-")}.png`, { animations: "disabled", maxDiffPixels: 0 });
  });
}
