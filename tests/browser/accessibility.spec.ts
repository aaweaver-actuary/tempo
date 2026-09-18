import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";
import { navigate, noPageOverflow } from "./ui-fixtures";
for (const width of [390, 1280])
  test(`workspace accessibility and reflow ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await prepareVisualUI(page);
    for (const workspace of [
      "Train",
      "Tactics",
      "Endgames",
      "Builder",
      "Games",
      "Repertoire",
      "Progress",
      "Settings",
    ]) {
      await navigate(page, workspace);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      expect(
        results.violations.map((violation) => ({
          id: violation.id,
          nodes: violation.nodes.map((node) => node.target),
        })),
      ).toEqual([]);
      await noPageOverflow(page);
    }
    // A 1280px window at 200% zoom has a 640 CSS-pixel layout viewport.
    await page.setViewportSize({ width: 640, height: 450 });
    await navigate(page, "Settings");
    await noPageOverflow(page);
    const save = page.getByRole("button", { name: "Save settings" });
    await save.scrollIntoViewIfNeeded();
    await expect(save).toBeInViewport();
  });
