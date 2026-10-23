import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal("indexedDB", new IDBFactory());
  localStorage.clear();
  sessionStorage.clear();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-22T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function setup(independent = true) {
  vi.stubGlobal("__TEMPO_STUDY__", independent);
  if (independent) {
    sessionStorage.setItem("tempo-learning-connection-v1", JSON.stringify({
      origin: "http://learning.test:8001", workspace: "workspace-a",
      credential: "unit-study-credential-" + "x".repeat(32),
      bootstrap: {
        schema_version: 1, workspace_id: "workspace-a", timezone: "Etc/UTC", local_date: "2026-10-22",
        settings: { study_only_mode: false, new_cards_per_day: 10, study_new_per_day: 10,
          tactics_new_per_day: 5, include_defensive_cards_in_daily_stack: true, light_first_interval_days: 7 },
      },
    }));
  }
  const { learningDayKey } = await import("../../app/lib/study-connection");
  const offline = await import("../../app/lib/offline-training");
  const { queueCardSchema } = await import("../../app/domain/schemas");
  const cards = [1, 2, 3].map(index => queueCardSchema.parse({
    id: `issued-card-${index}`, queue_entry_id: 100 + index, revision: 7 + index,
    latest_review_id: 70 + index, cycle: 2, content_type: "opening",
    start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    moves: ["e2e4", "e7e5"], repertoire_name: "Issued opening", repertoire_source: "PGN",
    first_correct_at: null, recent_attempts_json: "[]", scheduling_mode: "normal",
  }));
  const envelope = {
    cards, count: cards.length, local_date: learningDayKey(), prepared_at: new Date().toISOString(),
    projection: { state: "ready", generation: 1, updated_at: new Date().toISOString(),
      refresh_pending: false, last_error: null, blocked_count: 0 },
  };
  return { offline, cards, envelope };
}

it("independent mode keeps only the first of three valid issued cards", async () => {
  const { offline, cards, envelope } = await setup();
  const originalCards = structuredClone(cards);
  const saved = await offline.savePreparedTraining(envelope);

  expect(saved.cards).toEqual([originalCards[0]]);
  expect(saved.attempts).toEqual([]);
  expect(await offline.readPreparedTraining()).toEqual(saved);
  expect(envelope.cards).toEqual(originalCards);
});

it("an empty independent queue stays empty and invents no attempts", async () => {
  const { offline, envelope } = await setup();
  const saved = await offline.savePreparedTraining({ ...envelope, cards: [], count: 0 });

  expect(saved.cards).toEqual([]);
  expect(saved.attempts).toEqual([]);
  expect(await offline.readPreparedTraining()).toEqual(saved);
  await expect(offline.recordOfflineAttempt(101, "again", false, undefined, {
    attemptId: "unissued-attempt",
  })).rejects.toThrow("The active card differs from the saved phone queue");
  expect(await offline.readPreparedTraining()).toEqual(saved);
});

it("original card, queue-entry, attempt and revision identities survive save and record", async () => {
  const { offline, cards, envelope } = await setup();
  const original = structuredClone(cards[0]);
  const attemptId = "original-issued-attempt";
  await offline.savePreparedTraining(envelope);
  expect((await offline.readPreparedTraining())?.cards).toEqual([original]);

  const recorded = await offline.recordOfflineAttempt(original.queue_entry_id, "correct", false,
    undefined, { attemptId });

  expect(recorded.cards).toEqual([]);
  expect(recorded.attempts).toHaveLength(1);
  expect(recorded.attempts[0]).toMatchObject({
    cardId: original.id, localEntryId: original.queue_entry_id, attemptId,
    expectedRevision: original.revision, expectedReviewId: original.latest_review_id,
    queueCycle: original.cycle, outcome: "correct", guided: false,
    completedAt: "2026-10-22T12:00:00.000Z",
  });
  expect(await offline.readPreparedTraining()).toEqual(recorded);
  expect(cards[0]).toEqual(original);
});

it("Again creates no independent local repeat", async () => {
  const { offline, cards, envelope } = await setup();
  const saved = await offline.savePreparedTraining(envelope);
  const recordAgain = () => offline.recordOfflineAttempt(cards[0].queue_entry_id, "again", false,
    undefined, { attemptId: "original-again-attempt" });
  const recorded = await recordAgain();

  expect(recorded.cards).toEqual([]);
  expect(recorded.nextTemporaryId).toBe(saved.nextTemporaryId);
  expect(recorded.attempts).toHaveLength(1);
  expect(recorded.attempts[0]).toMatchObject({
    cardId: cards[0].id, localEntryId: cards[0].queue_entry_id,
    attemptId: "original-again-attempt", outcome: "again", guided: false,
  });
  expect(await offline.readPreparedTraining()).toEqual(recorded);
  expect(await recordAgain()).toEqual(recorded);
  expect(await offline.readPreparedTraining()).toEqual(recorded);
});

it("legacy control keeps the three-card batch and existing Again repeat behavior", async () => {
  const { offline, cards, envelope } = await setup(false);
  const saved = await offline.savePreparedTraining(envelope);
  expect(saved.cards).toEqual(cards);
  expect(saved.attempts).toEqual([]);
  expect(await offline.readPreparedTraining()).toEqual(saved);

  const recorded = await offline.recordOfflineAttempt(cards[0].queue_entry_id, "again", false,
    undefined, { attemptId: "legacy-again-attempt" });

  expect(recorded.cards).toEqual([cards[1], cards[2], {
    ...cards[0], queue_entry_id: saved.nextTemporaryId, parent_local_entry_id: cards[0].queue_entry_id,
    cycle: 3, attempt_state: "guided", attempt_failed: false,
    recent_attempts_json: '["again"]',
  }]);
  expect(recorded.nextTemporaryId).toBe(saved.nextTemporaryId + 1);
  expect(recorded.attempts).toHaveLength(1);
  expect(recorded.attempts[0]).toMatchObject({
    cardId: cards[0].id, localEntryId: cards[0].queue_entry_id,
    attemptId: "legacy-again-attempt", outcome: "again", guided: false,
  });
  expect(await offline.readPreparedTraining()).toEqual(recorded);
});
