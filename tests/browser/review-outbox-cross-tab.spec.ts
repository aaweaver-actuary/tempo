import type { Page } from "@playwright/test";
import { test, expect } from "./observability";
import { prepareUI, boardBounds } from "./ui-fixtures";
import { api } from "./product-fixtures";
import { envelope, instrumentReviewWrites, openOutboxHarness, readReviews, reviewFixture, saveProof, storageBarrier,
  type Issued } from "./review-outbox-cross-tab-fixture";
import type { PendingReview } from "../../app/lib/review-outbox";

// Keep route-controlled connected delivery deterministic; the offline shell
// is covered separately and must not serve an application behind the harness.
test.use({ serviceWorkers: "block" });

async function showIssuedReview(page: Page, identity: Issued) {
  await instrumentReviewWrites(page);
  // Observe actual server-issued presentation; select one already-issued head,
  // retaining every immutable field from the real queue response.
  await page.route("**/api/queue/window?**", async route => {
    const response = await route.fetch();
    const body = await response.json();
    const card = body.cards.find((item: { id: string }) => item.id === identity.card_id);
    expect(card, "The actual queue must contain this legitimate issued review").toBeDefined();
    await route.fulfill({ response, json: { ...body, cards: [card] } });
  });
  // No submission may escape before the durable-store observations. Lost
  // transport leaves the completed result retained by the real application.
  await page.route("**/api/cards/*/review", route => route.abort("failed"));
  await prepareUI(page);
  await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(identity.queue_entry_id));
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
}
async function completeCorrect(page: Page) {
  const board = await boardBounds(page);
  await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 6.5 / 8);
  await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 4.5 / 8);
}
async function flushOnly(page: Page, attemptId: string, receiptFirst = true, deferredAttemptIds: string[] = []) {
  return page.evaluate(({ attemptId, receiptFirst, deferredAttemptIds }) => window.tempoReviewOutbox.flushPendingReviews(1, receiptFirst,
    undefined, new Set(deferredAttemptIds), attemptId), { attemptId, receiptFirst, deferredAttemptIds });
}

for (const order of ["A-B", "B-A"] as const) {
  test(`review_outbox_two_tabs_preserve_distinct_completed_intents: ${order}`, async ({ page, context, request }, testInfo) => {
    test.setTimeout(180_000);
    const fixture = await reviewFixture(request);
    const other = await context.newPage();
    const barriers = [];
    try {
      await showIssuedReview(page, fixture.issued[0]);
      await showIssuedReview(other, fixture.issued[1]);
      const first = await storageBarrier(context, page), second = await storageBarrier(context, other);
      barriers.push(first, second);
      await first.arm(); await second.arm();
      const completionA = completeCorrect(page).catch(error => ({ error: String(error) }));
      const capturedA = await first.capture();
      const completionB = completeCorrect(other).catch(error => ({ error: String(error) }));
      const capturedB = await second.capture();
      expect(capturedA.replacement).toHaveLength(1); expect(capturedB.replacement).toHaveLength(1);
      const immutable = [capturedA.replacement[0], capturedB.replacement[0]];
      for (const barrier of order === "A-B" ? [first, second] : [second, first]) await barrier.release();
      await Promise.all([completionA, completionB]);
      const beforeReload = await readReviews(page);
      // Both realm-local in-flight completions must be interrupted; recovery
      // cannot retain an overwritten envelope through a surviving closure.
      await page.close();
      await openOutboxHarness(other); await other.reload(); await openOutboxHarness(other);
      const afterReload = await readReviews(other);
      const beforeRecovery = await fixture.observe();
      await other.unroute("**/api/cards/*/review");
      const recovery = await other.evaluate(() => window.tempoReviewOutbox.flushPendingReviews(Infinity, true))
        .catch(error => ({ error: String(error) }));
      const afterRecovery = await fixture.observe();
      const unresolved = await readReviews(other);
      await saveProof(testInfo, { project: fixture.project, order, immutable, capturedA, capturedB,
        beforeReload, afterReload, beforeRecovery, recovery, afterRecovery, unresolved });
      expect.soft(beforeReload.map(review => review.attemptId).sort(), "Neither completed immutable envelope may be overwritten").toEqual(immutable.map(review => review.attemptId).sort());
      expect.soft(afterReload.map(review => review.attemptId).sort(), "Both intents must survive true reload before settlement").toEqual(immutable.map(review => review.attemptId).sort());
      for (const original of immutable) {
        expect.soft(afterReload).toContainEqual(original);
        expect.soft(afterRecovery.receipts.some(receipt => receipt[1] === original.attemptId), "Each original attempt must remain authoritatively recoverable").toBe(true);
      }
      expect.soft(afterRecovery.reviews).toHaveLength(2);
      expect.soft(afterRecovery.reviews.filter(review => review[0] === fixture.issued[0].card_id)).toHaveLength(1);
      expect.soft(afterRecovery.reviews.filter(review => review[0] === fixture.issued[1].card_id)).toHaveLength(1);
    } finally {
      for (const barrier of barriers) await barrier.close().catch(() => undefined);
      await other.close(); await page.close(); await fixture.cleanup();
    }
  });
}

for (const accepted of ["correct", "again"] as const) {
  test(`review_two_tabs_conflicting_results_preserve_original_receipts: ${accepted} first`, async ({ page, context, request }, testInfo) => {
    test.setTimeout(180_000);
    const fixture = await reviewFixture(request);
    const other = await context.newPage();
    try {
      await showIssuedReview(page, fixture.issued[0]); await showIssuedReview(other, fixture.issued[0]);
      await completeCorrect(page);
      await expect.poll(async () => (await readReviews(page)).length).toBe(1);
      await other.getByRole("button", { name: "Show move" }).click();
      await completeCorrect(other);
      await expect.poll(async () => (await readReviews(other)).length).toBe(2);
      const originals = await readReviews(other);
      expect(originals.map(review => review.outcome).sort()).toEqual(["again", "correct"]);
      await openOutboxHarness(page); await openOutboxHarness(other);
      await page.unroute("**/api/cards/*/review"); await other.unroute("**/api/cards/*/review");
      const winner = originals.find(review => review.outcome === accepted)!;
      const competitor = originals.find(review => review.outcome !== accepted)!;
      const first = await flushOnly(page, winner.attemptId!, true, [competitor.attemptId!]);
      const acceptedHistory = await fixture.observe();
      const second = await flushOnly(other, competitor.attemptId!).catch(error => ({ error: String(error) }));
      const conflictingHistory = await fixture.observe();
      await page.reload(); await other.reload();
      const retained = await readReviews(other);
      await openOutboxHarness(page); await openOutboxHarness(other);
      const recovered = await other.evaluate(() => window.tempoReviewOutbox.flushPendingReviews(Infinity, true)).catch(error => ({ error: String(error) }));
      const finalHistory = await fixture.observe();
      await saveProof(testInfo, { project: fixture.project, originals, winner, competitor, first, second, acceptedHistory,
        conflictingHistory, retained, recovered, finalHistory });
      expect(acceptedHistory.reviews).toHaveLength(1);
      expect(finalHistory.reviews).toHaveLength(2);
      expect(finalHistory.receipts).toHaveLength(2);
      expect(finalHistory).toEqual(conflictingHistory);
      for (const original of originals) {
        const receipts = finalHistory.receipts.filter(receipt => receipt[1] === original.attemptId);
        expect(receipts).toHaveLength(1);
        expect(receipts[0][6]).toBe(original.outcome);
        expect(receipts[0][7]).toBe(Number(original.guided));
        expect(new Date(String(receipts[0][8])).toISOString()).toBe(original.completedAt);
      }
      expect(finalHistory.receipts.find(receipt => receipt[1] === winner.attemptId)).toEqual(acceptedHistory.receipts.find(receipt => receipt[1] === winner.attemptId));
      expect(first.preparedContinuations).toBeUndefined();
      // A competing outcome is either explicitly retained by reconciliation or
      // attributed by its own logical receipt under the existing server policy.
      expect(retained.some(review => review.attemptId === competitor.attemptId) ||
        finalHistory.receipts.some(receipt => receipt[1] === competitor.attemptId)).toBe(true);
      for (const review of retained) expect(originals.find(original => original.attemptId === review.attemptId)?.outcome).toBe(review.outcome);
    } finally { await page.close(); await other.close(); await fixture.cleanup(); }
  });
}

const mutationPairs = ["enqueue-enqueue", "enqueue-flush", "flush-retry", "flush-conflict", "flush-confirmed-deletion", "recovery-other-update"] as const;
for (const pair of mutationPairs) test(`review_outbox_cross_tab_mutations_preserve_unrelated_intent: ${pair}`, async ({ page, context, request }, testInfo) => {
  test.setTimeout(180_000);
  const fixture = await reviewFixture(request);
  const other = await context.newPage();
  const barriers = [];
  try {
    await instrumentReviewWrites(page); await instrumentReviewWrites(other);
    await openOutboxHarness(page); await openOutboxHarness(other);
    const originalA = envelope(fixture.issued[0]), originalB = envelope(fixture.issued[1]);
    const enqueue = (target: Page, review: PendingReview) => target.evaluate(review => window.tempoReviewOutbox.enqueuePendingReview(review), review);
    if (pair !== "enqueue-enqueue") await enqueue(page, originalA);
    if (["flush-retry", "flush-conflict", "flush-confirmed-deletion"].includes(pair)) {
      if (pair === "flush-retry") Object.assign(originalB, { state: "conflicted", conflict: { code: "card_deleted", message: "Retained completed result", retryable: false } });
      await enqueue(page, originalB);
    }
    if (pair === "flush-conflict") await fixture.retireSecond();
    const first = await storageBarrier(context, page), second = await storageBarrier(context, other);
    barriers.push(first, second);
    await first.arm(); await second.arm(pair === "flush-conflict" ? "conflicted" : undefined);
    const operationA = (pair === "enqueue-enqueue" || pair === "enqueue-flush" ? enqueue(page, pair === "enqueue-enqueue" ? originalA : originalB)
      : flushOnly(page, originalA.attemptId!, pair === "recovery-other-update")).catch(error => ({ error: String(error) }));
    const capturedA = await first.capture();
    const operationB = (pair === "flush-retry" ? other.evaluate(attemptId => window.tempoReviewOutbox.retryReviewConflict(attemptId), originalB.attemptId!)
      : pair === "flush-conflict" || pair === "flush-confirmed-deletion" ? flushOnly(other, originalB.attemptId!)
      : pair === "enqueue-flush" ? flushOnly(other, originalA.attemptId!) : enqueue(other, originalB)).catch(error => ({ error: String(error) }));
    const capturedB = await second.capture();
    await first.release(); await second.release();
    const results = await Promise.all([operationA, operationB]);
    const stored = await readReviews(other);
    await other.reload();
    const afterReload = await readReviews(other), history = await fixture.observe();
    await saveProof(testInfo, { project: fixture.project, pair, originalA, originalB, capturedA, capturedB, results, stored, afterReload, history });
    const settledIds = new Set(history.receipts.map(receipt => receipt[1]));
    for (const original of [originalA, originalB]) {
      expect.soft(settledIds.has(original.attemptId) || afterReload.some(review => review.attemptId === original.attemptId),
        "Unsettled immutable intent must survive another tab's write").toBe(true);
      const saved = afterReload.find(review => review.attemptId === original.attemptId);
      if (saved) expect.soft({ attemptId: saved.attemptId, outcome: saved.outcome, completedAt: saved.completedAt })
        .toEqual({ attemptId: original.attemptId, outcome: original.outcome, completedAt: original.completedAt });
    }
    if (pair === "flush-retry") expect.soft(afterReload.find(review => review.attemptId === originalB.attemptId)?.state).toBe("reconciling");
    if (pair === "flush-conflict") expect.soft(afterReload.find(review => review.attemptId === originalB.attemptId)?.state).toBe("conflicted");
  } finally {
    for (const barrier of barriers) await barrier.close().catch(() => undefined);
    await page.close(); await other.close(); await fixture.cleanup();
  }
});

test("lost_review_response_recovers_original_authoritative_receipt", async ({ page, context, request }, testInfo) => {
  test.setTimeout(180_000);
  const fixture = await reviewFixture(request);
  const other = await context.newPage();
  try {
    await openOutboxHarness(page); await openOutboxHarness(other);
    const original = envelope(fixture.issued[0]);
    original.preparedNext = fixture.issued[1];
    await page.evaluate(review => window.tempoReviewOutbox.enqueuePendingReview(review), original);
    const requests: { method: string; url: string; body: string | null; key?: string }[] = [];
    page.on("request", request => requests.push({ method: request.method(), url: request.url(), body: request.postData(), key: request.headers()["idempotency-key"] }));
    // Forward the real request, then drop the response after the server's work.
    await page.route("**/api/cards/*/review", async route => { await route.fetch(); await route.abort("failed"); });
    const interrupted = await flushOnly(page, original.attemptId!, false).catch(error => ({ error: String(error) }));
    await expect.poll(async () => (await fixture.observe()).receipts.some(receipt => receipt[1] === original.attemptId), { timeout: 15_000 }).toBe(true);
    const committed = await fixture.observe();
    await page.close(); await other.reload();
    const retained = await readReviews(other);
    await openOutboxHarness(other);
    const recoveredRequests: string[] = [];
    other.on("request", request => { if (request.method() === "POST") recoveredRequests.push(request.url()); });
    const recovered = await flushOnly(other, original.attemptId!);
    const afterRecovery = await fixture.observe();
    await saveProof(testInfo, { project: fixture.project, original, requests, interrupted, committed, retained, recovered, recoveredRequests, afterRecovery });
    expect(retained).toContainEqual(original);
    expect(afterRecovery.reviews).toHaveLength(1);
    expect(afterRecovery.receipts).toEqual(committed.receipts);
    expect(afterRecovery.cards).toEqual(committed.cards);
    expect(recoveredRequests).toEqual([]);
    expect(recovered.persistedAttemptIds).toContain(original.attemptId);
    if (recovered.preparedContinuations?.[original.attemptId!]) expect(recovered.preparedContinuations[original.attemptId!])
      .toMatchObject(original.preparedNext);
    expect(await readReviews(other)).toEqual([]);
    expect(requests.find(request => request.method === "POST")?.key).toBe(`review-attempt:${original.attemptId}`);
    expect((await request.get(`${api}/operations/${encodeURIComponent(`review-attempt:${original.attemptId}`)}`)).ok()).toBe(true);
  } finally { await page.close(); await other.close(); await fixture.cleanup(); }
});
