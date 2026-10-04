import { positionsFromMoves, useBoardHistory } from "../hooks/use-board-history";
import { Button } from "../components/buttons/BaseButton";
import { BoardHeading, BoardTools } from "../components/board/board-workspace";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import TrainingViewHeader from "./headers/TrainingViewHeader";
import RetryButton from "../components/buttons/RetryButton";
import EndgamesView from "./endgames_view";
import { PracticeCard } from "../types";
import { BoardTheme, Chessboard, PieceSet } from "../components/chessboard";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import { OutcomeFlash } from "../components/board/OutcomeFlash";
import AgainButton from "../components/buttons/AgainButton";
import AnalyzeOnLichessButton from "../components/buttons/AnalyzeOnLichessButton";
import EditCardButton from "../components/buttons/EditCardButton";
import StudyReviewBadge from "../components/StudyReviewBadge";
import RestartButton from "../components/buttons/RestartButton";
import FailureNote from "../components/FailureNote";
import FeedbackIcon from "../components/feedback/FeedbackIcon";
import FeedbackText from "../components/feedback/FeedbackText";
import OpeningTitle from "../components/OpeningTitle";
import type { AssistanceKind } from "../domain/opening-evidence";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { usePhoneViewport } from "../hooks/use-phone-viewport";
import { ActionMenu } from "../components/ui";
import { publishNotification, resolveNotification, updateNotification } from "../lib/notifications";
import { usesLocalApi } from "../utils/local";
import { Square } from "chess.js";
import { getFeedbackCopy } from "./getFeedbackCopy";
import { trainedColor } from "../utils/cards";
import {
  useTrainingStore,
  selectTrainingViewState,
} from "../state/training-store";
import { annotationToShapes } from "../utils/position-annotations";
import { useShallow } from "zustand/react/shallow";
import DefenseTrainingView from "./defense_training_view";
import StudyExerciseRunner from "./study_exercise_runner";

interface TrainingViewProps {
  repairNotice?: ReactNode;
  dateLabel: string;
  serviceError: string;
  offlineQueue?: boolean;
  refreshDatabaseQueue: (advance?: boolean) => void;
  cardsLeft: number;
  card: PracticeCard;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  rateCard: (outcome: "again" | "correct") => Promise<void>;
  reviewPersistenceState?:
    | "idle"
    | "saving"
    | "saveFailed"
    | "saved"
    | "refreshingQueue"
    | "queueFailed";
  reviewSaveError?: string;
  retryReviewSave?: () => void;
  retryQueueAfterReview?: () => void;
  handleAttemptFailure: () => void;
  resetCardAttempt: () => void;
  setEditorCard: (card: PracticeCard | null) => void;
  onAcceptPrefixSplit?: (card: PracticeCard) => Promise<void>;
  onRejectPrefixSplit?: (card: PracticeCard) => Promise<void>;
  onOpeningAssistance?: (moveOffset: number, kind: AssistanceKind) => void;
  onMove: (from: Square, to: Square) => void;
  onOpenPosition?: (target: "analysis" | "builder" | "games" | "compare") => void;
  useSharedBoard?: boolean;
  onDefenseGraded?: () => Promise<void>;
  onBury?: () => Promise<void>;
  burialPending?: boolean;
  burialRecoveryError?: string;
}

function StandardTrainingView({
  repairNotice,
  dateLabel,
  serviceError,
  offlineQueue = false,
  cardsLeft,
  refreshDatabaseQueue,
  card,
  boardTheme,
  pieceSet,
  rateCard,
  reviewPersistenceState = "idle",
  reviewSaveError = "",
  retryReviewSave,
  retryQueueAfterReview = () => undefined,
  handleAttemptFailure,
  resetCardAttempt,
  setEditorCard,
  onAcceptPrefixSplit = async () => { throw new Error("Prefix splitting is unavailable. Reload training."); },
  onRejectPrefixSplit = async () => { throw new Error("Prefix splitting is unavailable. Reload training."); },
  onMove,
  onOpeningAssistance,
  onOpenPosition = () => undefined,
  onBury = async () => undefined,
  burialPending = false,
  useSharedBoard = false,
}: TrainingViewProps) {
  const [burying, setBurying] = useState(false);
  const phoneViewport = usePhoneViewport();
  const phoneOpening = phoneViewport && card.kind === "opening" && cardsLeft > 0;
  const [studyDetailsExpanded, setStudyDetailsExpanded] = useState(false);
  const [prefixSplitPendingAction, setPrefixSplitPendingAction] = useState<"accept" | "reject" | null>(null);
  const [rejectedPrefixOfferKey, setRejectedPrefixOfferKey] = useState("");
  const [prefixSplitError, setPrefixSplitError] = useState<{ key: string; message: string }>();
  const reviewNotificationId = useRef<string | undefined>(undefined);
  const prefixOfferKey = `${card.backendId ?? card.id}:${card.revision ?? 1}:${card.prefixSplitLatestFailureId ?? 0}`;
  const {
    boardAttempt,
    attemptFailed,
    lastMove,
    opponentLastMove,
    isLocked,
    step,
    feedback,
    showHint,
    queueNotice,
    failureAnnotation,
    failureFen,
    currentFenString,
    teachingEncounterKey,
  } = useTrainingStore(useShallow(selectTrainingViewState));
  const attemptGeneration = useTrainingStore((state) => state.attempt.generation);
  const reviewBlocked = reviewPersistenceState === "saveFailed";

  useEffect(() => {
    const source = "training review";
    if (reviewPersistenceState === "saving") {
      reviewNotificationId.current = publishNotification({ severity: "info", source,
        key: `review-save:${card.queueEntryId ?? card.id}`, message: "Saving result…", active: true });
    } else if (reviewPersistenceState === "saveFailed") {
      if (reviewNotificationId.current) updateNotification(reviewNotificationId.current, { severity: "error", active: false, message: reviewSaveError });
      else publishNotification({ severity: "error", source, key: `review-save:${card.queueEntryId ?? card.id}`, message: reviewSaveError });
      reviewNotificationId.current = undefined;
    } else if (reviewPersistenceState === "saved") {
      if (reviewNotificationId.current) resolveNotification(reviewNotificationId.current, { severity: "success", message: "Result saved." });
      reviewNotificationId.current = undefined;
    } else if (reviewPersistenceState === "refreshingQueue") {
      reviewNotificationId.current = publishNotification({ severity: "info", source,
        key: `review-queue:${card.queueEntryId ?? card.id}`, message: "Result saved. Loading the next card…", active: true });
    } else if (reviewPersistenceState === "queueFailed") {
      if (reviewNotificationId.current) updateNotification(reviewNotificationId.current, { severity: "warning", active: false, message: "Result saved; the next card could not be loaded." });
      else publishNotification({ severity: "warning", source, key: `review-queue:${card.queueEntryId ?? card.id}`, message: "Result saved; the next card could not be loaded." });
      reviewNotificationId.current = undefined;
    } else if (reviewNotificationId.current) {
      resolveNotification(reviewNotificationId.current, { severity: "success", message: "Result saved. Next card loaded." });
      reviewNotificationId.current = undefined;
    }
  }, [card.id, card.queueEntryId, reviewPersistenceState, reviewSaveError]);
  const liveQueueBlocked = Boolean(serviceError && !offlineQueue);
  const trainingMutationBlocked = liveQueueBlocked || reviewBlocked || burialPending;
  const isEndgame = card.kind === "endgame";
  const feedbackCopy = getFeedbackCopy(attemptFailed, card)[feedback];
  const playerName = trainedColor(card) === "white" ? "White" : "Black";
  const currentMoveKey = `${card.backendId ?? card.id}:${card.revision ?? 1}:${step}`;
  const showTeachingArrow =
    showHint ||
    (card.kind === "opening" &&
      !card.hasPriorStudyReview &&
      !card.firstCleanPassAt &&
      card.queueAttemptState !== "reinforcement" &&
      teachingEncounterKey === currentMoveKey);
  const isFailedPosition = attemptFailed && currentFenString === failureFen;
  const trainingShapes = useMemo<DrawShape[]>(
    () => [
      ...(opponentLastMove
        ? [
            {
              orig: opponentLastMove[0] as Key,
              dest: opponentLastMove[1] as Key,
              brush: "red",
            } as DrawShape,
          ]
        : []),
      ...(isFailedPosition ? annotationToShapes(failureAnnotation) : []),
    ],
    [opponentLastMove, isFailedPosition, failureAnnotation],
  );
  const revealedMoves = card.moves.slice(
    0,
    feedback === "complete" ? card.moves.length : step,
  );

  const trainingPositionKey = `${card.queueEntryId ?? card.id}:${card.queueCycle ?? 0}:${card.revision ?? 1}:${attemptGeneration}`;
  const visiblePositions = useMemo(() => positionsFromMoves(card.startingFen, card.moves, feedback === "complete" ? card.moves.length : step), [card.startingFen, card.moves, feedback, step]);
  const boardHistory = useBoardHistory(trainingPositionKey, visiblePositions, currentFenString);
  function observeDisplayedHint(displayedFen: string) {
    if (displayedFen === currentFenString && cardsLeft > 0 && card.kind === "opening" && showTeachingArrow && !boardHistory.viewingHistory && !isLocked && !trainingMutationBlocked) {
      onOpeningAssistance?.(step, feedback === "wrong" ? "revealed" : attemptFailed ? "guided" : showHint ? "hint" : "teaching");
    }
  }
  useBoardPublisher("train", useSharedBoard && !isEndgame ? {
    keyboard: boardHistory.keyboard,
    positionKey: trainingPositionKey,
    unavailable:
      cardsLeft <= 0
        ? serviceError
          ? "Training position unavailable. Retry the local service."
          : /requires? the computer/.test(queueNotice)
            ? "Prepared exercises require the computer. Reconnect to continue."
            : "No cards due. Your next session will appear here."
        : undefined,
    fen: boardHistory.fen,
    expectedSan: card.moves[step],
    lastMove: boardHistory.viewingHistory ? undefined : lastMove,
    interactionMode:
      boardHistory.viewingHistory || isLocked ||
      trainingMutationBlocked ||
      step >= card.moves.length ||
      cardsLeft === 0
        ? "readonly"
        : "legal",
    showHint: cardsLeft > 0 && !boardHistory.viewingHistory && showTeachingArrow,
    onHintExposure: observeDisplayedHint,
    theme: boardTheme,
    pieceSet,
    orientation: card.orientation === "black" ? "black" : "white",
    shapes: boardHistory.viewingHistory ? [] : trainingShapes,
    positionRevision: boardAttempt,
    onMove,
    onSquareSelect: undefined,
    onFreeMove: undefined,
    onDrawnShapesChange: undefined,
    onFlip: undefined,
  } : null);

  function handleAnalyzeOnLichessClick() {
    if (!attemptFailed && !trainingMutationBlocked) void rateCard("again");
  }

  const buryAction = (
    <Button
      type="button"
      disabled={
        burying ||
        isLocked ||
        trainingMutationBlocked ||
        feedback === "complete" ||
        reviewPersistenceState === "saving" ||
        reviewPersistenceState === "refreshingQueue"
      }
      onClick={() => void runBury()}
    >
      {burying ? "Burying…" : "Bury"}
    </Button>
  );
  const showMoveAction = (
    <AgainButton
      handleAgain={handleAttemptFailure}
      isAttemptFailed={attemptFailed}
      isFeedbackComplete={feedback === "complete"}
      hasNoCardsLeft={cardsLeft === 0}
      isReviewBlocked={trainingMutationBlocked}
    />
  );
  const secondaryActions = (<>
    <RestartButton
      handleRestart={resetCardAttempt}
      disabled={trainingMutationBlocked}
    />
    <AnalyzeOnLichessButton
      moves={card.moves.slice(0, step)}
      fen={card.startingFen}
      onClick={handleAnalyzeOnLichessClick}
    />
    <EditCardButton
      disabled={burialPending}
      card={card}
      setEditorCard={(value) => setEditorCard(value)}
    />
  </>);

  const studyMetadata = (
    <div className="card-meta">
      <span
        className={`pill${card.kind === "puzzle" ? " puzzle" : ""}`}
      >
        {card.kind === "puzzle" ? "Puzzle" : "Review"}
      </span>
      {card.queueAttemptState === "reinforcement" && (
        <span className="pill">Reinforcement</span>
      )}
      <StudyReviewBadge count={card.priorStudyReviewCount} />
      {card.priorityReason && (
        <span className="pill">{card.priorityReason}</span>
      )}
      {card.encounterBadges?.map((badge) => (
        <span
          key={badge}
          className="pill"
          title={`${card.encounterCount30d ?? 0} distinct games in 30 days${card.lastEncounteredAt ? ` · latest ${card.lastEncounteredAt.slice(0, 10)}` : ""}`}
        >
          {badge}
        </span>
      ))}
      {!phoneOpening && !offlineQueue && /^(Again|Valid repertoire move|Cannot verify)/.test(queueNotice) && <em>{queueNotice}</em>}
    </div>
  );
  const moveTrail = (
    <div
      className={`move-trail${revealedMoves.length ? "" : " empty"}`}
      aria-live="polite"
    >
      <span>Moves played</span>
      {revealedMoves.length ? (
        <ol>
          {revealedMoves.map((move, index) => (
            <li key={`${move}-${index}`}>
              <b>
                {index % 2 === 0
                  ? `${Math.floor(index / 2) + 1}.`
                  : "..."}
              </b>
              {move}
            </li>
          ))}
        </ol>
      ) : (
        <p>Nothing is revealed until you play it.</p>
      )}
    </div>
  );
  const positionActions = (
    <div className="position-actions" aria-label="Open review position">
      {usesLocalApi() && card.kind === "opening" && (attemptFailed || feedback === "complete") && (
        <Button onClick={() => onOpenPosition("compare")}>Compare positions</Button>
      )}
      <Button onClick={() => onOpenPosition("analysis")}>
        Analysis
      </Button>
      <Button onClick={() => onOpenPosition("builder")}>Builder</Button>
      <Button onClick={() => onOpenPosition("games")}>
        Games here
      </Button>
    </div>
  );

  return (
    <>
      {!phoneOpening && repairNotice}
      {!phoneOpening && <TrainingViewHeader
        dateLabel={dateLabel}
        serviceError={serviceError}
        cardsLeft={cardsLeft}
        queueNotice={offlineQueue ? "" : queueNotice}
      />}
      {serviceError && (
        <div role="alert">
          {serviceError}{" "}
          <RetryButton onRetry={() => void refreshDatabaseQueue(serviceError.includes("no longer in today's queue"))} />
        </div>
      )}
      {reviewPersistenceState === "saveFailed" && (
        <div role="alert">
          {reviewSaveError}{" "}
          <Button
            onClick={() =>
              retryReviewSave
                ? retryReviewSave()
                : void rateCard(attemptFailed ? "again" : "correct")
            }
          >
            Retry save
          </Button>
        </div>
      )}
      {reviewPersistenceState === "queueFailed" && (
        <div role="alert">
          Result saved; the next card could not be loaded.{" "}
          <Button onClick={retryQueueAfterReview}>
            Retry loading the queue
          </Button>
        </div>
      )}
      {cardsLeft > 0 && isEndgame && (
        <EndgamesView
          key={card.queueEntryId}
          scheduledCard={card}
          onReview={(outcome) => void rateCard(outcome)}
          theme={boardTheme}
          pieceSet={pieceSet}
          onQueueChanged={() => void refreshDatabaseQueue()}
          onBury={onBury}
          blocked={trainingMutationBlocked}
          useSharedBoard={useSharedBoard}
        />
      )}
      {cardsLeft > 0 && !isEndgame && (
        <section
          className={`training-grid${useSharedBoard ? " training-grid-shared" : ""}${phoneOpening ? " phone-opening-study" : ""}`}
          id="train"
        >
          <div className="board-column">
            {phoneOpening && <BoardHeading>
              <div className="phone-study-heading">
                <h2>{card.title}</h2>
                <p>{playerName.toLowerCase()} to play</p>
              </div>
            </BoardHeading>}
            {!useSharedBoard && (
              <Chessboard
                key={`${card.queueEntryId ?? card.id}:${card.queueCycle ?? 0}:${card.revision ?? 1}`}
                positionRevision={boardAttempt}
                positionKey={trainingPositionKey}
                keyboard={boardHistory.keyboard}
                fen={boardHistory.fen}
                expectedSan={card.moves[step]}
                lastMove={boardHistory.viewingHistory ? undefined : lastMove}
                locked={
                  boardHistory.viewingHistory || isLocked ||
                  trainingMutationBlocked ||
                  step >= card.moves.length ||
                  cardsLeft === 0
                }
                showHint={!boardHistory.viewingHistory && showTeachingArrow}
                onHintExposure={observeDisplayedHint}
                shapes={boardHistory.viewingHistory ? [] : trainingShapes}
                theme={boardTheme}
                pieceSet={pieceSet}
                onMove={onMove}
                orientation={card.orientation}
              />
            )}
            {(feedback === "wrong" || feedback === "complete") && (
              <OutcomeFlash
                key={`${card.queueEntryId ?? card.id}:${card.queueCycle ?? 0}:${attemptFailed ? "wrong" : "correct"}`}
                outcome={attemptFailed ? "wrong" : "correct"}
              />
            )}
            <BoardTools>
              {phoneOpening ? <>
                {showMoveAction}
                <ActionMenu label="More" summaryAriaLabel="Study actions" className="phone-study-actions"
                  onClickCapture={(event) => {
                    const action = (event.target as HTMLElement).closest("button,a");
                    if (!action || action.hasAttribute("disabled")) return;
                    event.currentTarget.open = false;
                    event.currentTarget.querySelector("summary")?.focus();
                  }}>
                  <Button aria-label="Keyboard shortcuts" onClick={() => window.dispatchEvent(new Event("tempo:board-help"))}>? Keys</Button>
                  {buryAction}
                  {secondaryActions}
                </ActionMenu>
              </> : <>{buryAction}{showMoveAction}{secondaryActions}</>}
            </BoardTools>
          </div>
          <aside className="study-panel standard-study-panel">
            {!phoneOpening && <>
              <p className="side-to-play">{playerName.toLowerCase()} to play</p>
              {studyMetadata}
              <OpeningTitle card={card} />
            </>}
            <div
              className={`feedback ${feedback}`}
              role="status"
              aria-live="polite"
            >
              <FeedbackIcon feedback={feedback} />
              <FeedbackText
                title={attemptFailed && feedback === "ready" ? "Guided attempt resumed" : feedbackCopy.title}
                body={attemptFailed && feedback === "ready" ? "Follow the highlighted move to finish this line." : feedbackCopy.body}
              />
            </div>
            {phoneOpening && !offlineQueue && /^(Again|Valid repertoire move|Cannot verify)/.test(queueNotice) && <em className="training-queue-notice">{queueNotice}</em>}
            {isFailedPosition && failureAnnotation?.comment && (
              <FailureNote failureAnnotation={failureAnnotation} />
            )}
            {!phoneOpening && moveTrail}
            {usesLocalApi() && card.suggestShorterPrefix &&
              rejectedPrefixOfferKey !== prefixOfferKey &&
              card.kind === "opening" &&
              card.moves.length > 2 && (
                <div className="shorten-suggestion" aria-label="Shorten prefix suggestion">
                  <strong>This prefix may be carrying too much at once. Shorten it by one of your moves?</strong>
                  <div className="shorten-suggestion-actions">
                    <Button disabled={prefixSplitPendingAction !== null || trainingMutationBlocked} onClick={() => void decidePrefixSplit("reject")}>Reject</Button>
                    <Button variant="primary" disabled={prefixSplitPendingAction !== null || trainingMutationBlocked} onClick={() => void decidePrefixSplit("accept")}>
                      {prefixSplitPendingAction === "accept" ? "Saving…" : "Accept"}
                    </Button>
                  </div>
                  {prefixSplitPendingAction === "reject" && <span role="status">Saving choice…</span>}
                  {prefixSplitError?.key === prefixOfferKey && <span role="alert">{prefixSplitError.message}</span>}
                </div>
              )}
            <div className="ratings binary">
              <Button
                disabled={isLocked || trainingMutationBlocked}
                onClick={handleAttemptFailure}
              >
                <strong>Again</strong>
              </Button>
              <Button
                variant="primary"
                className="primary"
                disabled={attemptFailed || isLocked || trainingMutationBlocked}
                onClick={() => void rateCard("correct")}
              >
                <strong>
                  {attemptFailed ? "Finish on the board" : "Correct"}
                </strong>
              </Button>
            </div>
            {phoneOpening && <>
              <p className="phone-session-count">{serviceError && !offlineQueue ? "—" : cardsLeft} cards left</p>
              {repairNotice}
            </>}
            {phoneOpening ? <details className="study-details" open={studyDetailsExpanded}
              onToggle={(event) => setStudyDetailsExpanded(event.currentTarget.open)}>
              <summary>Study details</summary>
              <div className="study-details-content">
                {studyMetadata}
                <p className="study-source">{card.subtitle}</p>
                {moveTrail}
                {positionActions}
                <p className="study-date">Today · {dateLabel}</p>
              </div>
            </details> : positionActions}
          </aside>
        </section>
      )}
    </>
  );

  async function runBury() {
    if (burying || burialPending) return;
    setBurying(true);
    try { await onBury(); } finally { setBurying(false); }
  }

  async function decidePrefixSplit(action: "accept" | "reject") {
    if (prefixSplitPendingAction) return;
    setPrefixSplitPendingAction(action);
    setPrefixSplitError(undefined);
    try {
      if (action === "accept") {
        await onAcceptPrefixSplit(card);
        setRejectedPrefixOfferKey(prefixOfferKey);
      } else {
        await onRejectPrefixSplit(card);
        setRejectedPrefixOfferKey(prefixOfferKey);
      }
    } catch (error) {
      setPrefixSplitError({
        key: prefixOfferKey,
        message: `${action === "accept" ? "Could not split the prefix" : "Could not save your choice"}. ${error instanceof Error ? error.message : "Retry the action."}`,
      });
    } finally {
      setPrefixSplitPendingAction(null);
    }
  }
}

export default function TrainingView(props: TrainingViewProps) {
  const [buryError, setBuryError] = useState("");
  const [burying, setBurying] = useState(false);
  async function runBury() {
    if (burying) return;
    setBurying(true);
    setBuryError("");
    try { await props.onBury?.(); }
    catch (error) {
      setBuryError(`Could not bury this card. ${error instanceof Error ? error.message : "Retry the action."}`);
    } finally { setBurying(false); }
  }
  return <>
    {(buryError || props.burialRecoveryError || props.burialPending) && <div role="alert">{buryError || props.burialRecoveryError || "Burial is unresolved. Retry to check its result."} {props.burialPending && <Button disabled={burying} onClick={() => void runBury()}>Retry bury</Button>}</div>}
    <TrainingContent {...props} onBury={runBury} burialPending={props.burialPending || burying} />
  </>;
}

function TrainingContent(props: TrainingViewProps) {
  const liveQueueBlocked = Boolean(props.serviceError && !props.offlineQueue);
  const mutationBlocked = liveQueueBlocked || Boolean(props.burialPending);
  if (props.card.kind === "study" && props.card.studyId && props.card.studyExerciseId) {
    return <>{props.repairNotice}{liveQueueBlocked && <div role="alert">{props.serviceError} <RetryButton onRetry={() => props.refreshDatabaseQueue()} /></div>}<div inert={mutationBlocked}><StudyExerciseRunner
      key={`${props.card.queueEntryId ?? props.card.id}:${props.card.revision ?? 1}`}
      studyId={props.card.studyId} exerciseId={props.card.studyExerciseId}
      card={props.card} boardTheme={props.boardTheme} pieceSet={props.pieceSet}
      useSharedBoard={props.useSharedBoard ?? false} blocked={mutationBlocked}
      onAdvance={async () => { props.refreshDatabaseQueue(); }} /></div></>;
  }
  if (props.card.kind === "defense") {
    return (
      <>{props.repairNotice}{liveQueueBlocked && <div role="alert">{props.serviceError} <RetryButton onRetry={() => props.refreshDatabaseQueue()} /></div>}<div inert={mutationBlocked}><DefenseTrainingView
        key={`${props.card.queueEntryId ?? props.card.id}:${props.card.revision ?? 1}`}
        card={props.card}
        boardTheme={props.boardTheme}
        pieceSet={props.pieceSet}
        useSharedBoard={props.useSharedBoard ?? false}
        blocked={mutationBlocked}
        onAdvance={
          props.onDefenseGraded ??
          (async () => {
            props.refreshDatabaseQueue();
          })
        }
        onBury={props.onBury}
      /></div></>
    );
  }
  return <StandardTrainingView {...props} />;
}
