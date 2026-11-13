import { randomUUID } from "node:crypto";
import type { APIResponse } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { test, expect } from "./observability";
import { api } from "./product-fixtures";
import { assertDisposableTarget } from "./disposable-target";
import { boardBounds, navigate, prepareUI, noPageOverflow } from "./ui-fixtures";

type ReceiptResponse = Pick<APIResponse, "status" | "ok" | "json">;
async function confirmedOriginalReview(response: ReceiptResponse, operationId: string,
  readReceipt: (id: string) => Promise<ReceiptResponse>,
  options: { limit?: number; wait?: () => Promise<void> } = {}): Promise<Record<string, unknown>> {
  if (response.status() === 202) {
    const acknowledgement = await response.json();
    if (acknowledgement?.operation_id !== operationId || !["unknown", "queued", "pending", "running", "executing", "retrying"].includes(acknowledgement?.state))
      throw new Error("The original review acknowledgement is missing or foreign");
    for (let i = 0; i < (options.limit ?? 100); i++) {
      const polled = await readReceipt(operationId);
      if (!polled.ok()) throw new Error(`Original review receipt HTTP ${polled.status()}`);
      const receipt = await polled.json();
      if (receipt?.operation_id !== operationId) throw new Error("The original review receipt is missing or foreign");
      if (receipt.state === "complete") return confirmedReviewBody(receipt.response);
      if (!["unknown", "queued", "pending", "running", "executing", "retrying"].includes(receipt.state)) throw new Error("Original review did not complete");
      await (options.wait?.() ?? new Promise(resolve => setTimeout(resolve, 200)));
    }
    throw new Error("Original review receipt remained pending");
  }
  if (!response.ok()) throw new Error(`Original review HTTP ${response.status()}`);
  return confirmedReviewBody(await response.json());
}
function confirmedReviewBody(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object") throw new Error("Malformed original review result");
  const result = value as Record<string, unknown>;
  if (result.persisted !== true || !Number.isInteger(result.review_id) || Number(result.review_id) <= 0)
    throw new Error("Original review did not confirm persistence");
  return result;
}

test("original review replay validator rejects failure and foreign receipts", async () => {
  const reply = (status: number, value: unknown): ReceiptResponse => ({ status: () => status, ok: () => status >= 200 && status < 300, json: async () => value });
  const good = {persisted:true, review_id:7};
  for (const status of [409, 500]) await expect(confirmedOriginalReview(reply(status, {}), "original", async () => reply(500, {}))).rejects.toThrow();
  await expect(confirmedOriginalReview(reply(202, {operation_id:"foreign", state:"queued"}), "original", async () => reply(200, {}))).rejects.toThrow();
  await expect(confirmedOriginalReview(reply(202, {operation_id:"original", state:"queued"}), "original", async () => reply(200, {operation_id:"foreign", state:"complete", response:good}))).rejects.toThrow();
  await expect(confirmedOriginalReview(reply(202, {operation_id:"original", state:"queued"}), "original", async () => reply(200, {operation_id:"original", state:"complete", response:{persisted:true}}))).rejects.toThrow();
  expect(await confirmedOriginalReview(reply(202, {operation_id:"original", state:"queued"}), "original", async () => reply(200, {operation_id:"original", state:"complete", response:good}))).toEqual(good);
  let reads = 0;
  const unknown = reply(202, {operation_id:"original", state:"unknown"});
  const options = {limit: 2, wait: async () => {}};
  expect(await confirmedOriginalReview(unknown, "original", async () => reply(200, ++reads === 1
    ? {operation_id:"original", state:"unknown"} : {operation_id:"original", state:"complete", response:good}), options)).toEqual(good);
  await expect(confirmedOriginalReview(unknown, "original", async () => reply(200, {operation_id:"original", state:"unknown"}), options)).rejects.toThrow("remained pending");
  let executingReads = 0;
  expect(await confirmedOriginalReview(reply(202, {operation_id:"original", state:"executing"}), "original", async () => reply(200, ++executingReads === 1
    ? {operation_id:"original", state:"executing"} : {operation_id:"original", state:"complete", response:good}), options)).toEqual(good);
  await expect(confirmedOriginalReview(reply(202, {operation_id:"original", state:"executing"}), "original", async () => reply(200, {operation_id:"original", state:"executing"}), options)).rejects.toThrow("remained pending");
});

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
  let first: { url: string; body: Record<string, unknown>; key: string; result: Record<string, unknown>; cardId: string; revision: number } | undefined;
  const readReceipt = (id: string) => request.get(`${api}/operations/${encodeURIComponent(id)}`);
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
    await expect(page.getByText(/Study-only mode pauses optional analysis and imports/)).toBeVisible();
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
      const observedReview = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/cards/${head.id}/review`));
      await page.getByRole("button", { name: "Correct", exact: true }).click();
      const originalResponse = await observedReview;
      if (index === 0) {
        const originalRequest = originalResponse.request();
        const key = originalRequest.headers()["idempotency-key"];
        const body = originalRequest.postDataJSON() as Record<string, unknown>;
        expect(key).toBeTruthy();
        expect(body.attempt_id).toBeTruthy();
        expect(body.queue_entry_id).toBe(head.queue_entry_id);
        expect(body.expected_revision).toBe(head.revision);
        const result = await confirmedOriginalReview(originalResponse, key, readReceipt);
        first = {url: originalResponse.url(), body, key, result, cardId: head.id, revision: head.revision};
      }
      await expect.poll(() => fixture("verify").reviews).toBe(index + 1);
      await page.reload();
    }
    const nextHead = (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0];
    expect(nextHead.id).toBe(`${identifier}-prepared-003`);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(nextHead.queue_entry_id));
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    const finalBoard = await boardBounds(page);
    await page.mouse.click(finalBoard.x + finalBoard.width * 4.5 / 8, finalBoard.y + finalBoard.height * 6.5 / 8);
    await page.mouse.click(finalBoard.x + finalBoard.width * 4.5 / 8, finalBoard.y + finalBoard.height * 4.5 / 8);
    await expect(page.getByRole("button", { name: "Correct", exact: true })).toBeEnabled();
    const beforeRestart = fixture("verify");
    expect(beforeRestart).toMatchObject({ reviews: 3, complete_evidence: 3 });
    docker("restart", "foreground-worker", "background-worker", "background-scheduler");
    await expect.poll(async () => (await request.get(`${api}/health`)).ok(), { timeout: 60_000 }).toBe(true);
    if (!first) throw new Error("Original review was not observed");
    const original = first;
    const replay = await request.post(original.url, { headers: { "Idempotency-Key": original.key }, data: original.body });
    expect(await confirmedOriginalReview(replay, original.key, readReceipt)).toEqual(original.result);
    const afterRestart = fixture("verify");
    expect(afterRestart.attempts).toEqual(beforeRestart.attempts);
    expect(afterRestart.evidence).toEqual(beforeRestart.evidence);
    expect(afterRestart.completion_events).toBe(beforeRestart.completion_events);
    expect(afterRestart.attempts).toHaveLength(3);
    expect(new Set(afterRestart.attempts.map((item: { attempt_id: string }) => item.attempt_id)).size).toBe(3);
    expect(new Set(afterRestart.attempts.map((item: { review_id: number }) => item.review_id)).size).toBe(3);
    expect(afterRestart.evidence).toHaveLength(3);
    for (const attempt of afterRestart.attempts) expect(afterRestart.evidence).toContainEqual([attempt.attempt_id, attempt.card_id, attempt.review_id]);
    expect(afterRestart.reviews).toBe(3);
    expect(afterRestart.complete_evidence).toBe(3);
    const originalReceipt = afterRestart.attempts.find((item: { attempt_id: string }) => item.attempt_id === original.body.attempt_id);
    expect(originalReceipt).toMatchObject({ card_id: original.cardId, queue_entry_id: original.body.queue_entry_id,
      review_id: original.result.review_id, request: { attempt_id: original.body.attempt_id, expected_revision: original.revision }, result: original.result });
    await navigate(page, "Settings");
    await expect(page.getByRole("checkbox", { name: /Study-only mode/ })).toBeChecked();
    await page.getByRole("checkbox", { name: /Study-only mode/ }).uncheck();
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect.poll(async () => (await (await request.get(`${api}/settings`)).json()).study_only_mode).toBe(false);
  } finally {
    await request.put(`${api}/settings`, { data: originalSettings });
    docker("start", "foreground-worker", "background-worker", "background-scheduler");
    fixture("cleanup", JSON.stringify(displacedPositions));
  }
});
