import { API_URL } from "../const";
import { confirmOperationResponse } from "./operation-status";
import { queueRecordWithCompatibleOpeningEvidence } from "../domain/adapters/practice-card-adapters";
import { queueCardSchema, queueEnvelopeSchema } from "../domain/schemas";
import type { BackendQueueCard } from "../domain/transport";
import { evaluateStudyAnswer, studyAnswerSchema, studySnapshotSchema, type StudyAnswer, type StudyAssessment } from "../domain/study-exercises";
import { localDayKey } from "../utils/local";
import { offlineTrainingDatabase as database } from "./offline-training-storage";
import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { saveEvidenceAwareReview } from "./opening-evidence-review";
import { acknowledgeOpeningReview } from "./opening-evidence-journal";

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

export class OfflineReplayError extends Error {
  constructor(message: string, readonly endpoint: string, options?: ErrorOptions) {
    super(message, options);
  }
}

export function describeOfflineQueue(prepared: PreparedTraining): string {
  const preparedTime = new Date(prepared.preparedAt);
  const preparedLabel = Number.isNaN(preparedTime.getTime())
    ? prepared.preparedAt : preparedTime.toLocaleString();
  const unsynced = prepared.attempts.filter((attempt) => !attempt.serverReviewId && !attempt.serverAcknowledged && !attempt.conflict).length;
  const needsAttention = prepared.attempts.filter((attempt) => attempt.conflict).length;
  const creditedWithWarning = prepared.attempts.filter((attempt) => attempt.syncWarning).length;
  const connectedExercises = prepared.cards.filter(requiresConnectedGrading).length;
  return `Offline queue prepared ${preparedLabel} · ${unsynced} review${unsynced === 1 ? "" : "s"} saved on phone` +
    (needsAttention ? ` · ${needsAttention} need${needsAttention === 1 ? "s" : ""} attention` : "") +
    (creditedWithWarning ? ` · ${creditedWithWarning} credited with a scheduling warning` : "") +
    (connectedExercises ? ` · ${connectedExercises} exercise${connectedExercises === 1 ? " requires" : "s require"} the computer` : "") +
    ". The live count may differ until you reconnect.";
}

const RECORD_KEY = "prepared-daily-queue";

export function requiresConnectedGrading(card: Pick<BackendQueueCard, "content_type">): boolean {
  if (card.content_type === "defense") return true;
  if (card.content_type !== "study_exercise") return false;
  return !studySnapshotSchema.safeParse((card as BackendQueueCard).study_snapshot).success;
}

export async function readPreparedTraining(): Promise<PreparedTraining | null> {
  if (typeof indexedDB === "undefined") return null;
  const opened = await database();
  return new Promise((resolve, reject) => {
    const request = opened.transaction("training").objectStore("training").get(RECORD_KEY);
    request.onsuccess = () => resolve(request.result ?? null);
    request.onerror = () => reject(request.error);
  });
}

export async function discardOfflineConflict(localEntryId: number): Promise<PreparedTraining> {
  return updatePreparedTraining((current) => {
    if (!current?.attempts.some((attempt) => attempt.localEntryId === localEntryId && attempt.conflict))
      throw new Error("This saved conflict is no longer available. Reopen Review conflicts to refresh the list.");
    return { ...current, attempts: current.attempts.filter((attempt) => attempt.localEntryId !== localEntryId) };
  });
}

async function updatePreparedTraining(
  update: (current: PreparedTraining | null) => PreparedTraining,
): Promise<PreparedTraining> {
  const opened = await database();
  return new Promise((resolve, reject) => {
    const transaction = opened.transaction("training", "readwrite");
    const store = transaction.objectStore("training");
    let updated: PreparedTraining;
    const request = store.get(RECORD_KEY);
    request.onsuccess = () => {
      try {
        updated = update(request.result ?? null);
        store.put(updated, RECORD_KEY);
      } catch (error) {
        transaction.abort();
        reject(error);
      }
    };
    transaction.oncomplete = () => resolve(updated);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export async function savePreparedTraining(raw: unknown): Promise<PreparedTraining> {
  if (!raw || typeof raw !== "object" || typeof (raw as { prepared_at?: unknown }).prepared_at !== "string")
    throw new Error("Prepared queue has no preparation time");
  const queuePayload = { ...(raw as Record<string, unknown>) };
  delete queuePayload.prepared_at;
  const envelope = queueEnvelopeSchema.parse(queuePayload);
  if (!envelope.local_date || envelope.projection?.state !== "ready" || envelope.count !== envelope.cards.length)
    throw new Error("The daily queue is not fully prepared yet");
  const cards = envelope.cards.map((card) => queueCardSchema.parse(queueRecordWithCompatibleOpeningEvidence(card)));
  const preparedAt = (raw as { prepared_at: string }).prepared_at;
  return updatePreparedTraining((current) => {
    if (current?.attempts.some((attempt) => !attempt.serverReviewId && !attempt.serverAcknowledged && !attempt.conflict))
      return current;
    if (current && current.localDate === envelope.local_date &&
        Date.parse(current.preparedAt) > Date.parse(preparedAt))
      return current;
    return {
      localDate: envelope.local_date!, preparedAt, cards,
      attempts: current?.attempts.filter((attempt) => attempt.conflict || attempt.syncWarning) ?? [],
      nextTemporaryId: 1_000_000_000_000,
    };
  });
}

export function offlineRepeatPosition(
  card: BackendQueueCard,
  outcome: "again" | "correct",
): number | null {
  if (outcome === "again") return 4;
  if (card.scheduling_mode === "light" || card.scheduling_mode === "hard") return null;
  return card.first_correct_at ? null : Number.POSITIVE_INFINITY;
}

export async function markOfflineAttemptFailed(localEntryId: number): Promise<void> {
  await updatePreparedTraining((current) => {
    if (!current || current.localDate !== localDayKey())
      throw new Error("The prepared queue is from another day. Reconnect before reviewing.");
    const cardIndex = current.cards.findIndex((card) => card.queue_entry_id === localEntryId);
    if (cardIndex < 0) throw new Error("The failed card is no longer in the prepared queue.");
    return { ...current, cards: current.cards.map((card, index) =>
      index === cardIndex ? { ...card, attempt_failed: true } : card) };
  });
}

export async function recordOfflineAttempt(
  localEntryId: number,
  outcome: "again" | "correct",
  guided: boolean,
  studyResponse?: { studyId: string; exerciseId: string; attemptId: string; answer: StudyAnswer;
    assessment: StudyAssessment; selfRating?: "correct" | "again" },
  openingResponse?: { attemptId: string; completion?: OpeningEvidenceCheckpoint },
): Promise<PreparedTraining> {
  return updatePreparedTraining((current) => {
    if (!current || current.localDate !== localDayKey())
      throw new Error("The prepared queue is from another day. Reconnect before reviewing.");
    const cardIndex = current.cards.findIndex((queuedCard) => queuedCard.queue_entry_id === localEntryId);
    const card = current.cards[cardIndex];
    if (!card)
      throw new Error("The active card differs from the saved phone queue. Reload before reviewing.");
    if (requiresConnectedGrading(card))
      throw new Error("This exercise requires the computer's grading service.");
    const scheduledOutcome = guided ? "again" : outcome;
    let recentOutcomes: string[] = [];
    try { recentOutcomes = JSON.parse(card.recent_attempts_json ?? "[]") as string[]; }
    catch { throw new Error("The prepared card has invalid attempt history. Reconnect before reviewing."); }
    recentOutcomes = [scheduledOutcome, ...recentOutcomes].slice(0, 5);
    const nextSchedulingMode = card.scheduling_mode === "light" && scheduledOutcome === "again"
      ? "normal"
      : card.scheduling_mode !== "hard" && recentOutcomes.filter((item) => item === "again").length >= 3
        ? "hard" : card.scheduling_mode;
    const repeatPosition = offlineRepeatPosition(
      { ...card, scheduling_mode: nextSchedulingMode }, scheduledOutcome,
    );
    const parent = current.attempts.find((attempt) => attempt.localEntryId === card.parent_local_entry_id);
    const attempt: OfflineAttempt = {
      localEntryId, parentLocalEntryId: card.parent_local_entry_id,
      cardId: card.id, outcome, guided, completedAt: openingResponse?.completion?.terminal?.ended_at ?? new Date().toISOString(),
      expectedReviewId: parent?.serverReviewId ?? card.latest_review_id ?? 0,
      expectedRevision: card.revision ?? 1,
      queueCycle: card.cycle ?? 0,
      ...(studyResponse ?? {}),
      ...(openingResponse ? { attemptId: openingResponse.attemptId, openingEvidenceCompletion: openingResponse.completion } : {}),
    };
    const remaining = current.cards.filter((_, index) => index !== cardIndex);
    let nextTemporaryId = current.nextTemporaryId;
    if (repeatPosition !== null) {
      const repeatedCard = {
        ...card,
        queue_entry_id: nextTemporaryId++,
        parent_local_entry_id: localEntryId,
        cycle: (card.cycle ?? 0) + 1,
        attempt_state: scheduledOutcome === "again" ? "guided" : "reinforcement",
        attempt_failed: false,
        first_correct_at: scheduledOutcome === "correct" ? attempt.completedAt : card.first_correct_at,
        recent_attempts_json: JSON.stringify(recentOutcomes),
        scheduling_mode: nextSchedulingMode,
        ...(card.opening_decision_manifest ? {
          opening_evidence_origin_queue_entry_id: card.opening_evidence_origin_queue_entry_id ?? card.queue_entry_id,
          opening_evidence_parent_attempt_id: attempt.attemptId,
        } : {}),
      } as BackendQueueCard;
      remaining.splice(Math.min(repeatPosition, remaining.length), 0, repeatedCard);
    }
    return { ...current, cards: remaining, attempts: [...current.attempts, attempt], nextTemporaryId };
  });
}

export async function recordOfflineStudyAttempt(localEntryId: number, studyId: string,
  answer: StudyAnswer, selfRating?: "correct" | "again", guided = false): Promise<{ prepared: PreparedTraining; assessment: StudyAssessment }> {
  const current = await readPreparedTraining();
  const card = current?.cards.find((item) => item.queue_entry_id === localEntryId);
  if (!card || card.content_type !== "study_exercise" || !card.study_snapshot || !card.study_exercise_id)
    throw new Error("This prepared study exercise is unavailable. Reconnect before reviewing.");
  const snapshot = studySnapshotSchema.parse(card.study_snapshot);
  const response = studyAnswerSchema.parse(answer);
  const assessment = evaluateStudyAnswer(snapshot.specification, response, snapshot.fen);
  if (assessment.outcome === "invalid_submission") throw new Error(assessment.feedback);
  if (assessment.outcome === "unrecognized" || assessment.outcome === "needs_self_assessment") {
    if (!selfRating) throw new Error("Compare with the rubric and choose a self-assessment before saving offline.");
  }
  const outcome = selfRating ?? (assessment.outcome === "correct" ? "correct" : "again");
  const prepared = await recordOfflineAttempt(localEntryId, outcome, guided || Boolean(card.attempt_failed), {
    studyId, exerciseId: card.study_exercise_id, attemptId: crypto.randomUUID(),
    answer: response, assessment, selfRating,
  });
  return { prepared, assessment };
}

async function performReplayOfflineAttempts(): Promise<PreparedTraining | null> {
  let current = await readPreparedTraining();
  if (!current) return null;
  for (const [attemptIndex, attempt] of current.attempts.entries()) {
    if (attempt.serverReviewId || attempt.serverAcknowledged || (attempt.conflict && attempt.studyId)) continue;
    if (current.attempts.slice(0, attemptIndex).some((earlier) => earlier.cardId === attempt.cardId && earlier.conflict)) {
      current = await updatePreparedTraining((saved) => ({
        ...saved!, attempts: saved!.attempts.map((item) => item.localEntryId === attempt.localEntryId
          ? { ...item, conflict: "An earlier phone attempt for this card conflicted with the computer" } : item),
      }));
      continue;
    }
    const parent = current.attempts.find((candidate) => candidate.localEntryId === attempt.parentLocalEntryId);
    if (parent?.conflict) {
      current = await updatePreparedTraining((saved) => ({
        ...saved!, attempts: saved!.attempts.map((item) => item.localEntryId === attempt.localEntryId
          ? { ...item, conflict: "An earlier repeat conflicted with a computer review" } : item),
      }));
      continue;
    }
    const serverEntryId = parent?.serverRepeatEntryId ?? parent?.serverEntryId ?? attempt.localEntryId;
    const expectedReviewId = parent?.serverReviewId ?? attempt.expectedReviewId;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5_000);
    const reviewEndpoint = attempt.studyId && attempt.exerciseId && attempt.answer && attempt.attemptId
      ? `${API_URL}/api/studies/${encodeURIComponent(attempt.studyId)}/exercises/${encodeURIComponent(attempt.exerciseId)}/attempts`
      : `${API_URL}/api/cards/${encodeURIComponent(attempt.cardId)}/review`;
    let response: Response;
    try {
      if (attempt.studyId && attempt.exerciseId && attempt.answer && attempt.attemptId) {
        response = await fetch(reviewEndpoint, {
          method: "POST", headers: { "Content-Type": "application/json",
            "Idempotency-Key": `phone-study:${attempt.attemptId}` }, signal: controller.signal,
          body: JSON.stringify({ attempt_id: attempt.attemptId, revision: attempt.expectedRevision,
            answer: attempt.answer, context: "review", card_id: attempt.cardId,
            queue_entry_id: serverEntryId, queue_cycle: attempt.queueCycle ?? 0,
            expected_review_id: expectedReviewId, hint_seen: attempt.guided }),
        });
      } else {
        const reviewAttemptId = attempt.attemptId ?? `phone:${attempt.localEntryId}:${attempt.completedAt}`;
        const completion = attempt.openingEvidenceCompletion
          ? { ...attempt.openingEvidenceCompletion, queue_entry_id: serverEntryId } : undefined;
        response = await saveEvidenceAwareReview({
          endpoint: reviewEndpoint, operationKey: `phone-reconcile:${reviewAttemptId}`, signal: controller.signal,
          completion, evidenceRejected: attempt.openingEvidenceRejected,
          onEvidenceRejected: async (message) => {
            await updatePreparedTraining((saved) => ({ ...saved!, attempts: saved!.attempts.map((item) =>
              item.localEntryId === attempt.localEntryId ? { ...item, openingEvidenceRejected: message } : item) }));
          },
          body: {
            queue_entry_id: serverEntryId, outcome: attempt.outcome, guided: attempt.guided,
            recorded_at: attempt.completedAt, expected_review_id: expectedReviewId,
            expected_revision: attempt.expectedRevision,
            attempt_id: reviewAttemptId,
          },
        });
      }
      response = await confirmOperationResponse(response);
    } catch (error) {
      throw new OfflineReplayError(`Could not sync saved review. ${String(error)}`, reviewEndpoint, { cause: error });
    } finally {
      window.clearTimeout(timeout);
    }
    if (response.status === 409) {
      const detail = (await response.json().catch(() => ({}))) as { detail?: string };
      current = await updatePreparedTraining((saved) => ({
        ...saved!, attempts: saved!.attempts.map((item) => item.localEntryId === attempt.localEntryId
          ? { ...item, conflict: detail.detail ?? "This review conflicts with the computer" } : item),
      }));
      continue;
    }
    if (!response.ok) throw new OfflineReplayError(`Could not sync phone review (HTTP ${response.status})`, reviewEndpoint);
    let result = await response.json() as { persisted?: boolean; review_id: number | null; requeue_entry_id: number | null;
      warning?: string; reconciliation?: string;
      competing_review?: { outcome?: string | null; completed_at?: string | null };
      pending_self_assessment?: boolean; review?: { review_id: number; requeue_entry_id: number | null } };
    if (result.pending_self_assessment && attempt.studyId && attempt.exerciseId && attempt.attemptId && attempt.selfRating) {
      const assessmentEndpoint = `${API_URL}/api/studies/${encodeURIComponent(attempt.studyId)}/exercises/${encodeURIComponent(attempt.exerciseId)}/attempts/${encodeURIComponent(attempt.attemptId)}/self-assess`;
      let assessed: Response;
      try {
        assessed = await fetch(assessmentEndpoint, {
          method: "POST", headers: { "Content-Type": "application/json",
            "Idempotency-Key": `phone-assessment:${attempt.attemptId}` },
          body: JSON.stringify({ rating: attempt.selfRating }),
        });
        assessed = await confirmOperationResponse(assessed);
      } catch (error) {
        throw new OfflineReplayError(`Could not sync study self-assessment. ${String(error)}`, assessmentEndpoint, { cause: error });
      }
      if (assessed.status === 409) {
        const detail = (await assessed.json().catch(() => ({}))) as { detail?: string };
        current = await updatePreparedTraining((saved) => ({ ...saved!, attempts: saved!.attempts.map((item) => item.localEntryId === attempt.localEntryId
          ? { ...item, conflict: detail.detail ?? "Study revision changed before sync" } : item) }));
        continue;
      }
      if (!assessed.ok) throw new OfflineReplayError(`Could not sync study self-assessment (HTTP ${assessed.status})`, assessmentEndpoint);
      result = await assessed.json() as typeof result;
    }
    if (result.review) result = { ...result.review };
    if (!Number.isInteger(result.review_id) && !(result.persisted && result.reconciliation === "history_only"))
      throw new Error("Review sync did not confirm a saved review");
    if (attempt.openingEvidenceCompletion && attempt.attemptId)
      void acknowledgeOpeningReview(attempt.attemptId).catch(() => undefined);
    current = await updatePreparedTraining((saved) => ({
      ...saved!, attempts: saved!.attempts.map((item) => item.localEntryId === attempt.localEntryId
        ? { ...item, serverEntryId, serverReviewId: result.review_id ?? undefined,
            serverAcknowledged: true, conflict: undefined,
            syncWarning: result.warning ? `${result.warning} Other saved result: ${result.competing_review?.outcome ?? "unknown"} ` +
              `at ${result.competing_review?.completed_at ?? "unknown"}.` : undefined,
            serverRepeatEntryId: result.requeue_entry_id } : item),
    }));
  }
  return current;
}

let activeReplay: Promise<PreparedTraining | null> | undefined;

export function replayOfflineAttempts(): Promise<PreparedTraining | null> {
  activeReplay ??= performReplayOfflineAttempts().finally(() => { activeReplay = undefined; });
  return activeReplay;
}
