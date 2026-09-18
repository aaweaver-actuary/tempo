import { assertDisposableTarget } from "./disposable-target";
import { navigate } from "./ui-fixtures";
import type { Page } from "@playwright/test";
import { test as base, expect } from "./observability";
import { Chess } from "chess.js";

const api = process.env.TEMPO_DOCKER_URL
  ? `${process.env.TEMPO_DOCKER_URL}/api`
  : "http://127.0.0.1:8001/api";
const pgn =
  '[Event "My repertoire"]\n\n1. e4 e5 2. Nf3 Nc6 (2... Nf6) *\n\n[Event "Other line"]\n\n1. d4 d5 2. c4 e6 *';

async function nav(page: Page, name: string) {
  await navigate(page, name);
}
async function assertSharedBoardShellLayout(page: Page, mobile: boolean) {
  const shell = page.locator(".unified-board-shell-layout").first();
  await expect(shell).toBeVisible();
  const boardRegion = shell.locator(".persistent-board-shell").first();
  const panelRegion = shell.locator(".unified-board-shell-panel").first();
  await expect(boardRegion).toBeVisible();
  await expect(panelRegion).toBeVisible();
  const [boardBox, panelBox] = await Promise.all([
    boardRegion.boundingBox(),
    panelRegion.boundingBox(),
  ]);
  expect(boardBox).not.toBeNull();
  expect(panelBox).not.toBeNull();
  if (!boardBox || !panelBox) return;
  if (mobile)
    expect(boardBox.y + boardBox.height).toBeLessThanOrEqual(panelBox.y + 2);
  else expect(boardBox.x + boardBox.width).toBeLessThanOrEqual(panelBox.x + 2);
}
async function boardVisible(
  page: Page,
  options: { requireControlsInViewport?: boolean } = {},
) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  const board = page.locator(".board-frame").first();
  if (
    await page
      .locator('.persistent-board-shell[data-unavailable="true"]')
      .count()
  ) {
    await expect(page.locator(".board-unavailable")).toBeVisible();
    const region = (await page
      .locator(".persistent-board-shell")
      .boundingBox())!;
    expect(region.x + region.width).toBeLessThanOrEqual(
      page.viewportSize()!.width + 1,
    );
    return;
  }
  await expect(board).toBeVisible();
  await expect(board.locator("piece.anim")).toHaveCount(0);
  const box = await board.boundingBox();
  const viewport = page.viewportSize()!;
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  if (viewport.width >= 1100 && viewport.height >= 600)
    expect(box!.y).toBeGreaterThanOrEqual(65);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
  if (viewport.width >= 1100 && viewport.height >= 600)
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
  expect(Math.abs(box!.width - box!.height)).toBeLessThan(2);
  const surface = await board.locator(".cg-wrap").boundingBox();
  const squares = await board.locator("cg-board").boundingBox();
  expect(surface).not.toBeNull();
  expect(squares).not.toBeNull();
  for (const axis of ["x", "y", "width", "height"] as const)
    expect(Math.abs(surface![axis] - squares![axis])).toBeLessThan(0.6);
  expect(Math.abs(surface!.width - surface!.height)).toBeLessThan(0.6);
  // A Black prompt may start its automatic reply between separate DOM reads.
  // Sample surface and pieces together, and assert the settled geometry rather
  // than an interpolated animation frame.
  await expect
    .poll(() =>
      board.locator(".cg-wrap").evaluate((node) => {
        const bounds = node.getBoundingClientRect();
        return [...node.querySelectorAll("cg-board piece:not(.ghost)")].every(
          (piece) => {
            const b = piece.getBoundingClientRect();
            const file = (b.x - bounds.x) / (bounds.width / 8),
              rank = (b.y - bounds.y) / (bounds.height / 8);
            return (
              !piece.classList.contains("anim") &&
              Math.abs(b.width - bounds.width / 8) < 0.6 &&
              Math.abs(b.height - bounds.height / 8) < 0.6 &&
              Math.abs(file - Math.round(file)) < 0.02 &&
              Math.abs(rank - Math.round(rank)) < 0.02 &&
              file >= -0.02 &&
              file <= 7.02 &&
              rank >= -0.02 &&
              rank <= 7.02
            );
          },
        );
      }),
    )
    .toBe(true);
  const controls = page.locator(".board-tools").first();
  if ((options.requireControlsInViewport ?? true) && (await controls.count())) {
    if (viewport.width < 1100 || viewport.height < 600)
      await controls.scrollIntoViewIfNeeded();
    const controlsBox = (await controls.boundingBox())!;
    expect(controlsBox.y + controlsBox.height).toBeLessThanOrEqual(
      viewport.height + 1,
    );
  }
}

async function move(page: Page, from: string, to: string) {
  const board = page.locator(".board-frame").first();
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  await boardVisible(page);
  const black = (await board.getAttribute("data-orientation")) === "black";
  for (const square of [from, to]) {
    const box = (await board.locator(".cg-wrap").boundingBox())!;
    const file = square.charCodeAt(0) - 97,
      rank = Number(square[1]) - 1;
    await page.mouse.click(
      box.x + (((black ? 7 - file : file) + 0.5) * box.width) / 8,
      box.y + (((black ? rank : 7 - rank) + 0.5) * box.height) / 8,
    );
  }
}

async function clickSquare(
  page: Page,
  square: string,
  button: "left" | "right" = "left",
) {
  const board = page.locator(".board-frame").first();
  await boardVisible(page);
  const black = (await board.getAttribute("data-orientation")) === "black";
  const box = (await board.locator(".cg-wrap").boundingBox())!;
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  const x = box.x + (((black ? 7 - file : file) + 0.5) * box.width) / 8;
  const y = box.y + (((black ? rank : 7 - rank) + 0.5) * box.height) / 8;
  await page.mouse.move(x, y);
  await page.mouse.down({ button });
  // Chessground resolves the drawn square on its animation frame before release.
  if (button === "right")
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    );
  await page.mouse.up({ button });
}

const test = base.extend<{ disposableProduct: void }>({
  disposableProduct: [
    async ({ request }, use) => {
      const health = await request.get(`${api}/health`);
      assertDisposableTarget(health.ok() ? await health.json() : null);
      const repertoires = (
        await (await request.get(`${api}/repertoires`)).json()
      ).repertoires;
      for (const item of repertoires)
        await request.delete(`${api}/repertoires/${item.id}`);
      const queue = (await (await request.get(`${api}/queue/today`)).json())
        .cards;
      for (const card of queue)
        if (card.content_type !== "opening")
          await request.delete(`${api}/cards/${card.id}`);
      const settings = await (await request.get(`${api}/settings`)).json();
      await request.put(`${api}/settings`, {
        data: {
          ...settings,
          new_cards_per_day: 2,
          initial_depth: 2,
          lichess_username: "",
          chesscom_username: "",
        },
      });
      await use();
    },
    { auto: true },
  ],
});

export {
  test,
  expect,
  Chess,
  api,
  pgn,
  nav,
  boardVisible,
  assertSharedBoardShellLayout,
  move,
  clickSquare,
};
