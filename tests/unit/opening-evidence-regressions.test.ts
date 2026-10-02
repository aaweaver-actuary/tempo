import { afterEach, describe, expect, it, vi } from "vitest";
import manifestFixture from "../fixtures/opening-evidence-manifest.json";
import { openingDecisionManifestSchema, openingEvidenceCheckpointSchema } from "../../app/domain/opening-evidence";
import { OpeningAttemptJournal, beginOpeningAttempt, partialOpeningAttempt } from "../../app/lib/opening-evidence-journal";
import * as openingStorage from "../../app/lib/offline-training-storage";
import { saveEvidenceAwareReview } from "../../app/lib/opening-evidence-review";
import { useTrainingStore } from "../../app/state/training-store";
import { asCardId, asFenString, asQueueEntryId, asSanMove } from "../../app/types";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";

const manifest = openingDecisionManifestSchema.parse(manifestFixture);
const header = { attempt_id: "logical-attempt", manifest, origin_queue_entry_id: 101, queue_entry_id: 101,
  parent_attempt_id: null, started_at: "2026-09-30T12:00:00Z", study_timezone: "America/New_York",
  source: "live" as const, terminal: null };
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("shadow opening journal", () => {
  it("AS-08 appends before advancing without awaiting deferred local persistence", () => {
    const appends = vi.fn(() => new Promise<void>(() => undefined));
    const journal = new OpeningAttemptJournal(header, appends);
    for (const decision of manifest.decisions) journal.response(decision.move_offset, decision.expected_uci, "expected");
    expect(appends).toHaveBeenCalledTimes(3);
    expect(journal.events.map(event => event.sequence)).toEqual([1, 2, 3]);
    expect(journal.finish("partial").terminal).toMatchObject({ state: "partial", final_sequence: 3 });
  });

  it("AS-09 preserves categories, first response, reveal and correction without fabricated manual moves", () => {
    const journal = new OpeningAttemptJournal(header, async () => undefined);
    journal.assistance(0, "teaching"); journal.assistance(0, "teaching"); journal.assistance(0, "other");
    journal.response(0, "d2d4", "alternate"); journal.assistance(0, "revealed");
    journal.response(0, "e2e4", "expected"); journal.response(0, "e2e4", "expected");
    journal.manualFailure(2); journal.response(2, "g1f3", "expected");
    expect(journal.events.map(event => event.kind)).toEqual([
      "assistance", "assistance", "first_response", "reveal", "correction", "manual_failure", "correction",
    ]);
    expect(journal.events[2]).toMatchObject({ response_uci: "d2d4", disposition: "alternate" });
    expect(journal.events[5].response_uci).toBeNull();
    expect(openingEvidenceCheckpointSchema.safeParse(journal.finish("complete")).success).toBe(true);
  });

  it("AS-15 local append failure retains original events and reports a capture gap without blocking moves", async () => {
    const append = vi.fn().mockRejectedValueOnce(new Error("Browser storage unavailable"))
      .mockResolvedValue(undefined);
    const journal = new OpeningAttemptJournal(header, append);
    journal.response(0, "e2e4", "expected");
    const original = structuredClone(journal.events[0]);
    await Promise.resolve();
    expect(journal.storageError).toContain("Browser storage unavailable");
    journal.response(2, "g1f3", "expected");
    expect(journal.uncommittedEvents.get(1)).toEqual(original);
    expect(journal.finish("partial").events).toEqual([original, journal.events[1]]);
    expect(journal.terminal?.final_sequence).toBe(2);
  });

  it("AS-15 restart retains an uncommitted partial journal for storage recovery", async () => {
    vi.stubGlobal("indexedDB", {});
    const storage = vi.spyOn(openingStorage, "offlineTrainingDatabase").mockRejectedValue(new Error("Storage is denied"));
    const card=mapQueueCardToPracticeCard({id:"shadow-card",queue_entry_id:101,start_fen:manifest.decisions[0].fen,
      moves:["e2e4","e7e5","g1f3","b8c6","f1b5"],content_type:"opening",repertoire_name:"Shadow",
      repertoire_source:"PGN",opening_decision_manifest:manifest});
    const journal=beginOpeningAttempt(card,"uncommitted-partial")!;
    journal.response(0,"e2e4","expected");
    await vi.waitFor(() => expect(journal.storageError).toContain("Storage is denied"));
    partialOpeningAttempt("uncommitted-partial");
    expect(beginOpeningAttempt(card,"uncommitted-partial")).toBe(journal);
    expect(journal.snapshot()).toMatchObject({terminal:{state:"partial",final_sequence:1},
      events:[{sequence:1,response_uci:"e2e4"}]});
    await vi.waitFor(() => expect(storage).toHaveBeenCalledTimes(2));
  });

  it("AS-15 denied IndexedDB open can retry after access is restored without reloading unsaved work", async () => {
    const database={close:vi.fn(),onversionchange:undefined} as unknown as IDBDatabase;
    const request={result:database} as IDBOpenDBRequest;
    const open=vi.fn().mockImplementationOnce(() => {throw new Error("Storage is denied");}).mockReturnValue(request);
    vi.stubGlobal("indexedDB",{open});
    await expect(openingStorage.offlineTrainingDatabase()).rejects.toThrow("Storage is denied");
    const retry=openingStorage.offlineTrainingDatabase();
    expect(open).toHaveBeenCalledTimes(2);
    request.onsuccess?.call(request,new Event("success"));
    await expect(retry).resolves.toBe(database);
    database.onversionchange?.call(database,new Event("versionchange") as IDBVersionChangeEvent);
    expect(database.close).toHaveBeenCalledOnce();
  });

  it("AS-11 queue refresh preserves logical identity while restart and reinforcement replace it", () => {
    const card = { id: asCardId("shadow-card"), kind: "opening" as const, title: "Shadow", subtitle: "PGN",
      queueEntryId: asQueueEntryId(101), startingFen: asFenString(manifest.decisions[0].fen),
      moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" as const };
    useTrainingStore.getState().hydrateLocalQueue([card], true);
    const first = useTrainingStore.getState().attempt.attemptId;
    useTrainingStore.getState().hydrateLocalQueue([{ ...card }]);
    expect(useTrainingStore.getState().attempt.attemptId).toBe(first);
    useTrainingStore.getState().resetTrainingLine(card);
    const restarted = useTrainingStore.getState().attempt.attemptId;
    expect(restarted).not.toBe(first);
    useTrainingStore.getState().hydrateLocalQueue([{ ...card, queueEntryId: asQueueEntryId(102), queueAttemptState: "reinforcement" }], true);
    expect(useTrainingStore.getState().attempt.attemptId).not.toBe(restarted);
  });

  it("AS-16 invalid optional shadow manifest retains the ordinary playable card", () => {
    const card = mapQueueCardToPracticeCard({ id: "shadow-card", queue_entry_id: 101,
      start_fen: manifest.decisions[0].fen, moves: ["e2e4"], content_type: "opening",
      repertoire_name: "Shadow", repertoire_source: "PGN", opening_decision_manifest: { unsupported: true } });
    expect(card.moves).toEqual(["e4"]);
    expect(card.openingDecisionManifest).toBeUndefined();
    expect(card.openingEvidenceDiagnostic).toContain("invalid manifest");
  });
});

describe("evidence aware aggregate review", () => {
  const completion = openingEvidenceCheckpointSchema.parse({ ...header, events: [], terminal: {
    state: "complete", final_sequence: 0, ended_at: "2026-09-30T12:01:00Z" } });
  const body = { attempt_id: header.attempt_id, outcome: "correct", guided: false, queue_entry_id: 101 };
  it("AS-16 definitive evidence rejection retains diagnostics before aggregate-only delivery", async () => {
    const requests: RequestInit[] = [];
    const rejected = vi.fn(() => { expect(requests).toHaveLength(1); });
    const response = await saveEvidenceAwareReview({ endpoint: "/review", operationKey: "original", body, completion,
      onEvidenceRejected: rejected, request: async (_url, options) => {
        requests.push(options);
        return requests.length === 1 ? Response.json({ detail: { code: "opening_evidence_conflict",
          message: "Immutable manifest rejected", aggregate_review_allowed: true } }, { status: 409 })
          : Response.json({ persisted: true });
      } });
    expect(response.ok).toBe(true); expect(rejected).toHaveBeenCalledOnce();
    expect(JSON.parse(requests[1].body as string)).toEqual(body);
    expect(new Headers(requests[1].headers).get("Idempotency-Key")).toBe("original:aggregate-only");
  });
  it("AS-15 ambiguous delivery retries its original payload and key without fallback", async () => {
    const request = vi.fn().mockRejectedValueOnce(new TypeError("Lost response"))
      .mockResolvedValueOnce(Response.json({ persisted: true }));
    const rejected = vi.fn();
    const options = { endpoint: "/review", operationKey: "original", body, completion, request, onEvidenceRejected: rejected };
    await expect(saveEvidenceAwareReview(options)).rejects.toThrow("Lost response");
    await saveEvidenceAwareReview(options);
    expect(request.mock.calls[0][1]).toEqual(request.mock.calls[1][1]);
    expect(rejected).not.toHaveBeenCalled();
  });
});
