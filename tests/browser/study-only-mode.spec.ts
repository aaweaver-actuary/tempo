import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { test, expect } from "./observability";
import { api } from "./product-fixtures";
import { assertDisposableTarget } from "./disposable-target";
import { boardBounds, navigate, prepareUI, noPageOverflow } from "./ui-fixtures";

test("study_only_mode_preserves_foreground_review_and_queue_progression across reload and resumption", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  assertDisposableTarget(await (await request.get(`${api}/health`)).json());
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("An owning disposable runner is required");
  const identifier = `training-transition-${randomUUID()}`;
  const compose = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
  const docker = (...args: string[]) => execFileSync("docker", [...compose, ...args], { encoding: "utf8", timeout: 60_000 });
  const fixture = (action: string, ...args: string[]) => JSON.parse(docker("run", "--rm", "--no-deps", "schema", "python",
    "/source/database/tests/check_postgres_training_transition.py", action, identifier, ...args));
  const originalSettings = await (await request.get(`${api}/settings`)).json();
  let displacedPositions: number[][] = [];
  const reviewRequests: { url: string; body: unknown; key: string }[] = [];
  page.on("request", outgoing => {
    if (outgoing.url().endsWith("/review")) reviewRequests.push({ url: outgoing.url(), body: outgoing.postDataJSON(), key: outgoing.headers()["idempotency-key"] });
  });
  try {
    await prepareUI(page);
    await navigate(page, "Settings");
    await expect(page.getByRole("button", { name: "Save settings" })).toBeEnabled();
    await page.getByRole("checkbox", { name: /Study-only mode/ }).check();
    expect((await (await request.get(`${api}/settings`)).json()).study_only_mode).toBe(false);
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect.poll(async () => (await (await request.get(`${api}/settings`)).json()).study_only_mode).toBe(true);
    await noPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("study-only-settings.png") });
    docker("stop", "background-worker", "background-scheduler");
    const today = (await (await request.get(`${api}/queue/window?limit=20`)).json()).local_date;
    displacedPositions = fixture("seed-with-existing-queue", today).displaced_queue_positions;
    docker("start", "background-worker", "background-scheduler");
    await page.reload();
    await navigate(page, "Train");
    await expect(page.getByText(/Study-only mode is on. Optional/)).toBeVisible();
    await page.getByRole("button", { name: "Analysis activity" }).click();
    await expect(page.getByText(/pending work is preserved/)).toBeVisible();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    for (let index = 0; index < 3; index++) {
      const head = (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0];
      expect(head.id).toBe(`${identifier}-prepared-${String(index).padStart(3, "0")}`);
      await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(head.queue_entry_id));
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
      const board = await boardBounds(page);
      if (index === 0) {
        await page.mouse.click(board.x + board.width * 3.5 / 8, board.y + board.height * 6.5 / 8);
        await page.mouse.click(board.x + board.width * 3.5 / 8, board.y + board.height * 4.5 / 8);
        await expect(page.getByText(/Valid repertoire move/)).toBeVisible();
        await expect(page.getByText(/Cannot verify another repertoire move/)).toHaveCount(0);
      }
      await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 6.5 / 8);
      await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 4.5 / 8);
      await expect(page.getByRole("button", { name: "Correct", exact: true })).toBeEnabled();
      await page.getByRole("button", { name: "Correct", exact: true }).click();
      await expect.poll(() => fixture("verify").reviews).toBe(index + 1);
      await page.reload();
    }
    const nextHead = (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0];
    expect(nextHead.id).toBe(`${identifier}-prepared-003`);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(nextHead.queue_entry_id));
    const beforeRestart = fixture("verify");
    expect(beforeRestart).toMatchObject({ reviews: 3, complete_evidence: 3, pending_maintenance: 3000 });
    docker("restart", "foreground-worker", "background-worker", "background-scheduler");
    await expect.poll(async () => (await request.get(`${api}/health`)).ok(), { timeout: 60_000 }).toBe(true);
    const first = reviewRequests[0];
    expect(first).toBeTruthy();
    await request.post(first.url, { headers: { "Idempotency-Key": first.key }, data: first.body });
    await expect.poll(() => fixture("verify").reviews).toBe(3);
    await navigate(page, "Settings");
    await expect(page.getByRole("checkbox", { name: /Study-only mode/ })).toBeChecked();
    await page.getByRole("checkbox", { name: /Study-only mode/ }).uncheck();
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect.poll(async () => (await (await request.get(`${api}/settings`)).json()).study_only_mode).toBe(false);
    await expect.poll(() => fixture("verify").pending_maintenance, { timeout: 60_000 }).toBeLessThan(3000);
  } finally {
    await request.put(`${api}/settings`, { data: originalSettings });
    docker("start", "foreground-worker", "background-worker", "background-scheduler");
    fixture("cleanup", JSON.stringify(displacedPositions));
  }
});
