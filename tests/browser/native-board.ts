import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

export async function boardVisible(
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
  const unavailableShell = page.locator('.persistent-board-shell[data-unavailable="true"]');
  if (await unavailableShell.count()) {
    const unavailableMessage = page.locator(".board-unavailable");
    if (!(await unavailableMessage.isVisible())) {
      // The board may become ready between the shell check and the message check.
      await expect(unavailableShell).toHaveCount(0);
    } else {
      const region = (await page
        .locator(".persistent-board-shell")
        .boundingBox())!;
      expect(region.x + region.width).toBeLessThanOrEqual(
        page.viewportSize()!.width + 1,
      );
      return;
    }
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

export async function move(page: Page, from: string, to: string) {
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
