import { afterEach, beforeEach, expect, it, vi } from "vitest";
import manifest from "../fixtures/opening-evidence-manifest.json";
import { canonicalFenKey } from "../../app/utils/canonical-line";
import { beginOpeningAttempt, partialOpeningAttempt, preparePositionOpeningAttempt } from "../../app/lib/opening-evidence-journal";
import { enqueuePendingReview, flushPendingReviews, pendingReviews, conflictedReviews } from "../../app/lib/review-outbox";
import { saveEvidenceAwareReview } from "../../app/lib/opening-evidence-review";
import { offlineTrainingDatabase } from "../../database/browser/offline-training-storage";
import { confirmOpeningDelivery, readPositionOpeningJournal, readSavedOpeningJournal } from "../../database/browser/opening-evidence-journal-storage";
import { resetStudyStorage, withMissingReceipts } from "./support/study-intent-fixtures";
import { asCardId, asFenString, asQueueEntryId } from "../../app/types";
import type { PracticeCard } from "../../app/domain/cards";
import { decodePositionPresentation } from "../../app/domain/position-presentation";
import { computeEssentialStudyTask } from "../../app/lib/essential-study-computation";
import { decodeStudyResult } from "../../app/lib/study-result-decoder";
import { openingDecisionManifestSchema } from "../../app/domain/opening-evidence";

const plan = decodePositionPresentation({ version: 1, plan_id: "a".repeat(64),
  owner_epoch: "issued-epoch", target_ply: 0, starting_fen: manifest.decisions[0].fen,
  moves_uci: ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
  source_digest: "issued-source", local_day: "2026-09-30", skipped_prefix_proof_ids: [],
  attempt_id: "issued-position", route_id: "issued-route", original_card_id: manifest.card_id, original_card_revision: 3,
  queue_entry_id: 101, decision_slots: manifest.decisions.map(d => ({ move_offset: d.move_offset,
    expected_uci: d.expected_uci, position_key: canonicalFenKey(d.fen) })) });
const card: PracticeCard = { id: asCardId("queue-101"), backendId: asCardId(manifest.card_id), kind: "opening" as const,
  title: "Position", subtitle: "", startingFen: asFenString(manifest.decisions[0].fen), moves: [], userMoveTarget: 3,
  queueEntryId: asQueueEntryId(101), revision: 3, openingDecisionManifest: manifest as any,
  positionStudyMode: "position" as const, positionPresentation: plan };
const completion = { attempt_id: plan.attempt_id, manifest, origin_queue_entry_id: 101, queue_entry_id: 101,
  parent_attempt_id: null, started_at: "2026-09-30T12:00:00Z", study_timezone: "UTC", source: "live" as const,
  events: [], terminal: { state: "complete" as const, final_sequence: 0, ended_at: "2026-09-30T12:01:00Z" } };
const review = { backendId: manifest.card_id, queueEntryId: 101, expectedRevision: 3, attemptId: plan.attempt_id,
  outcome: "correct" as const, guided: true, completedAt: completion.terminal.ended_at,
  openingEvidenceCompletion: completion as any, positionStudy: true as const };
beforeEach(async () => { await resetStudyStorage(); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("restores the first wrong response after checkpoint acknowledgement and pause", async () => {
  let held = false;
  vi.stubGlobal("navigator", { locks: { request: async (_name: string, _options: unknown, callback: (lock: unknown) => Promise<void>) => {
    if (held) return callback(null);
    held = true;
    try { await callback({}); } finally { held = false; }
  } } });
  expect(await preparePositionOpeningAttempt(card)).toBeUndefined();
  expect(beginOpeningAttempt(card, "different")).toBeUndefined();
  expect(beginOpeningAttempt(card, plan.attempt_id, { offline: true })).toBeUndefined();
  const mismatched = { ...card, positionPresentation: { ...plan, decision_slots: [] } };
  expect(beginOpeningAttempt(mismatched, plan.attempt_id)).toBeUndefined();
  const journal = beginOpeningAttempt(card, plan.attempt_id)!;
  journal.response(0, "d2d4", "wrong");
  await vi.waitFor(async () => {
    const saved = await readSavedOpeningJournal(await offlineTrainingDatabase(), plan.attempt_id, () => false);
    expect(saved?.events[0].response_uci).toBe("d2d4");
  });
  const database = await offlineTrainingDatabase();
  await confirmOpeningDelivery(database, journal.snapshot(), { persisted: true, attempt_id: plan.attempt_id,
    received_sequences: [1], contiguous_sequence: 1 }, () => {});
  expect((await readPositionOpeningJournal(database, plan.attempt_id))?.events[0].response_uci).toBe("d2d4");
  partialOpeningAttempt(plan.attempt_id);
  await vi.waitFor(() => expect(held).toBe(false));
  vi.resetModules();
  const reloaded = await import("../../app/lib/opening-evidence-journal");
  const changed = { ...card, positionPresentation: { ...plan, route_id: "different-route" } };
  expect(await reloaded.preparePositionOpeningAttempt(changed)).toContain("does not match");
  expect(await reloaded.preparePositionOpeningAttempt(card)).toBeUndefined();
  const restored = reloaded.beginOpeningAttempt(card, plan.attempt_id)!;
  expect(restored).not.toBe(journal);
  restored.response(0, "d2d4", "wrong");
  restored.assistance(0, "guided");
  restored.response(0, "e2e4", "expected");
  expect(restored.snapshot()).toMatchObject({ attempt_id: plan.attempt_id, terminal: null,
    events: [{ sequence: 1, kind: "first_response", response_uci: "d2d4" },
      { sequence: 2, kind: "assistance", assistance: "guided" }, { sequence: 3, kind: "correction", response_uci: "e2e4" }] });
  reloaded.partialOpeningAttempt(plan.attempt_id);
  await vi.waitFor(() => expect(held).toBe(false));
});

it("holds a second tab when the issued attempt lease is owned", async () => {
  vi.stubGlobal("navigator", { locks: { request: async (_name: string, _options: unknown, callback: (lock: unknown) => Promise<void>) => callback(null) } });
  const other = { ...card, positionPresentation: { ...plan, attempt_id: "other-tab" } };
  expect(await preparePositionOpeningAttempt(other)).toContain("another tab");
  expect(beginOpeningAttempt(other, "other-tab")).toBeUndefined();
});

it("retains one immutable position packet through network failure and duplicate enqueue, then sends it once on retry", async () => {
  await enqueuePendingReview(review);
  await enqueuePendingReview(review);
  await expect(enqueuePendingReview({ ...review, guided: false })).rejects.toThrow("different completed result");
  const bodies: string[] = [];
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (_input, options) => {
    bodies.push(String(options!.body));
    if (bodies.length === 1) throw new Error("offline");
    return Response.json({ persisted: true });
  })));
  await expect(flushPendingReviews()).rejects.toThrow("offline");
  expect(pendingReviews()).toHaveLength(1);
  expect((await flushPendingReviews(Infinity, true, plan.attempt_id)).persistedAttemptIds).toEqual([plan.attempt_id]);
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toBe(bodies[1]);
  expect(JSON.parse(bodies[0]).opening_evidence_completion).toEqual(completion);
});

it("retains a position conflict without aggregate fallback or legacy reconciliation", async () => {
  await enqueuePendingReview(review);
  const request = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ detail: { code: "opening_evidence_conflict", message: "changed", aggregate_review_allowed: true } }, { status: 409 }));
  vi.stubGlobal("fetch", withMissingReceipts(request));
  const result = await flushPendingReviews();
  expect(result.conflictedAttemptIds).toEqual([plan.attempt_id]);
  expect(conflictedReviews()[0].openingEvidenceCompletion).toEqual(completion);
  expect(request).toHaveBeenCalledTimes(1);
  expect(String(request.mock.calls[0][0])).not.toContain("reconcile");
});

it("does not downgrade a position evidence conflict to an aggregate-only review", async () => {
  const request = vi.fn(async () => Response.json({ detail: { code: "opening_evidence_unavailable", aggregate_review_allowed: true } }, { status: 409 }));
  const rejected = vi.fn();
  const response = await saveEvidenceAwareReview({ endpoint: "/review", operationKey: "key", body: {}, completion: completion as any,
    positionStudy: true, onEvidenceRejected: rejected, request });
  expect(response.status).toBe(409);
  expect(request).toHaveBeenCalledOnce();
  expect(rejected).not.toHaveBeenCalled();
});

it("fen_spec_ep_manifest_prepares_a_playable_durable_position_attempt_and_restores_original_first_response", async () => {
  vi.resetModules();
  const journalModule = await import("../../app/lib/opening-evidence-journal");
  const { offlineTrainingDatabase: currentDatabase } = await import("../../database/browser/offline-training-storage");
  const { readPositionOpeningJournal: readJournal } = await import("../../database/browser/opening-evidence-journal-storage");
  let held = false;
  vi.stubGlobal("navigator", { locks: { request: async (_name: string, _options: unknown, callback: (lock: unknown) => Promise<void>) => {
    if (held) return callback(null);
    held = true;
    try { await callback({}); } finally { held = false; }
  } } });
  const rawManifest = { ...manifest, decisions: manifest.decisions.map(decision => ({ ...decision,
    fen: decision.move_offset === 2 ? decision.fen.replace(" - 0 2", " e6 0 2") : decision.fen })) };
  const issuedPlan = decodePositionPresentation({ ...plan, attempt_id: "issued-fen-spec-ep" });
  const manifestBytes = JSON.stringify(rawManifest);
  const planBytes = JSON.stringify(issuedPlan);
  const task = { kind: "queue" as const, payload: { cards: [{ id: manifest.card_id, queue_entry_id: 101, revision: 3,
    start_fen: issuedPlan.starting_fen, moves: [...issuedPlan.moves_uci], content_type: "opening",
    repertoire_name: "Position", repertoire_source: "synthetic", trained_color: "white",
    opening_decision_manifest: rawManifest, position_study_mode: "position", position_presentation: issuedPlan }] } };
  const [workerCard] = decodeStudyResult(task, structuredClone(computeEssentialStudyTask(task)));
  expect(workerCard.positionStudyHold).toBeUndefined();
  expect(workerCard.openingDecisionManifest?.decisions[1].fen).toContain(" e6 ");
  expect(workerCard.positionPresentation?.decision_slots[1].position_key).toMatch(/ -$/);
  expect(await journalModule.preparePositionOpeningAttempt(workerCard)).toBeUndefined();
  const journal = journalModule.beginOpeningAttempt(workerCard, issuedPlan.attempt_id)!;
  expect(journal).toBeDefined();
  const database = await currentDatabase();
  const prepared = await readJournal(database, issuedPlan.attempt_id);
  expect(prepared?.attempt).toMatchObject({ attempt_id: issuedPlan.attempt_id, queue_entry_id: 101,
    manifest: rawManifest, position_plan: issuedPlan, terminal: null });
  expect(prepared?.events).toEqual([]);
  journal.response(2, "d2d4", "wrong");
  await vi.waitFor(async () => {
    const saved = await readJournal(database, issuedPlan.attempt_id);
    expect(saved?.events).toMatchObject([{ sequence: 1, kind: "first_response",
      decision_id: rawManifest.decisions[1].decision_id, response_uci: "d2d4" }]);
  });
  journalModule.partialOpeningAttempt(issuedPlan.attempt_id);
  await vi.waitFor(() => expect(held).toBe(false));
  vi.resetModules();
  const reloaded = await import("../../app/lib/opening-evidence-journal");
  expect(await reloaded.preparePositionOpeningAttempt(workerCard)).toBeUndefined();
  const restored = reloaded.beginOpeningAttempt(workerCard, issuedPlan.attempt_id)!;
  expect(restored).not.toBe(journal);
  restored.response(2, "d2d4", "wrong");
  restored.response(2, "g1f3", "expected");
  expect(restored.snapshot()).toMatchObject({ attempt_id: issuedPlan.attempt_id, manifest: rawManifest,
    queue_entry_id: 101, terminal: null, events: [
      { sequence: 1, kind: "first_response", decision_id: rawManifest.decisions[1].decision_id, response_uci: "d2d4" },
      { sequence: 2, kind: "correction", decision_id: rawManifest.decisions[1].decision_id, response_uci: "g1f3" },
    ] });
  expect(restored.positionPlan).toEqual(issuedPlan);
  expect(JSON.stringify(rawManifest)).toBe(manifestBytes);
  expect(JSON.stringify(workerCard.positionPresentation)).toBe(planBytes);
  expect(Object.isFrozen(workerCard.positionPresentation)).toBe(true);
  reloaded.partialOpeningAttempt(issuedPlan.attempt_id);
  await vi.waitFor(() => expect(held).toBe(false));
});

it("position_journal_keeps_legally_available_ep_distinct_and_refuses_a_mismatched_issued_key", async () => {
  vi.resetModules();
  const journalModule = await import("../../app/lib/opening-evidence-journal");
  const { offlineTrainingDatabase: currentDatabase } = await import("../../database/browser/offline-training-storage");
  const { readPositionOpeningJournal: readJournal } = await import("../../database/browser/opening-evidence-journal-storage");
  vi.stubGlobal("navigator", { locks: { request: async (_name: string, _options: unknown, callback: (lock: unknown) => Promise<void>) => callback({}) } });
  const fen = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1";
  const epManifest = openingDecisionManifestSchema.parse({ ...manifest,
    decisions: [{ ...manifest.decisions[0], fen, expected_uci: "e1d1" }] });
  const mismatchedPlan = decodePositionPresentation({ ...plan, attempt_id: "issued-legal-ep-mismatch", starting_fen: fen,
    moves_uci: ["e1d1"], decision_slots: [{ move_offset: 0, position_key: canonicalFenKey(fen.replace(" d6 ", " - ")), expected_uci: "e1d1" }] });
  const mismatched = { ...card, startingFen: asFenString(fen), openingDecisionManifest: epManifest, positionPresentation: mismatchedPlan };
  expect(await journalModule.preparePositionOpeningAttempt(mismatched)).toContain("invalid");
  expect(journalModule.beginOpeningAttempt(mismatched, mismatchedPlan.attempt_id)).toBeUndefined();
  expect(await readJournal(await currentDatabase(), mismatchedPlan.attempt_id)).toBeUndefined();
  expect(epManifest.decisions[0].fen).toBe(fen);
});
