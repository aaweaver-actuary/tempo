import { afterEach, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { retireLegacyPracticeCommands } from "../../database/browser/study-connection-storage";
import { retireBackupPractice, retirePortablePractice } from "../../app/domain/retired-practice";

afterEach(() => vi.unstubAllGlobals());

it("retired template and attempt journals are removed without clearing other pending saves", () => {
  localStorage.setItem("tempo-pending-endgame-template-v1", "original template");
  localStorage.setItem("tempo-pending-endgame-attempt-v1", "original attempt");
  localStorage.setItem("tempo-pending-tactic-capture-v1", "keep exact bytes");
  retireLegacyPracticeCommands();
  retireLegacyPracticeCommands();
  expect(localStorage.getItem("tempo-pending-endgame-template-v1")).toBeNull();
  expect(localStorage.getItem("tempo-pending-endgame-attempt-v1")).toBeNull();
  expect(localStorage.getItem("tempo-pending-tactic-capture-v1")).toBe("keep exact bytes");
});

it("old snapshots cannot revive retired cards or reviews and retain ending-themed tactics", () => {
  const tables = { cards: [{ id: "old", content_type: "endgame" }, { id: "kept", content_type: "tactic", theme: "pawnEndgame" }],
    reviews: [{ id: 1, card_id: "old" }, { id: 2, card_id: "kept" }], endgame_templates: [{ id: "old-template" }],
    daily_queue: [{ id: 1, card_id: "old" }, { id: 2, card_id: "kept" }] };
  const original = structuredClone(tables);
  expect(retirePortablePractice(tables)).toEqual({ cards: [tables.cards[1]], reviews: [tables.reviews[1]], daily_queue: [tables.daily_queue[1]] });
  expect(tables).toEqual(original);
});

it("an old snapshot with mixed ownership fails before deleting unrelated cards", () => {
  expect(() => retirePortablePractice({ cards: [{ id: "kept", content_type: "tactic", repertoire_id: "__endgames__" }] })).toThrow("unrelated cards");
});

it("missing retired identities never match unrelated rows with no card_id", () => {
  expect(() => retirePortablePractice({ cards: [{ content_type: "endgame" }], settings: [{ id: 1 }] })).toThrow("identity is missing");
  expect(() => retireBackupPractice({ cards: [{ key: "old", value: { content_type: "endgame" } }], settings: [{ key: "1", value: { sound: true } }] })).toThrow("identity is missing");
});

it("encrypted backup projection excludes only retired cards and linked history", () => {
  const kept = { key: "kept", value: { id: "kept", content_type: "tactic", theme: "rookEndgame" } };
  const stores = { cards: [{ key: "old", value: { id: "old", content_type: "endgame" } }, kept],
    reviews: [{ key: "old", value: { card_id: "old" } }, { key: "kept", value: { card_id: "kept" } }],
    syncMetadata: [{ key: "endgame_attempts:old", value: {} }, { key: "game_sync_state:lichess", value: { checkpoint: 7 } }],
    settings: [{ key: "1", value: { draw_hold_user_moves: 20, sound: true } }] };
  const original = structuredClone(stores);
  expect(retireBackupPractice(stores)).toEqual({ cards: [kept], reviews: [stores.reviews[1]], syncMetadata: [stores.syncMetadata[1]], settings: [{ key: "1", value: { sound: true } }] });
  expect(stores).toEqual(original);
});

it("prepared queue retirement preserves other cards, unknown pending reviews and counters", async () => {
  const { updatePreparedTraining } = await import("../../database/browser/prepared-training-storage");
  const { readPreparedTraining } = await import("../../app/lib/offline-training");
  const attempt = (cardId: string, localEntryId: number) => ({ cardId, localEntryId, outcome: "correct", guided: false,
    completedAt: "2026-10-29T00:00:00Z", expectedReviewId: 0, expectedRevision: 1 });
  const raw = { localDate: "2026-10-29", preparedAt: "2026-10-29T00:00:00Z", nextTemporaryId: 1_000_000_000_000,
    cards: [{ id: "old", content_type: "endgame" }, { id: "kept", content_type: "tactic" }],
    attempts: [attempt("old", 1), attempt("kept", 2), attempt("unclassified", 3)] };
  await updatePreparedTraining(() => raw as unknown as import("../../app/domain/offline-training").PreparedTraining);
  const result = await readPreparedTraining();
  expect(result).toEqual({ ...raw, cards: [raw.cards[1]], attempts: raw.attempts.slice(1) });
  expect(await readPreparedTraining()).toEqual(result);
});

it("validated snapshot import retires mirrored rows atomically and preserves pending intent data", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const { putRecords, getRecords, importPortableSnapshot } = await import("../../database/browser/tempo-db");
  const { studyIntentStore } = await import("../../database/browser/study-intent-storage");
  const cards = [{ id: "old", content_type: "endgame" }, { id: "kept", content_type: "tactic" }];
  const reviews = [{ id: 1, card_id: "old" }, { id: 2, card_id: "kept" }];
  await putRecords("cards", cards.map(value => ({ key: String(value.id), value })));
  await putRecords("reviews", reviews.map(value => ({ key: String(value.id), value })));
  const store = await studyIntentStore();
  await store.put({ key: "pending-review", kind: "review", attemptId: "kept-attempt", requestId: "kept-request",
    endpoint: "/api/cards/kept/review", revision: 1, createdAt: "2026-10-29T00:00:00Z", payload: { outcome: "correct" } });
  const pending = await store.get("pending-review");
  const tables = { cards, reviews, endgame_templates: [{ id: "old-template" }] };
  const stable = (value: unknown): string => value === null || typeof value !== "object" ? JSON.stringify(value)
    : Array.isArray(value) ? `[${value.map(stable).join(",")}]`
    : `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, entry]) => `${JSON.stringify(key)}:${stable(entry)}`).join(",")}}`;
  const checksum = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(stable({ schemaVersion: 1, tables }))))]
    .map(byte => byte.toString(16).padStart(2, "0")).join("");
  const snapshot = { schemaVersion: 1, source: "synthetic retirement", exportedAt: "2026-10-29T00:00:00Z", tables,
    counts: { cards: 2, reviews: 2, endgame_templates: 1 }, checksum };
  await expect(importPortableSnapshot({ ...snapshot, checksum: "0".repeat(64) })).rejects.toThrow("checksum mismatch");
  expect(await getRecords("cards")).toHaveLength(2);
  expect(await importPortableSnapshot(snapshot)).toEqual({ cards: 1, reviews: 1 });
  expect((await getRecords("cards")).map(row => row.value)).toEqual([cards[1]]);
  expect((await getRecords("reviews")).map(row => row.value)).toEqual([reviews[1]]);
  expect(await store.get("pending-review")).toEqual(pending);
  await putRecords("cards", [{ key: "ambiguous", value: { id: "ambiguous", content_type: "tactic", repertoire_id: "__endgames__" } }]);
  const before = await getRecords("cards");
  await expect(importPortableSnapshot(snapshot)).rejects.toThrow("unrelated cards");
  expect(await getRecords("cards")).toEqual(before);
});
