import type { Page } from "@playwright/test";
import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";
import { navigate, noPageOverflow } from "./ui-fixtures";
async function squareCenter(page: Page, square: string) {
  const board = page.locator(".cg-wrap");
  await board.scrollIntoViewIfNeeded();
  const bounds = (await board.boundingBox())!;
  const black =
    (await page.locator(".board-frame").getAttribute("data-orientation")) ===
    "black";
  const file = square.charCodeAt(0) - 97,
    rank = Number(square[1]) - 1;
  return {
    x: bounds.x + (((black ? 7 - file : file) + 0.5) * bounds.width) / 8,
    y: bounds.y + (((black ? rank : 7 - rank) + 0.5) * bounds.height) / 8,
  };
}
for (const deviceScaleFactor of [1, 2])
  test(`touch board input and rotation preserve legal position at DPR ${deviceScaleFactor}`, async ({
    browser,
  }) => {
    const context = await browser.newContext({
      hasTouch: true,
      deviceScaleFactor,
      viewport: { width: 390, height: 844 },
      baseURL: process.env.TEMPO_BROWSER_URL ?? process.env.TEMPO_DOCKER_URL ?? "http://127.0.0.1:3001",
    });
    try {
      const page = await context.newPage();
      await prepareVisualUI(page);
      await navigate(page, "Train");
      await expect(page.locator(".board-frame")).toHaveAttribute(
        "data-input-enabled",
        "true",
      );
      for (const square of ["e2", "e5"]) {
        const point = await squareCenter(page, square);
        await page.touchscreen.tap(point.x, point.y);
      }
      await expect(page.locator(".board-frame")).toHaveAttribute(
        "data-fen",
        /PPPPPPPP/,
      );
      for (const square of ["e2", "e4"]) {
        const point = await squareCenter(page, square);
        await page.touchscreen.tap(point.x, point.y);
      }
      await expect(page.locator(".board-frame")).toHaveAttribute(
        "data-fen",
        /4P3/,
      );
      const fen = await page.locator(".board-frame").getAttribute("data-fen");
      await page.setViewportSize({ width: 844, height: 390 });
      await noPageOverflow(page);
      await expect(page.locator(".board-frame")).toHaveAttribute(
        "data-fen",
        fen!,
      );
    } finally {
      await context.close();
    }
  });
