/** Prepared phone queue payload shared by application policy and browser storage. */
import type { BackendQueueCard } from "./transport";
import type { OpeningEvidenceCheckpoint } from "./opening-evidence";
import type { StudyAnswer, StudyAssessment } from "./study-exercises";
import type { PositionOfflinePlan } from "./position-offline-plan";

export type OfflineGradingProvenance = { server_contract_version: number; browser_implementation_revision: number };

/** Missing provenance is historical, not evidence of any particular grader. */
export function classifyOfflineGradingProvenance(value: unknown): "legacy_unknown" | "current" | "supported-other" | "unsupported" {
  if (value === undefined) return "legacy_unknown";
  if (!value || typeof value !== "object" || Array.isArray(value)) return "unsupported";
  const provenance = value as Record<string, unknown>;
  if (Object.keys(provenance).length !== 2 || provenance.server_contract_version !== 1 ||
      !Number.isInteger(provenance.browser_implementation_revision)) return "unsupported";
  if (provenance.browser_implementation_revision === 2) return "current";
  return "unsupported";
}

export type OfflineAttempt = {
  localEntryId: number;
  parentLocalEntryId?: number;
  cardId: string;
  outcome: "again" | "correct";
  guided: boolean;
  completedAt: string;
  expectedReviewId: number;
  expectedRevision: number;
  queueCycle?: number;
  serverEntryId?: number;
  serverReviewId?: number;
  serverRepeatEntryId?: number | null;
  conflict?: string;
  syncWarning?: string;
  serverAcknowledged?: boolean;
  studyId?: string;
  exerciseId?: string;
  attemptId?: string;
  openingEvidenceCompletion?: OpeningEvidenceCheckpoint;
  openingEvidenceRejected?: string;
  openingEvidenceFallbackReason?: "local_storage_quota";
  answer?: StudyAnswer;
  assessment?: StudyAssessment;
  gradingProvenance?: OfflineGradingProvenance;
  serverAssessment?: StudyAssessment;
  selfRating?: "correct" | "again";
};

export type PreparedTraining = {
  localDate: string;
  preparedAt: string;
  cards: BackendQueueCard[];
  attempts: OfflineAttempt[];
  nextTemporaryId: number;
  /** Immutable v1 position issues accompany, but never rewrite, legacy queue cards. */
  positionPlans?: PositionOfflinePlan[];
};
