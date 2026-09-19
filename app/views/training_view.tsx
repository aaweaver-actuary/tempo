import { BoardTools } from "../components/board-workspace";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import TrainingViewHeader from "./headers/TrainingViewHeader";
import RetryButton from "../components/buttons/RetryButton";
import EndgamesView from "./endgames_view";
import { PracticeCard } from "../types";
import { BoardTheme, Chessboard, PieceSet } from "../components/chessboard";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import { OutcomeFlash } from "../components/board-controls";
import AgainButton from "../components/buttons/AgainButton";
import AnalyzeOnLichessButton from "../components/buttons/AnalyzeOnLichessButton";
import EditCardButton from "../components/buttons/EditCardButton";
import RestartButton from "../components/buttons/RestartButton";
import FailureNote from "../components/FailureNote";
import FeedbackIcon from "../components/feedback/FeedbackIcon";
import FeedbackText from "../components/feedback/FeedbackText";
import OpeningTitle from "../components/OpeningTitle";
import { useEffect, useMemo } from "react";
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

interface TrainingViewProps {
  dateLabel: string;
  serviceError: string;
  refreshDatabaseQueue: () => void;
  cardsLeft: number;
  card: PracticeCard;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  rateCard: (outcome: "again" | "correct") => Promise<void>;
  handleAttemptFailure: () => void;
  resetCardAttempt: () => void;
  setEditorCard: (card: PracticeCard | null) => void;
  onMove: (from: Square, to: Square) => void;
  onOpenPosition?: (target: "analysis" | "builder" | "games") => void;
  useSharedBoard?: boolean;
}

export default function TrainingView({
  dateLabel,
  serviceError,
  cardsLeft,
  refreshDatabaseQueue,
  card,
  boardTheme,
  pieceSet,
  rateCard,
  handleAttemptFailure,
  resetCardAttempt,
  setEditorCard,
  onMove,
  onOpenPosition = () => undefined,
  useSharedBoard = false,
}: TrainingViewProps) {
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
    suggestShorter,
    currentFenString,
    teachingEncounterKey,
  } = useTrainingStore(useShallow(selectTrainingViewState));
  const isEndgame = card.kind === "endgame";
  const { setShellBoardForOwner, releaseShellBoardForOwner } = useBoardPublisher();
  const feedbackCopy = getFeedbackCopy(attemptFailed, card)[feedback];
  const playerName = trainedColor(card) === "white" ? "White" : "Black";
  const currentMoveKey = `${card.backendId ?? card.id}:${card.revision ?? 1}:${step}`;
  const showTeachingArrow =
    showHint ||
    (card.kind === "opening" && teachingEncounterKey === currentMoveKey);
  const isFailedPosition = attemptFailed && currentFenString === failureFen;
  const trainingShapes = useMemo<DrawShape[]>(() => [
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
  ], [opponentLastMove, isFailedPosition, failureAnnotation]);
  const revealedMoves = card.moves.slice(
    0,
    feedback === "complete" ? card.moves.length : step,
  );

  useEffect(() => {
    if (!useSharedBoard || isEndgame) return;
    setShellBoardForOwner("train", {
      unavailable: cardsLeft <= 0 ? (serviceError ? "Training position unavailable. Retry the local service." : "No cards due. Your next session will appear here.") : undefined,
      fen: currentFenString,
      expectedSan: card.moves[step],
      lastMove,
      interactionMode:
        isLocked || step >= card.moves.length || cardsLeft === 0
          ? "readonly"
          : "legal",
      showHint: cardsLeft > 0 && showTeachingArrow,
      theme: boardTheme,
      pieceSet,
      orientation: card.orientation === "black" ? "black" : "white",
      shapes: trainingShapes,
      drawnShapes: [],
      positionRevision: boardAttempt,
      onMove,
      onSquareSelect: undefined,
      onFreeMove: undefined,
      onDrawnShapesChange: undefined,
      onFlip: undefined,
    });
    return () => releaseShellBoardForOwner("train");
  }, [
    serviceError,
    boardAttempt,
    boardTheme,
    card.moves,
    card.orientation,
    cardsLeft,
    currentFenString,
    isEndgame,
    isLocked,
    lastMove,
    onMove,
    pieceSet,
    releaseShellBoardForOwner,
    setShellBoardForOwner,
    showTeachingArrow,
    step,
    trainingShapes,
    useSharedBoard,
  ]);

  function handleAnalyzeOnLichessClick() {
    if (!attemptFailed) void rateCard("again");
  }

  return (
    <>
      <TrainingViewHeader
        dateLabel={dateLabel}
        serviceError={serviceError}
        cardsLeft={cardsLeft}
      />
      {serviceError && (
        <div role="alert">
          {serviceError}{" "}
          <RetryButton onRetry={() => void refreshDatabaseQueue()} />
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
          useSharedBoard={useSharedBoard}
        />
      )}
      {cardsLeft > 0 && !isEndgame && (
        <section
          className={`training-grid${useSharedBoard ? " training-grid-shared" : ""}`}
          id="train"
        >
          <div className="board-column">
            {!useSharedBoard && (
              <Chessboard
                key={`${card.queueEntryId ?? card.id}:${card.queueCycle ?? 0}:${card.revision ?? 1}`}
                positionRevision={boardAttempt}
                fen={currentFenString}
                expectedSan={card.moves[step]}
                lastMove={lastMove}
                locked={
                  isLocked || step >= card.moves.length || cardsLeft === 0
                }
                showHint={showTeachingArrow}
                shapes={trainingShapes}
                theme={boardTheme}
                pieceSet={pieceSet}
                onMove={onMove}
                orientation={card.orientation}
              />
            )}
            {(attemptFailed || feedback === "complete") && (
              <OutcomeFlash
                key={`${card.queueEntryId ?? card.id}:${card.queueCycle ?? 0}:${attemptFailed ? "wrong" : "correct"}`}
                outcome={attemptFailed ? "wrong" : "correct"}
              />
            )}
            <BoardTools>
              <AgainButton
                handleAgain={handleAttemptFailure}
                isAttemptFailed={attemptFailed}
                isFeedbackComplete={feedback === "complete"}
                hasNoCardsLeft={cardsLeft === 0}
              />
              <RestartButton handleRestart={resetCardAttempt} />
              <AnalyzeOnLichessButton
                moves={card.moves.slice(0, step)}
                fen={card.startingFen}
                onClick={handleAnalyzeOnLichessClick}
              />
              <EditCardButton
                card={card}
                setEditorCard={(value) => setEditorCard(value)}
              />
            </BoardTools>
          </div>
          <aside className="study-panel">
            <p className="side-to-play">{playerName.toLowerCase()} to play</p>
            <div className="card-meta">
              <span
                className={`pill${card.kind === "puzzle" ? " puzzle" : ""}`}
              >
                {card.kind === "puzzle" ? "Puzzle" : "Review"}
              </span>
              {card.queueAttemptState === "reinforcement" && (
                <span className="pill">Reinforcement</span>
              )}
              {queueNotice && <em>{queueNotice}</em>}
            </div>
            <OpeningTitle card={card} />
            <div
              className={`feedback ${feedback}`}
              role="status"
              aria-live="polite"
            >
              <FeedbackIcon feedback={feedback} />
              <FeedbackText
                title={feedbackCopy.title}
                body={feedbackCopy.body}
              />
            </div>
            {isFailedPosition && failureAnnotation?.comment && (
              <FailureNote failureAnnotation={failureAnnotation} />
            )}
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
            {(usesLocalApi() ? card.suggestShorterPrefix : suggestShorter) &&
              card.kind === "opening" &&
              card.moves.length > 2 && (
                <div className="shorten-suggestion">
                  <strong>This prefix may be carrying too much at once.</strong>
                  <button
                    onClick={() =>
                      setEditorCard({
                        ...card,
                        moves: card.moves.slice(0, -2),
                      })
                    }
                  >
                    Preview one move shorter
                  </button>
                </div>
              )}
            <div className="ratings binary">
              <button disabled={isLocked} onClick={handleAttemptFailure}>
                <strong>Again</strong>
              </button>
              <button
                className="primary"
                disabled={attemptFailed || isLocked}
                onClick={() => void rateCard("correct")}
              >
                <strong>
                  {attemptFailed ? "Finish on the board" : "Correct"}
                </strong>
              </button>
            </div>
            <div className="position-actions" aria-label="Open review position">
              <button onClick={() => onOpenPosition("analysis")}>Analysis</button>
              <button onClick={() => onOpenPosition("builder")}>Builder</button>
              <button onClick={() => onOpenPosition("games")}>Games here</button>
            </div>
          </aside>
        </section>
      )}
    </>
  );
}
