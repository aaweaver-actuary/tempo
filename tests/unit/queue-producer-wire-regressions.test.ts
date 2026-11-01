import { beforeEach, expect, it } from "vitest";
import fixtures from "../fixtures/queue-wire/producer.json";
import type { PracticeCard } from "../../app/types";
import { computeStudyTask } from "../../app/lib/study-computation";
import { clearDataDiagnostics, dataDiagnostics } from "../../app/lib/validated-data";

beforeEach(() => clearDataDiagnostics());
const { prepared_at: _preparedAt, ...preparedPayload } = fixtures.available;
const decode = (payload: unknown) => computeStudyTask({ kind: "queue", payload }) as PracticeCard[];

it("actual_queue_producer_payload_preserves_family_and_attempt_authority", () => {
  const cards = decode(preparedPayload);
  expect(cards).toHaveLength(3);
  expect(cards[0]).toMatchObject({ backendId: "opening-1", queueEntryId: 21,
    openingEvidenceOriginEntryId: 20, openingEvidenceParentAttemptId: "original-attempt",
    openingDecisionManifest: fixtures.available.cards[0].opening_decision_manifest });
  expect(cards[1]).toMatchObject({ backendId: "tactic-1", kind: "puzzle" });
  expect(cards[2]).toMatchObject({ backendId: "exercise-card", kind: "study", studySnapshot: fixtures.available.cards[2].study_snapshot });
  expect(dataDiagnostics()).toEqual([]);
});

it("producer_advisory_unavailable_and_legacy_absence_keep_cards_usable", () => {
  expect(decode(fixtures.unavailable)).toHaveLength(3);
  const legacy = structuredClone(preparedPayload);
  for (const card of legacy.cards) delete (card as { encounter_evidence_status?: string }).encounter_evidence_status;
  expect(decode(legacy)).toHaveLength(3);
  expect(dataDiagnostics()).toEqual([]);
  expect(decode(fixtures.empty)).toEqual([]);
  expect(fixtures.empty.projection.state).toBe("refreshing");
});

it("invalid_issued_identity_is_rejected_and_invalid_manifest_is_not_used", () => {
  for (const [field, value] of [["revision", 0], ["queue_entry_id", 0], ["id", ""]] as const) {
    clearDataDiagnostics();
    const payload = structuredClone(preparedPayload);
    Object.assign(payload.cards[0], { [field]: value });
    expect(decode(payload)).toHaveLength(2);
    expect(dataDiagnostics().length).toBeGreaterThan(0);
  }
  clearDataDiagnostics();
  const payload = structuredClone(preparedPayload);
  payload.cards[0].opening_decision_manifest!.manifest_id = "invalid";
  const cards = decode(payload);
  expect(cards).toHaveLength(3);
  expect(cards[0].openingDecisionManifest).toBeUndefined();
  expect(dataDiagnostics().length).toBeGreaterThan(0);
});

it("malformed_advisory_status_remains_rejected_under_current_policy", () => {
  const payload = structuredClone(preparedPayload);
  Object.assign(payload.cards[0], { encounter_evidence_status: "unknown" });
  expect(decode(payload)).toHaveLength(2);
  expect(dataDiagnostics().length).toBeGreaterThan(0);
});
