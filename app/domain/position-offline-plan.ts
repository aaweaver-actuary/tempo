import * as z from "zod";
import {
  decodePositionPresentation,
  type PositionPresentation,
} from "./position-presentation";

/** JSON-safe due/credit facts captured with the server issue. */
export type PositionDueSnapshot = Readonly<Record<string, unknown>>;

/** A local copy of a server issue. Its digest is an identifier, not proof of issuance. */
export type PositionOfflinePlan = Readonly<{
  version: 1;
  position_study_mode: "position";
  position_presentation: PositionPresentation;
  due_snapshot: PositionDueSnapshot;
}>;

const envelopeSchema = z.strictObject({
  version: z.literal(1),
  position_study_mode: z.literal("position"),
  position_presentation: z.unknown(),
  due_snapshot: z.record(z.string(), z.unknown()),
});

function freezeJson(value: unknown): unknown {
  if (Array.isArray(value)) return Object.freeze(value.map(freezeJson));
  if (value && typeof value === "object") {
    return Object.freeze(Object.fromEntries(Object.entries(value).map(([key, item]) => [key, freezeJson(item)])));
  }
  return value;
}

function cloneJsonRecord(raw: unknown): PositionDueSnapshot {
  const parsed = z.record(z.string(), z.unknown()).parse(raw);
  // JSON is the browser storage representation. Reject values that would be
  // silently dropped or changed during refresh/reload instead of sealing them.
  const assertJson = (value: unknown, seen = new Set<object>()): void => {
    if (value === null || typeof value === "string" || typeof value === "boolean") return;
    if (typeof value === "number" && Number.isFinite(value)) return;
    if (Array.isArray(value)) {
      if (seen.has(value)) throw new Error("position_offline_due_snapshot_not_json");
      seen.add(value);
      value.forEach((item) => assertJson(item, seen));
      seen.delete(value);
      return;
    }
    if (value && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
      if (seen.has(value)) throw new Error("position_offline_due_snapshot_not_json");
      seen.add(value);
      Object.values(value).forEach((item) => assertJson(item, seen));
      seen.delete(value);
      return;
    }
    throw new Error("position_offline_due_snapshot_not_json");
  };
  assertJson(parsed);
  const encoded = JSON.stringify(parsed);
  if (encoded === undefined) throw new Error("position_offline_due_snapshot_not_json");
  const decoded: unknown = JSON.parse(encoded);
  if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) {
    throw new Error("position_offline_due_snapshot_not_object");
  }
  return freezeJson(decoded) as PositionDueSnapshot;
}

/** Build a duplicate-safe immutable payload while retaining the exact issued suffix. */
export function sealPositionOfflinePlan(
  rawPresentation: unknown,
  dueSnapshot: unknown,
): PositionOfflinePlan {
  const plan = decodePositionPresentation(rawPresentation);
  return Object.freeze({
    version: 1 as const,
    position_study_mode: "position" as const,
    position_presentation: plan,
    due_snapshot: cloneJsonRecord(dueSnapshot),
  });
}

/** Strictly decode a persisted position payload; unknown versions fail closed. */
export function decodePositionOfflinePlan(raw: unknown): PositionOfflinePlan {
  const envelope = envelopeSchema.parse(raw);
  return sealPositionOfflinePlan(envelope.position_presentation, envelope.due_snapshot);
}

/** Stable browser-storage roundtrip, preserving all identity and suffix fields. */
export function roundTripPositionOfflinePlan(raw: unknown): PositionOfflinePlan {
  const decoded = decodePositionOfflinePlan(raw);
  const serialized = JSON.stringify(decoded);
  if (serialized === undefined) throw new Error("position_offline_plan_not_json");
  return decodePositionOfflinePlan(JSON.parse(serialized));
}

export type PositionOfflineGateInput = Readonly<{
  positionStudyMode?: "position";
  positionPresentation?: unknown;
  positionStudyHold?: string;
  sealedPositionPlan?: unknown;
  originalCardId?: string;
  originalQueueEntryId?: number;
  originalCardRevision?: number;
  expectedAttemptId?: string;
}>;

export type PositionOfflineHold =
  | "legacy_card"
  | "online_only_position_study"
  | "position_plan_missing"
  | "position_plan_invalid"
  | "position_plan_identity_mismatch"
  | "position_plan_owner_unproven";

/**
 * Offline attempts are unsupported for Position Study v1. This is the shared
 * gate for consumers: legacy cards stay on their existing path, while every
 * position-mode card is held even when its cached issue decodes successfully.
 */
export function getPositionStudyOfflineHold(input: PositionOfflineGateInput): PositionOfflineHold {
  if (input.positionStudyMode !== "position") return "legacy_card";
  if (input.positionStudyHold) return "position_plan_owner_unproven";
  if (input.sealedPositionPlan === undefined) return "position_plan_missing";
  let sealed: PositionOfflinePlan;
  try {
    sealed = decodePositionOfflinePlan(input.sealedPositionPlan);
    const plan = sealed.position_presentation;
    if ((input.originalCardId !== undefined && plan.original_card_id !== input.originalCardId) ||
        (input.originalQueueEntryId !== undefined && plan.queue_entry_id !== input.originalQueueEntryId) ||
        (input.originalCardRevision !== undefined && plan.original_card_revision !== input.originalCardRevision) ||
        (input.expectedAttemptId !== undefined && plan.attempt_id !== input.expectedAttemptId)) {
      return "position_plan_identity_mismatch";
    }
    if (input.positionPresentation !== undefined) {
      const presentation = decodePositionPresentation(input.positionPresentation);
      if (JSON.stringify(presentation) !== JSON.stringify(sealed.position_presentation)) {
        return "position_plan_identity_mismatch";
      }
    }
  } catch {
    return "position_plan_invalid";
  }
  return "online_only_position_study";
}

/** True only when the existing legacy offline-training semantics apply. */
export function canReplayPositionStudyOffline(input: PositionOfflineGateInput): boolean {
  return getPositionStudyOfflineHold(input) === "legacy_card";
}

