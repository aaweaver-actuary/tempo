/** Prepared phone queue payload shared by application policy and browser storage. */
import type { BackendQueueCard } from "./transport";
import type { OpeningEvidenceCheckpoint } from "./opening-evidence";
import type { StudyAnswer, StudyAssessment } from "./study-exercises";

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
  selfRating?: "correct" | "again";
};

export type PreparedTraining = {
  localDate: string;
  preparedAt: string;
  cards: BackendQueueCard[];
  attempts: OfflineAttempt[];
  nextTemporaryId: number;
};
