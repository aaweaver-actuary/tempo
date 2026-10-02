import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { openingDecisionManifestSchema } from "../../app/domain/opening-evidence";
const manifest = openingDecisionManifestSchema.parse(JSON.parse(readFileSync(new URL("../fixtures/opening-evidence-manifest.json", import.meta.url), "utf8")));
import type { OpeningEvidenceCheckpoint } from "../../app/domain/opening-evidence";
import { prepareVisualUI } from "./visual-fixtures";

const today = new Date();
const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
const projection = { state: "ready", generation: 1, updated_at: null, refresh_pending: 0, last_error: null };
const card = { id: manifest.card_id, revision: 3, queue_entry_id: 101,
  start_fen: manifest.decisions[0].fen, moves: ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
  content_type: "opening", repertoire_name: "Shadow opening", repertoire_source: "PGN",
  trained_color: "white", has_study_review: true, scheduling_mode: "normal",
  opening_decision_manifest: manifest, opening_evidence_study_timezone: "America/New_York" };
async function prepareQueue(page: Page) {
  const payload = { local_date: localDate, count: 1, cards: [card], projection };
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: payload }));
  await page.route("**/api/queue/prepared?**", route => route.fulfill({ json: { ...payload, prepared_at: new Date().toISOString() } }));
  await page.route("**/api/repertoire/lines", route => route.fulfill({ json: { lines: [] } }));
}
async function square(page: Page, square: string) {
  const bounds = (await page.locator(".cg-wrap").boundingBox())!;
  await page.mouse.click(bounds.x + (square.charCodeAt(0) - 97 + 0.5) * bounds.width / 8,
    bounds.y + (8 - Number(square[1]) + 0.5) * bounds.height / 8);
}
async function move(page: Page, from: string, to: string) {
  await expect(page.getByText("Shadow opening", { exact: true })).toBeVisible();
  await square(page, from); await square(page, to); }
async function savedEvents(page: Page) {
  return page.evaluate(() => new Promise<unknown[]>((resolve, reject) => {
    const request = indexedDB.open("tempo-offline-training", 2);
    request.onsuccess = () => {
      const database = request.result;
      const read = database.transaction("opening_events").objectStore("opening_events").getAll();
      read.onsuccess = () => { database.close(); resolve(read.result); };
      read.onerror = () => { database.close(); reject(read.error); };
    };
    request.onerror = () => reject(request.error);
  }));
}

test("AS-08 deferred evidence persistence leaves rendered moves and aggregate review responsive", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  let releaseCheckpoint: (() => void) | undefined;
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    await new Promise<void>(resolve => { releaseCheckpoint = resolve; });
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.events.length } });
  });
  const reviews: { attempt_id: string; opening_evidence_completion: OpeningEvidenceCheckpoint }[] = [];
  await page.route("**/api/cards/shadow-card/review", async route => {
    reviews.push(route.request().postDataJSON()); await route.fulfill({ json: { persisted: true } });
  });
  await page.goto("/");
  await move(page, "e2", "e4");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await expect(page.locator("cg-board piece.white.pawn")).toHaveCount(8);
  expect(await page.locator("cg-board piece.white.pawn").evaluateAll(pawns => {
    const board = document.querySelector("cg-board")!.getBoundingClientRect();
    return pawns.some(pawn => {
      const piece = pawn.getBoundingClientRect();
      return Math.abs(piece.x - board.x - board.width * 4 / 8) < 2 &&
        Math.abs(piece.y - board.y - board.height * 4 / 8) < 2;
    });
  })).toBe(true);
  await expect.poll(() => Boolean(releaseCheckpoint)).toBe(true);
  await move(page, "g1", "f3");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /5N2.* w /);
  await move(page, "f1", "b5");
  await expect.poll(() => reviews.length).toBe(1);
  const completion = reviews[0].opening_evidence_completion;
  expect(completion.attempt_id).toBe(reviews[0].attempt_id);
  expect(completion.study_timezone).toBe("America/New_York");
  expect(completion.events.map(event => event.response_uci)).toEqual(["e2e4", "g1f3", "f1b5"]);
  expect(completion.terminal).toMatchObject({ state: "complete", final_sequence: 3 });
  releaseCheckpoint?.();
});

test("AS-15 ambiguous checkpoint retries frozen events and delivery key before newer work", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  const sends: { key: string; body: OpeningEvidenceCheckpoint }[] = [];
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    sends.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    if (sends.length === 1) { await route.abort("failed"); return; }
    const checkpoint = sends.at(-1)!.body;
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.events.at(-1)?.sequence } });
  });
  await page.goto("/"); await move(page, "e2", "e4");
  await expect.poll(() => sends.length).toBe(1);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await move(page, "g1", "f3");
  await expect.poll(() => sends.length).toBe(3);
  expect(sends[1]).toEqual(sends[0]);
  expect(sends[2].body.attempt_id).toBe(sends[0].body.attempt_id);
  expect(sends[2].body.events.map(event => event.sequence)).toEqual([2]);
});

test("AS-09 actual teaching arrows manual Again and revealed correction retain first response context", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
    local_date: localDate, count: 1, cards: [{ ...card, has_study_review: false }], projection } }));
  await page.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  const reviews: { opening_evidence_completion: OpeningEvidenceCheckpoint }[] = [];
  await page.route("**/api/cards/shadow-card/review", async route => {
    reviews.push(route.request().postDataJSON()); await route.fulfill({ json: { persisted: true } });
  });
  await page.route("**/api/queue/entries/101/fail", route => route.fulfill({ json: { persisted: true } }));
  await page.goto("/");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-hint", "true");
  await move(page, "e2", "e4");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await page.getByRole("button", { name: "Show move" }).click();
  await move(page, "g1", "f3");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /5N2.* w /);
  await move(page, "f1", "c4");
  await expect(page.getByText(/Again recorded|saving guided/).first()).toBeVisible();
  await move(page, "f1", "b5");
  await expect.poll(() => reviews.length).toBe(1);
  const events = reviews[0].opening_evidence_completion.events;
  expect(events.find(event => event.decision_index === 0)).toMatchObject({ kind: "assistance", assistance: "teaching" });
  expect(events.find(event => event.decision_index === 1 && event.kind === "manual_failure")?.response_uci).toBeNull();
  expect(events.find(event => event.decision_index === 2 && event.kind === "first_response")).toMatchObject({ response_uci: "f1c4", disposition: "wrong" });
  expect(events.some(event => event.decision_index === 2 && event.kind === "reveal")).toBe(true);
  expect(events.some(event => event.decision_index === 2 && event.kind === "correction")).toBe(true);
});

test("AS-15 crash recovery seals committed work as partial and starts a new board identity", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  await page.goto("/"); await move(page, "e2", "e4");
  await expect.poll(async () => (await savedEvents(page)).length).toBe(1);
  const events = await savedEvents(page) as { attempt_id: string; observed_at: string }[];
  await page.unroute("**/api/opening-evidence/checkpoints");
  const recovered: OpeningEvidenceCheckpoint[] = [];
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    recovered.push(checkpoint);
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: 1 } });
  });
  await page.reload();
  await expect.poll(() => recovered.some(checkpoint => checkpoint.terminal?.state === "partial")).toBe(true);
  expect(recovered[0].events[0].observed_at).toBe(events[0].observed_at);
  const partial = recovered.find(checkpoint => checkpoint.terminal)!;
  expect(partial.attempt_id).toBe(events[0].attempt_id);
  expect(partial.terminal?.final_sequence).toBe(1);
  await move(page, "e2", "e4");
  await expect.poll(() => recovered.some(checkpoint => checkpoint.attempt_id !== partial.attempt_id)).toBe(true);
});

test("AS-15 orphaned completion without an aggregate outbox retains partial work instead of inventing a review", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  await page.route("**/api/opening-evidence/attempts/*", route => route.fulfill({ status: 404, json: { detail: "Not persisted" } }));
  await page.goto("/"); await move(page, "e2", "e4");
  await expect.poll(async () => (await savedEvents(page)).length).toBe(1);
  const events = await savedEvents(page) as { attempt_id: string; observed_at: string }[];
  await page.evaluate(({ attemptId, observedAt }) => new Promise<void>((resolve, reject) => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onsuccess = () => {
      const database = open.result;
      const transaction = database.transaction("opening_attempts", "readwrite");
      const store = transaction.objectStore("opening_attempts");
      const read = store.get(attemptId);
      read.onsuccess = () => store.put({ ...read.result,
        terminal: { state: "complete", final_sequence: 1, ended_at: observedAt } });
      transaction.oncomplete = () => { database.close(); resolve(); };
      transaction.onerror = () => { database.close(); reject(transaction.error); };
    };
  }), { attemptId: events[0].attempt_id, observedAt: events[0].observed_at });
  const recovered: OpeningEvidenceCheckpoint[] = [];
  await page.unroute("**/api/opening-evidence/checkpoints");
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    recovered.push(checkpoint);
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: 1 } });
  });
  let aggregateReviews = 0;
  await page.route("**/api/cards/shadow-card/review", route => { aggregateReviews += 1; return route.abort("failed"); });
  await page.reload();
  await expect.poll(() => recovered.some(checkpoint => checkpoint.terminal?.state === "partial")).toBe(true);
  expect(recovered.find(checkpoint => checkpoint.terminal)?.terminal?.ended_at).toBe(events[0].observed_at);
  expect(aggregateReviews).toBe(0);
});

for (const rejectParentEvidence of [false, true]) {
test(rejectParentEvidence
  ? "AS-16 offline parent fallback retains rejected journal after queue refresh and reconciles repeat evidence"
  : "AS-16 offline repeats retain independent attempts and reconcile their parent before completion", async ({ page, context }) => {
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "standalone", { value: true, configurable: true });
    Object.defineProperty(navigator, "userAgent", { value: "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15", configurable: true });
  });
  await prepareQueue(page);
  // A first study pass has a legitimate reinforcement repeat in the existing phone queue.
  const firstStudy = { ...card, first_correct_at: undefined };
  await page.route("**/api/queue/prepared?**", route => route.fulfill({ json: {
    local_date: localDate, count: 1, cards: [firstStudy], projection, prepared_at: new Date().toISOString() } }));
  await page.goto("/");
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await expect.poll(() => page.evaluate(() => new Promise<boolean>(resolve => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onsuccess = () => {
      const database = open.result;
      const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
      read.onsuccess = () => { database.close(); resolve(Boolean(read.result)); };
    };
  }))).toBe(true);
  await page.route("**/api/**", route => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("Shadow opening", { exact: true })).toBeVisible();
  await move(page, "e2", "e4");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await move(page, "g1", "f3");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /5N2.* w /);
  await move(page, "f1", "b5");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", card.start_fen);
  // Grade the genuine repeat before reconnecting; an unencountered decision gets no invented observation.
  await page.getByRole("button", { name: "Correct", exact: true }).click();
  await expect(page.locator(".session-count strong")).toHaveText("0");
  const saved = await page.evaluate(() => new Promise<{ attempts: { attemptId: string; completedAt: string; openingEvidenceCompletion: OpeningEvidenceCheckpoint }[] }>(resolve => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onsuccess = () => {
      const database = open.result;
      const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
      read.onsuccess = () => { database.close(); resolve(read.result); };
    };
  }));
  expect(saved.attempts).toHaveLength(2);
  expect(saved.attempts[0].attemptId).not.toBe(saved.attempts[1].attemptId);
  expect(saved.attempts[1].openingEvidenceCompletion.parent_attempt_id).toBe(saved.attempts[0].attemptId);
  expect(saved.attempts[0].openingEvidenceCompletion.events.filter(event => event.kind === "first_response")).toHaveLength(3);
  expect(saved.attempts[1].openingEvidenceCompletion.events).toEqual([]);
  const reviews: { attempt_id: string; queue_entry_id: number; recorded_at: string; opening_evidence_completion?: OpeningEvidenceCheckpoint }[] = [];
  const deliveryKeys: string[] = [];
  await page.route("**/api/cards/shadow-card/review", async route => {
    reviews.push(route.request().postDataJSON());
    deliveryKeys.push(route.request().headers()["idempotency-key"]);
    if (rejectParentEvidence && reviews.length === 1) {
      await route.fulfill({ status: 409, json: { detail: { code: "opening_evidence_conflict",
        message: "Parent evidence was rejected", aggregate_review_allowed: true } } });
      return;
    }
    const savedReviewCount = reviews.length - Number(rejectParentEvidence);
    await route.fulfill({ json: { persisted: true, review_id: 800 + savedReviewCount,
      requeue_entry_id: savedReviewCount === 1 ? 102 : null } });
  });
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.events.at(-1)?.sequence ?? 0 } });
  });
  await page.unroute("**/api/**"); await page.reload();
  await expect.poll(() => reviews.length).toBe(rejectParentEvidence ? 3 : 2);
  const acceptedReviews = rejectParentEvidence ? reviews.slice(1) : reviews;
  expect(acceptedReviews.map(review => review.queue_entry_id)).toEqual([101, 102]);
  expect(acceptedReviews.map(review => review.attempt_id)).toEqual(saved.attempts.map(attempt => attempt.attemptId));
  expect(acceptedReviews[1].opening_evidence_completion?.queue_entry_id).toBe(102);
  expect(reviews[0].recorded_at).toBe(saved.attempts[0].completedAt);
  expect(reviews[0].opening_evidence_completion).toEqual(saved.attempts[0].openingEvidenceCompletion);
  if (rejectParentEvidence) {
    expect(acceptedReviews[0].opening_evidence_completion).toBeUndefined();
    expect(deliveryKeys[1]).toBe(`${deliveryKeys[0]}:aggregate-only`);
    await expect.poll(() => page.evaluate(() => new Promise<boolean>(resolve => {
      const open = indexedDB.open("tempo-offline-training", 2);
      open.onsuccess = () => {
        const database = open.result;
        const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
        read.onsuccess = () => {
          database.close();
          resolve(Boolean(read.result.attempts.find((attempt: { openingEvidenceRejected?: string; serverAcknowledged?: boolean }) =>
            attempt.serverAcknowledged && attempt.openingEvidenceRejected === "Parent evidence was rejected")));
        };
      };
    }))).toBe(true);
    await page.reload();
    await expect.poll(() => page.evaluate(() => new Promise<boolean>(resolve => {
      const open = indexedDB.open("tempo-offline-training", 2);
      open.onsuccess = () => {
        const database = open.result;
        const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
        read.onsuccess = () => {
          database.close();
          resolve(Boolean(read.result.attempts.find((attempt: { openingEvidenceRejected?: string }) =>
            attempt.openingEvidenceRejected === "Parent evidence was rejected")));
        };
      };
    }))).toBe(true);
  }
});
}
