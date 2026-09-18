import {
  test,
  expect,
  nav,
  boardVisible,
  assertSharedBoardShellLayout,
} from "./product-fixtures";
test("high-DPI board geometry stays aligned through narrow resize and orientation flips", async ({
  browser,
}) => {
  const context = await browser.newContext({
    deviceScaleFactor: 2,
    viewport: { width: 1280, height: 800 },
    baseURL: process.env.TEMPO_DOCKER_URL ?? "http://127.0.0.1:3001",
  });
  const page = await context.newPage();
  await page.addInitScript(() => {
    localStorage.setItem("tempo-stockfish-on", "false");
    localStorage.setItem("tempo-maia-on", "false");
  });
  await page.goto("/");
  await nav(page, "Builder");
  for (const viewport of [
    { width: 1280, height: 800 },
    { width: 390, height: 844 },
    { width: 768, height: 600 },
  ]) {
    await page.setViewportSize(viewport);
    await boardVisible(page);
    const orientation = await page
      .locator(".board-frame")
      .getAttribute("data-orientation");
    await page.keyboard.press("f");
    await expect(page.locator(".board-frame")).not.toHaveAttribute(
      "data-orientation",
      orientation!,
    );
    await boardVisible(page);
  }
  await context.close();
});

test("shared board shell keeps board region fixed left on desktop and top on mobile across board workspaces", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem("tempo-stockfish-on", "false");
    localStorage.setItem("tempo-maia-on", "false");
  });
  await page.goto("/");
  for (const view of ["Train", "Builder", "Games", "Tactics", "Endgames"]) {
    await nav(page, view);
    await page.evaluate(() => window.scrollTo(0, 0));
    await boardVisible(page, { requireControlsInViewport: false });
    await assertSharedBoardShellLayout(page, false);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  for (const view of ["Train", "Builder", "Games", "Tactics", "Endgames"]) {
    await nav(page, view);
    await page.evaluate(() => window.scrollTo(0, 0));
    await boardVisible(page, { requireControlsInViewport: false });
    await assertSharedBoardShellLayout(page, true);
  }
});
