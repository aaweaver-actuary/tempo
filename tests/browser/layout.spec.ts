import {
  test,
  expect,
  viewports,
  boardWorkspaces,
  navigate,
  prepareUI,
  boardBounds,
  noPageOverflow,
} from "./ui-fixtures";

for (const viewport of viewports) {
  test(`board bounds remain identical across workspace navigation ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await prepareUI(page);
    let reference: Awaited<ReturnType<typeof boardBounds>> | undefined;
    for (const workspace of boardWorkspaces) {
      await navigate(page, workspace);
      await expect
        .poll(async () => {
          const bounds = await boardBounds(page);
          if (!reference) {
            reference = bounds;
            return 0;
          }
          return Math.max(
            ...(["x", "y", "width", "height"] as const).map((axis) =>
              Math.abs(bounds[axis] - reference![axis]),
            ),
          );
        })
        .toBeLessThanOrEqual(1);
      await noPageOverflow(page);
    }
  });
}

test("application navigation retains one Chessground instance", async ({
  page,
}) => {
  await prepareUI(page);
  await page
    .locator(".persistent-board-shell .cg-wrap")
    .evaluate((element) =>
      element.setAttribute("data-original-instance", "true"),
    );
  for (const workspace of [
    ...boardWorkspaces,
    "Settings",
    "Progress",
    "Repertoire",
    "Builder",
  ]) {
    await navigate(page, workspace);
    await expect(
      page.locator(".persistent-board-shell .cg-wrap"),
    ).toHaveAttribute("data-original-instance", "true");
    await expect(page.locator(".cg-wrap")).toHaveCount(1);
  }
});

test("board controls retain consistent placement across workspaces", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await prepareUI(page);
  let reference: number | undefined;
  for (const workspace of boardWorkspaces) {
    await navigate(page, workspace);
    const toolbar = page.locator(".shared-board-toolbar");
    await expect(
      toolbar.getByRole("button", { name: "Flip board", exact: true }),
    ).toBeVisible();
    await expect
      .poll(async () => {
        const bounds = (await toolbar.boundingBox())!;
        reference ??= bounds.y;
        return Math.abs(bounds.y - reference);
      })
      .toBeLessThanOrEqual(1);
  }
});

test("endgame study actions remain reachable on narrow screens", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareUI(page);
  await navigate(page, "Endgames");
  for (const name of ["Edit material", "Win", "Draw"]) {
    const control = page.getByRole("button", { name: new RegExp(name) });
    await control.scrollIntoViewIfNeeded();
    await expect(control).toBeInViewport();
  }
  await page.getByRole("button", { name: /Edit material/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
});

test("desktop workspace panels do not clip controls or content", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await prepareUI(page);
  for (const workspace of ["Builder", "Games", "Endgames"]) {
    await navigate(page, workspace);
    await noPageOverflow(page);
    for (const button of await page
      .locator(".unified-board-shell-panel button:visible")
      .all()) {
      await button.scrollIntoViewIfNeeded();
      const bounds = (await button.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(1281);
    }
  }
});

test("shared split supports keyboard reset and persistence without changing workspace preference", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await prepareUI(page);
  await navigate(page, "Builder");
  const separator = page.getByRole("separator", { name: "Board size" });
  await separator.focus();
  await page.keyboard.press("ArrowRight");
  await expect(separator).toHaveAttribute("aria-valuenow", "48");
  await navigate(page, "Endgames");
  await expect(separator).toHaveAttribute("aria-valuenow", "48");
  await page.reload();
  await expect(separator).toHaveAttribute("aria-valuenow", "48");
  await separator.focus();
  await page.keyboard.press("Home");
  await expect(separator).toHaveAttribute("aria-valuenow", "46");
});

test("tablet section menu stays inside the header and never overlaps the board", async ({
  page,
}) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await prepareUI(page);
  const header = (await page.locator(".topbar").boundingBox())!;
  const menu = (await page.locator(".tablet-navigation").boundingBox())!;
  expect(menu.y).toBeGreaterThanOrEqual(header.y);
  expect(menu.y + menu.height).toBeLessThanOrEqual(header.y + header.height);
});

test("phone board controls provide 44px touch targets with every pointer type", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareUI(page);
  for (const workspace of boardWorkspaces) {
    await navigate(page, workspace);
    const controls = page
      .locator(".board-tools button, .board-tools a")
      .filter({ visible: true });
    for (const control of await controls.all()) {
      const bounds = await control.boundingBox();
      expect(bounds!.width).toBeGreaterThanOrEqual(44);
      expect(bounds!.height).toBeGreaterThanOrEqual(44);
    }
  }
});

test("tactical catalog groups and activation controls remain reachable on phones", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareUI(page);
  await navigate(page, "Tactics");
  await page.getByRole("tab", { name: "Packs", exact: true }).click();
  const catalog = page.getByRole("region", { name: "Tactical puzzle catalog" });
  await expect(catalog).toBeVisible();
  await expect(catalog.getByText("Basic motifs")).toBeVisible();
  const activation = catalog.getByRole("button", {
    name: /Activate Hanging pieces easy pack 1/,
  });
  if (!(await activation.isVisible()))
    await catalog
      .locator(".tactic-theme summary")
      .filter({ hasText: "Hanging pieces" })
      .click();
  await expect(activation).toBeVisible();
  const bounds = await activation.boundingBox();
  expect(bounds!.height).toBeGreaterThanOrEqual(44);
  await activation.click();
  await expect(
    catalog.getByRole("button", {
      name: /Deactivate Hanging pieces easy pack 1/,
    }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".cg-wrap")).toBeVisible();
});
