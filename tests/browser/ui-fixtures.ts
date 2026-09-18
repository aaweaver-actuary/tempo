import type { Page } from "@playwright/test";
import { test as base, expect } from "./observability";
export const test = base;
export { expect };
export const boardWorkspaces = [
  "Train",
  "Tactics",
  "Endgames",
  "Builder",
  "Games",
];
export const viewports = [
  { width: 320, height: 568 },
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
  { width: 767, height: 844 },
  { width: 769, height: 844 },
  { width: 1099, height: 800 },
  { width: 1100, height: 800 },
  { width: 1101, height: 800 },
];
export async function navigate(page: Page, name: string) {
  const navigation = page.getByRole("navigation", {
    name: "Primary navigation",
  });
  const direct = navigation
    .getByRole("button", { name, exact: true })
    .filter({ visible: true });
  if (!(await direct.count())) {
    const menu = navigation
      .getByRole("button", { name: "More", exact: true })
      .filter({ visible: true });
    if (await menu.count()) await menu.click();
    else await page.locator(".tablet-navigation").click();
  }
  await navigation
    .getByRole("button", { name, exact: true })
    .filter({ visible: true })
    .click();
}
export async function prepareUI(page: Page) {
  await page.addInitScript(() => {
    for (const key of [
      "tempo-stockfish-on",
      "tempo-maia-on",
      "tempo-games-engine-on",
      "tempo-explorer-on",
    ])
      localStorage.setItem(key, "false");
  });
  await page.route("https://tablebase.lichess.ovh/**", (route) =>
    route.fulfill({ json: { category: "win", moves: [] } }),
  );
  await page.route("**/api/endgames/probe", (route) =>
    route.fulfill({ json: { category: "win", moves: [] } }),
  );
  await page.goto("/");
}
export async function noPageOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
}
export async function boardBounds(page: Page) {
  const board = page.locator(".persistent-board-shell .board-viewport");
  await expect(board).toBeAttached();
  return (await board.boundingBox())!;
}
