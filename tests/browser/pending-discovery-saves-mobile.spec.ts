import type { Locator, Page } from "@playwright/test";
import { test, expect, noPageOverflow } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import type { PendingDiscoveryAdmission } from "../../app/lib/discovery-admission-outbox";

const storageKey = "tempo-pending-discovery-admissions-v1";
const records: PendingDiscoveryAdmission[] = ["a", "b", "c", "d"].map((digit, index) => ({
  opportunityId: digit.repeat(64),
  selectedMoveUci: "g1f3",
  evidenceFingerprint: `mobile-overlay-evidence-${index}`,
  operationId: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
  state: "failed",
  error: "'Active discovery not found'",
}));

async function expectHitTarget(locator: Locator) {
  await locator.scrollIntoViewIfNeeded();
  await expect(locator).toBeInViewport();
  expect(await locator.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const hit = document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    return hit !== null && element.contains(hit);
  })).toBe(true);
}

async function expectStudyUsable(page: Page) {
  const board = page.locator(".persistent-board-shell .board-viewport");
  await expect(board).toBeVisible();
  // Preserve existing notification behavior: allow its transient toasts to expire naturally.
  await expect(page.locator(".notification-toast")).toHaveCount(0, { timeout: 10000 });
  await expectHitTarget(board);
  const toolbar = page.locator(".shared-board-toolbar");
  await toolbar.getByRole("button", { name: "Flip board", exact: true }).click({ trial: true });
  await toolbar.getByRole("button", { name: /Show move/ }).click({ trial: true });
  await expectHitTarget(page.getByRole("status").filter({ hasText: "Study-only mode is on." }));
  await page.locator(".phone-navigation").getByRole("button", { name: "Train", exact: true }).click({ trial: true });
  await noPageOverflow(page);
}

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }]) {
  test(`retained failed discovery saves protect Study, scroll on demand, and survive reload at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const acceptancePosts: string[] = [];
    const reviewPosts: string[] = [];
    page.on("request", request => {
      if (request.method() !== "POST") return;
      const path = new URL(request.url()).pathname;
      if (/\/api\/discoveries\/[^/]+\/accept$/.test(path)) acceptancePosts.push(path);
      if (/\/review$/.test(path)) reviewPosts.push(path);
    });
    await prepareVisualUI(page);
    // Override only settings; use the complete local application's existing visual fixtures.
    await page.route("**/api/settings", async route => {
      await route.fulfill({ json: { initial_depth: 6, timezone: "America/New_York",
        new_cards_per_day: 10, study_only_mode: true } });
    });
    // Seed once in the loaded origin. No init script can silently restore deleted records.
    await page.evaluate(({ key, saved }) => localStorage.setItem(key, JSON.stringify(saved)), { key: storageKey, saved: records });
    await page.reload();
    const readSaved = () => page.evaluate(key => localStorage.getItem(key), storageKey);
    const originalSaved = JSON.stringify(records);
    const disclosure = page.locator("details.pending-discovery-saves");
    const trigger = disclosure.locator("summary");
    const panel = page.getByRole("region", { name: "Saved discovery requests" });
    await expect(trigger).toHaveText("Saves (4)");
    await expect(trigger).toHaveAccessibleName("Saved discovery requests");
    await expect(disclosure).not.toHaveAttribute("open");
    await expect(panel).toBeHidden();
    await expect(page.getByText(records[0].opportunityId, { exact: true })).toBeHidden();
    const triggerBounds = (await trigger.boundingBox())!;
    expect(triggerBounds.x).toBeGreaterThanOrEqual(0);
    expect(triggerBounds.x + triggerBounds.width).toBeLessThanOrEqual(viewport.width);
    expect(triggerBounds.y + triggerBounds.height).toBeLessThanOrEqual(56);
    await expectStudyUsable(page);
    const frame = page.locator(".board-frame").first();
    const originalFen = await frame.getAttribute("data-fen");
    const originalQueueEntry = await page.locator("#train").getAttribute("data-queue-entry-id");
    await page.locator(".cg-wrap").first().evaluate(element => element.setAttribute("data-discovery-test-instance", "original"));

    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(disclosure).toHaveAttribute("open", "");
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("listitem")).toHaveCount(4);
    await expect(panel.getByRole("alert")).toHaveCount(4);
    const panelBounds = (await panel.boundingBox())!;
    const navBounds = (await page.locator(".phone-navigation").boundingBox())!;
    expect(panelBounds.x).toBeGreaterThanOrEqual(8);
    expect(panelBounds.x + panelBounds.width).toBeLessThanOrEqual(viewport.width - 8);
    expect(panelBounds.y).toBeGreaterThanOrEqual(56);
    expect(panelBounds.y + panelBounds.height).toBeLessThanOrEqual(navBounds.y);
    for (const record of records) {
      const identifier = panel.getByText(record.opportunityId, { exact: true });
      await expect(identifier).toBeVisible();
      const row = panel.getByRole("listitem").filter({ hasText: record.opportunityId });
      await expect(row.locator("strong")).toHaveText("failed");
      await expect(row).toContainText("move g1f3");
      await expect(row.getByRole("alert")).toHaveText(record.error!);
      expect(await identifier.evaluate(element => {
        const range = document.createRange();
        range.selectNodeContents(element);
        const lines = Array.from(range.getClientRects());
        return lines.length > 1 && lines.every(line => line.left >= 8 && line.right <= innerWidth - 8);
      })).toBe(true);
    }
    expect(await panel.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    const documentScroll = await page.evaluate(() => scrollY);
    await panel.getByRole("button", { name: "Retry save" }).last().scrollIntoViewIfNeeded();
    expect(await panel.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    expect(await page.evaluate(() => scrollY)).toBe(documentScroll);
    await panel.getByRole("button", { name: "Retry save" }).last().click({ trial: true });
    await page.locator(".phone-navigation").getByRole("button", { name: "Train", exact: true }).click({ trial: true });
    await noPageOverflow(page);
    await trigger.focus();
    await page.keyboard.press("Space");
    await expect(disclosure).not.toHaveAttribute("open");
    await expectStudyUsable(page);
    await trigger.click();
    await expect(panel.getByRole("listitem")).toHaveCount(4);
    await trigger.click();
    expect(await readSaved()).toBe(originalSaved);
    await expect(frame).toHaveAttribute("data-fen", originalFen!);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", originalQueueEntry!);
    await expect(page.locator(".cg-wrap").first()).toHaveAttribute("data-discovery-test-instance", "original");
    // Include more than one existing recovery interval before checking for unsolicited retries.
    await page.clock.install();
    await page.clock.runFor(6500);
    expect(acceptancePosts).toEqual([]);
    expect(reviewPosts).toEqual([]);
    await page.reload();
    await expect(trigger).toHaveText("Saves (4)");
    await expect(disclosure).not.toHaveAttribute("open");
    await expect(panel).toBeHidden();
    expect(await readSaved()).toBe(originalSaved);
    await expectStudyUsable(page);
    await page.clock.runFor(6500);
    expect(acceptancePosts).toEqual([]);
    expect(reviewPosts).toEqual([]);
    expect(await readSaved()).toBe(originalSaved);
  });
}
