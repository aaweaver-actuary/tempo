import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";
import { move } from "./native-board";
import { readFile, writeFile, rename } from "node:fs/promises";
import { dirname, join } from "node:path";
import type { Response } from "@playwright/test";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const priorityReason = "Priority review · missed in a recent game";

test("previously studied game-miss priority card starts without a teaching arrow", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: { cards: [{
    id: "previously-studied-miss", queue_entry_id: 2, start_fen: startFen,
    moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
    repertoire_name: "Spanish opening", repertoire_source: "PGN",
    first_correct_at: null, has_study_review: 1, trained_color: "white",
    gameplay_priority_reason: priorityReason,
  }] } }));
  await page.reload();
  await expect(page.getByText(priorityReason)).toBeVisible();
  await expect(page.locator(".cg-wrap svg.cg-shapes > g > g[cgHash]")).toHaveCount(0);
});

test("connected completion fetches a new real-game obligation before cached ordinary work", async ({ page }) => {
  await prepareVisualUI(page);
  let saved = false;
  let releaseReview: (() => void) | undefined;
  const card = (id: string, entry: number, title: string) => ({
    id, queue_entry_id: entry, revision: 1, start_fen: startFen, moves: ["e2e4"],
    content_type: "opening", repertoire_name: title, repertoire_source: "PGN", trained_color: "white",
  });
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
    count: 2, cards: saved ? [
      { ...card("missed-decision", 303, "Missed decision"), gameplay_priority_reason: priorityReason },
      card("ordinary-next", 302, "Ordinary next"),
    ] : [card("retained-active", 301, "Active attempt"), card("ordinary-next", 302, "Ordinary next")],
  } }));
  await page.route("**/api/cards/retained-active/review", async route => {
    await new Promise<void>(resolve => { releaseReview = resolve; });
    saved = true;
    await route.fulfill({ json: { persisted: true } });
  });
  await page.reload();
  await expect(page.getByText("Active attempt", { exact: true })).toBeVisible();
  const board = page.locator(".board-frame");
  const fen = await board.getAttribute("data-fen");
  await page.getByRole("button", { name: "Correct", exact: true }).click();
  await expect.poll(() => Boolean(releaseReview)).toBe(true);
  await expect(page.getByText("Ordinary next", { exact: true })).toHaveCount(0);
  await expect(board).toHaveAttribute("data-fen", fen!);
  releaseReview?.();
  await expect(page.getByText("Missed decision", { exact: true })).toBeVisible();
  await expect(page.getByText(priorityReason)).toBeVisible();
  await expect(page.getByText("Ordinary next", { exact: true })).toHaveCount(0);
});

test("packaged first-departure feedback uses real browser saves and issuance", async ({ page }, testInfo) => {
  const scenarioPath = process.env.TEMPO_PACKAGED_FEEDBACK_SCENARIO;
  test.skip(!scenarioPath, "Requires the owned packaged feedback fixture");
  if (!scenarioPath) throw new Error("Owned packaged feedback scenario is required");
  const scenario = JSON.parse(await readFile(scenarioPath, "utf8")) as {
    version: number; protected_card_id: string; protected_queue_entry_id: number; target_card_id: string;
  };
  expect(scenario.version).toBe(1);
  const directory = dirname(scenarioPath);
  const reviews: Array<{ card_id: string; identity: string; body: Record<string, unknown>;
    response: Record<string, unknown>; replay: Record<string, unknown> }> = [];
  const refreshRequests: Array<Record<string, unknown>> = [];
  const network: Array<{ sequence: number; kind: string; status: number; url: string; body: unknown }> = [];
  const consoleErrors: string[] = [], pageErrors: string[] = [];
  const pending = new Set<Promise<void>>();
  let sequence = 0;
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", error => pageErrors.push(error.message));
  page.on("response", response => {
    const path = new URL(response.url()).pathname;
    const kind = path === "/api/queue/prepare-next" ? "prepare"
      : path === "/api/queue/window" ? "queue" : path.includes("/review") ? "review" : null;
    if (!kind) return;
    const observedSequence = ++sequence;
    const task = (async () => {
      let body: unknown;
      try { body = await response.json(); } catch { body = { invalid_json: true }; }
      network.push({ sequence: observedSequence, kind, status: response.status(), url: response.url(), body });
    })();
    pending.add(task);
    void task.finally(() => pending.delete(task));
  });
  async function browserReady(value: Record<string, unknown>) {
    const path = join(directory, "browser-ready.json"), temporary = path + ".next";
    await writeFile(temporary, JSON.stringify(value));
    await rename(temporary, path);
  }
  async function providerReady(stage: string) {
    await expect.poll(async () => {
      try {
        const value = JSON.parse(await readFile(join(directory, "provider-ready.json"), "utf8"));
        if (value.stage === "abort") throw new Error("Owned provider fixture aborted");
        return value.stage;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return "pending";
        throw error;
      }
    }, { timeout: 45_000 }).toBe(stage);
  }
  async function confirmed(status: number, value: Record<string, unknown>) {
    expect([200, 202]).toContain(status);
    if (status === 200) return value;
    let receipt: { state: string; response?: Record<string, unknown> } | undefined;
    await expect.poll(async () => {
      receipt = await page.evaluate(async operation => {
        const response = await fetch(`/api/operations/${encodeURIComponent(operation)}`);
        if (!response.ok) throw new Error(`Actual operation read failed: ${response.status}`);
        return response.json();
      }, String(value.operation_id));
      return receipt?.state;
    }, { timeout: 45_000 }).toBe("complete");
    return receipt!.response!;
  }
  async function playAndSave(cardId: string) {
    const received = page.waitForResponse(response => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/cards/${cardId}/review`);
    await move(page, "e2", "e4");
    const response: Response = await received;
    const request = response.request(), body = request.postDataJSON() as Record<string, unknown>;
    const identity = request.headers()["idempotency-key"];
    expect(identity).toBeTruthy();
    expect(identity).toBe(`review-attempt:${String(body.attempt_id)}`);
    const saved = await confirmed(response.status(), await response.json());
    expect(saved.persisted).toBe(true);
    // Replay the exact browser request against the same real server. The test
    // never creates a review, issuance or receipt response itself.
    const repeated = await page.evaluate(async original => {
      const response = await fetch(original.url, { method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": original.identity },
        body: JSON.stringify(original.body) });
      return { status: response.status, value: await response.json() };
    }, { url: request.url(), identity, body });
    const replay = await confirmed(repeated.status, repeated.value);
    expect(replay).toEqual(saved);
    const review = { card_id: cardId, identity, body, response: saved, replay };
    reviews.push(review);
    return review;
  }
  try {
    await page.setViewportSize({ width: 1280, height: 844 });
    await page.addInitScript(() => localStorage.setItem("tempo-sound", "false"));
    await page.goto("/");
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(scenario.protected_queue_entry_id));
    const board = page.locator(".board-frame").first(), protectedFen = await board.getAttribute("data-fen");
    await browserReady({ stage: "protected", entry_id: scenario.protected_queue_entry_id });
    await providerReady("source");
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(scenario.protected_queue_entry_id));
    await expect(board).toHaveAttribute("data-fen", protectedFen!);
    const protectedReview = await playAndSave(scenario.protected_card_id);
    expect(protectedReview.body.guided).toBe(false);
    await expect.poll(() => page.locator("#train").getAttribute("data-queue-entry-id"),
      { timeout: 45_000 }).not.toBe(String(scenario.protected_queue_entry_id));
    await expect(board).toHaveAttribute("data-input-enabled", "true");
    await Promise.all([...pending]);
    const observed = [...network].sort((left, right) => left.sequence - right.sequence);
    const declined = observed.find(item => item.kind === "queue" && item.status === 503
      && (item.body as { detail?: { code?: string } }).detail?.code === "study_feedback_preparation_pending");
    expect(declined, "Actual queue declined new authority while local feedback was pending").toBeDefined();
    const prepared = observed.find(item => item.kind === "prepare" && item.sequence > declined!.sequence
      && [200, 202].includes(item.status));
    expect(prepared, "Actual browser submitted the explicit preparation command").toBeDefined();
    const issued = observed.find(item => item.kind === "queue" && item.status === 200 && item.sequence > prepared!.sequence
      && (item.body as { cards?: Array<{ id: string }> }).cards?.[0]?.id === scenario.target_card_id);
    expect(issued, "Browser reread and displayed the urgent target from the real queue").toBeDefined();
    const targetEntry = Number(await page.locator("#train").getAttribute("data-queue-entry-id"));
    expect(targetEntry).toBe((issued!.body as { cards: Array<{ queue_entry_id: number }> }).cards[0].queue_entry_id);
    const targetFen = await board.getAttribute("data-fen");
    expect(targetFen).toBeTruthy();
    await browserReady({ stage: "target", target_entry_id: targetEntry, target_fen: targetFen, protected_review: protectedReview });
    await providerReady("request-refresh");
    const requestedRefresh = await page.evaluate(async () => {
      const current = await fetch("/api/settings");
      if (current.status !== 200) throw new Error(`Actual settings read failed: ${current.status}`);
      const settings = await current.json() as Record<string, unknown>;
      if (settings.new_cards_per_day !== 0 || settings.study_only_mode !== true)
        throw new Error("Owned refresh must preserve zero allowance and Study-only mode");
      const identity = `packaged-refresh:${crypto.randomUUID()}`;
      const response = await fetch("/api/settings", { method: "PUT",
        headers: { "Content-Type": "application/json", "Idempotency-Key": identity },
        body: JSON.stringify(settings) });
      return { identity, settings, status: response.status, receipt: await response.json() as Record<string, unknown> };
    });
    refreshRequests.push(requestedRefresh);
    const refreshedSettings = await confirmed(requestedRefresh.status, requestedRefresh.receipt);
    refreshRequests[0].response = refreshedSettings;
    expect(refreshedSettings).toEqual(requestedRefresh.settings);
    await browserReady({ stage: "refresh-requested", request: refreshRequests[0], target_entry_id: targetEntry });
    await providerReady("refresh");
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(targetEntry));
    await expect(board).toHaveAttribute("data-fen", targetFen!);
    // A genuinely unseen opening can first teach its move. Use its natural
    // requeued attempt for the clean completion; do not suppress assistance.
    for (let attempt = 0; attempt < 2; attempt++) {
      const entry = await page.locator("#train").getAttribute("data-queue-entry-id");
      const saved = await playAndSave(scenario.target_card_id);
      if (saved.body.guided === false) break;
      await expect.poll(() => page.locator("#train").getAttribute("data-queue-entry-id"),
        { timeout: 45_000 }).not.toBe(entry);
      await expect(board).toHaveAttribute("data-input-enabled", "true");
    }
    expect(reviews.some(review => review.card_id === scenario.target_card_id && review.body.guided === false)).toBe(true);
    expect(pageErrors).toEqual([]);
    await browserReady({ stage: "complete", reviews, target_first_entry_id: targetEntry });
  } finally {
    await Promise.all([...pending]);
    await testInfo.attach("actual-browser-correspondence", { contentType: "application/json",
      body: JSON.stringify({ scenario, reviews, refresh_requests: refreshRequests, network,
        console_errors: consoleErrors, page_errors: pageErrors }) });
  }
});
