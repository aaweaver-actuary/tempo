import { Square, Chess, Move } from "chess.js";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { STANDARD_FEN } from "../const";
import { useState, useCallback, useEffect } from "react";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { API_URL } from "../const";
import { ImportDialogBox } from "../import_dialog_box";
import { moveSoundEnabled, playMoveSound } from "../lib/move-sound";
import { bundledRepertoires, demoCards } from "../samples";
import { Settings } from "../utils/settings";
import { TreeBrowser } from "./tree_browser";
import {
  View,
  PracticeCard,
  LocalRepertoire,
  Feedback,
  BackendQueueCard,
  PositionAnnotation,
} from "../types";
import { practiceCardFromQueue, trainedColor } from "../utils/cards";
import { usesLocalApi, localDayKey } from "../utils/local";
import { lichessAnalysisUrl } from "../utils/urls";
import BuilderView from "./analysis_view";
import CardEditor from "./card_editor";
import EndgamesView from "./endgames_view";
import { GamesView } from "./games_view";
import ProgressView from "./progress_view";
import RepertoireView from "./repertoire_view";
import SettingsView from "./settings_view";
import TacticsView from "./tactics_view";
import { dateLabel } from "../utils/dates";
import { initialTrainingState } from "../page";
import { annotationToShapes, loadPositionAnnotation } from "../utils/position-annotations";
import { migrateSqliteToBrowser } from "../lib/sqlite-migration";

export default function Home() {
  const [view, setView] = useState<View>("train");
  const [practiceCards, setPracticeCards] = useState<PracticeCard[]>([
    ...demoCards,
  ]);
  const [importedRepertoires, setImportedRepertoires] = useState<
    LocalRepertoire[]
  >([]);
  const [activeCardIndex, setActiveCardIndex] = useState(0);
  const [fen, setFen] = useState(STANDARD_FEN);
  const [step, setStep] = useState(0);
  const [feedback, setFeedback] = useState<Feedback>("ready");
  const [lastMove, setLastMove] = useState<[string, string]>();
  const [opponentLastMove, setOpponentLastMove] = useState<[string, string]>();
  const [locked, setLocked] = useState(false);
  const [showHint, setShowHint] = useState(false);
  const [cardsLeft, setCardsLeft] = useState(12);
  const [reviewed, setReviewed] = useState(0);
  const [showImport, setShowImport] = useState(false);
  const [showTree, setShowTree] = useState(false);
  const [editorCard, setEditorCard] = useState<PracticeCard | null>(null);
  const [suggestShorter, setSuggestShorter] = useState(false);
  const [seenMoves, setSeenMoves] = useState<Set<string>>(new Set());
  const [teachingEncounterKey, setTeachingEncounterKey] = useState<string | null>(null);
  const [firstCleanPasses, setFirstCleanPasses] = useState<Set<string>>(
    new Set(),
  );
  const [attemptFailed, setAttemptFailed] = useState(false);
  const [failureAnnotation, setFailureAnnotation] = useState<PositionAnnotation>();
  const [dailyQueue, setDailyQueue] = useState<number[]>(
    Array.from({ length: 12 }, (_, index) => index % demoCards.length),
  );
  const [queueNotice, setQueueNotice] = useState("");
  const [boardTheme, setBoardTheme] = useState<BoardTheme>("brown");
  const [pieceSet, setPieceSet] = useState<PieceSet>("cburnett");
  const [soundOn, setSoundOn] = useState(true);
  const [databaseQueue, setDatabaseQueue] = useState(false);
  const card = practiceCards[activeCardIndex] ?? practiceCards[0];
  const repertoireLine = card.moves;

  const refreshDatabaseQueue = useCallback(async () => {
    if (!usesLocalApi()) return;
    try {
      const response = await fetch(`${API_URL}/api/queue/today`);
      if (!response.ok) throw new Error();
      const body = (await response.json()) as { cards: BackendQueueCard[] };
      const playable = body.cards
        .filter((item) => item.content_type !== "endgame")
        .map(practiceCardFromQueue);
      setDatabaseQueue(true);
      setPracticeCards(playable.length ? playable : [...demoCards]);
      const queue = playable.map((_, index) => index);
      setDailyQueue(queue);
      setCardsLeft(queue.length);
      setActiveCardIndex(0);
      const first = playable[0];
      if (first) {
        const start = initialTrainingState(first);
        setFen(start.fen);
        setStep(start.step);
        setFeedback("ready");
        setLastMove(start.lastMove);
        setOpponentLastMove(start.lastMove);
        setLocked(false);
        setShowHint(false);
        setAttemptFailed(false);
        setFailureAnnotation(undefined);
      }
    } catch {
      setDatabaseQueue(false);
    }
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [view]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      if (
        params.has("code") ||
        ["analysis", "builder"].includes(sessionStorage.getItem("tempo-return-view") ?? "")
      ) {
        setView("builder");
        sessionStorage.removeItem("tempo-return-view");
      }
      const today = localDayKey();
      if (localStorage.getItem("tempo-day") !== today) {
        localStorage.setItem("tempo-day", today);
        localStorage.setItem("tempo-cards-left", "12");
        localStorage.setItem("tempo-reviewed", "0");
        localStorage.setItem(
          "tempo-daily-queue",
          JSON.stringify(
            Array.from({ length: 12 }, (_, index) => index % demoCards.length),
          ),
        );
      }
      setCardsLeft(Number(localStorage.getItem("tempo-cards-left") ?? 12));
      setReviewed(Number(localStorage.getItem("tempo-reviewed") ?? 0));
      setSeenMoves(
        new Set(JSON.parse(localStorage.getItem("tempo-seen-moves") ?? "[]")),
      );
      setFirstCleanPasses(
        new Set(
          JSON.parse(localStorage.getItem("tempo-first-clean-passes") ?? "[]"),
        ),
      );
      const savedRepertoires = JSON.parse(
        localStorage.getItem("tempo-imported-repertoires") ?? "[]",
      ) as LocalRepertoire[];
      const tombstones = new Set<string>(JSON.parse(
        localStorage.getItem("tempo-repertoire-tombstones") ?? "[]",
      ));
      const initialized = localStorage.getItem("tempo-repertoires-initialized") === "true";
      const seededRepertoires = initialized
        ? savedRepertoires
        : [
            ...bundledRepertoires.filter((item) => !tombstones.has(item.id)),
            ...savedRepertoires.filter((saved) => !bundledRepertoires.some((sample) => sample.id === saved.id)),
          ];
      localStorage.setItem("tempo-repertoires-initialized", "true");
      localStorage.setItem("tempo-imported-repertoires", JSON.stringify(seededRepertoires));
      setImportedRepertoires(seededRepertoires);
      const savedCards = [
        ...new Map(
          seededRepertoires
            .flatMap((repertoire) => repertoire.cards)
            .map((savedCard) => [savedCard.id, savedCard]),
        ).values(),
      ];
      const loadedCards = [...demoCards, ...savedCards];
      setPracticeCards(loadedCards);
      const storedQueue = JSON.parse(
        localStorage.getItem("tempo-daily-queue") ??
          JSON.stringify(
            Array.from({ length: 12 }, (_, index) => index % demoCards.length),
          ),
      ) as number[];
      setDailyQueue(storedQueue);
      setCardsLeft(storedQueue.length);
      setActiveCardIndex(storedQueue[0] ?? 0);
      const loadedCard = loadedCards[storedQueue[0] ?? 0];
      if (loadedCard) {
        const start = initialTrainingState(loadedCard);
        setFen(start.fen);
        setStep(start.step);
        setLastMove(start.lastMove);
        setOpponentLastMove(start.lastMove);
      }
      setBoardTheme(
        (localStorage.getItem("tempo-board-theme") as BoardTheme | null) ??
          "brown",
      );
      setPieceSet(
        (localStorage.getItem("tempo-piece-set") as PieceSet | null) ??
          "cburnett",
      );
      setSoundOn(moveSoundEnabled());
      void refreshDatabaseQueue();
      void migrateSqliteToBrowser().catch(() => undefined);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshDatabaseQueue]);

  function addImportedRepertoire(repertoire: LocalRepertoire) {
    const repertoires = [
      ...importedRepertoires.filter((item) => item.id !== repertoire.id),
      repertoire,
    ];
    setImportedRepertoires(repertoires);
    localStorage.setItem(
      "tempo-imported-repertoires",
      JSON.stringify(repertoires),
    );
    const existingIds = new Set(practiceCards.map((item) => item.id));
    const additions = repertoire.cards.filter(
      (item) => !existingIds.has(item.id),
    );
    const cards = [...practiceCards, ...additions];
    setPracticeCards(cards);
    const addedIndexes = additions.map((item) =>
      cards.findIndex((cardItem) => cardItem.id === item.id),
    );
    const queue = [...dailyQueue, ...addedIndexes];
    setDailyQueue(queue);
    setCardsLeft(queue.length);
    localStorage.setItem("tempo-daily-queue", JSON.stringify(queue));
    localStorage.setItem("tempo-cards-left", String(queue.length));
  }

  function renameLocalRepertoire(id: string, name: string) {
    const next = importedRepertoires.map((item) =>
      item.id === id ? { ...item, title: name } : item,
    );
    setImportedRepertoires(next);
    localStorage.setItem("tempo-imported-repertoires", JSON.stringify(next));
  }

  function deleteLocalRepertoire(id: string, sourceName?: string) {
    const removed = importedRepertoires.find(
      (item) =>
        item.id === id || (sourceName && item.sourceName === sourceName),
    );
    const nextRepertoires = importedRepertoires.filter(
      (item) =>
        item.id !== id && (!sourceName || item.sourceName !== sourceName),
    );
    setImportedRepertoires(nextRepertoires);
    localStorage.setItem(
      "tempo-imported-repertoires",
      JSON.stringify(nextRepertoires),
    );
    const tombstones = new Set<string>(JSON.parse(
      localStorage.getItem("tempo-repertoire-tombstones") ?? "[]",
    ));
    tombstones.add(id);
    localStorage.setItem("tempo-repertoire-tombstones", JSON.stringify([...tombstones]));
    if (!removed) return;
    const removedIds = new Set(removed.cards.map((item) => item.id));
    const queuedIds = dailyQueue
      .map((index) => practiceCards[index]?.id)
      .filter(
        (cardId): cardId is string =>
          Boolean(cardId) && !removedIds.has(cardId),
      );
    const nextCards = practiceCards.filter((item) => !removedIds.has(item.id));
    const nextQueue = queuedIds
      .map((cardId) => nextCards.findIndex((item) => item.id === cardId))
      .filter((index) => index >= 0);
    setPracticeCards(nextCards);
    setDailyQueue(nextQueue);
    setCardsLeft(nextQueue.length);
    setActiveCardIndex(nextQueue[0] ?? 0);
    localStorage.setItem("tempo-daily-queue", JSON.stringify(nextQueue));
    localStorage.setItem("tempo-cards-left", String(nextQueue.length));
    resetLine(nextCards[nextQueue[0] ?? 0] ?? demoCards[0]);
  }

  function resetLine(nextCard = card) {
    const start = initialTrainingState(nextCard);
    setFen(start.fen);
    setStep(start.step);
    setFeedback("ready");
    setLastMove(start.lastMove);
    setOpponentLastMove(start.lastMove);
    setLocked(false);
    setShowHint(false);
    setAttemptFailed(false);
    setFailureAnnotation(undefined);
  }

  function changeBoardTheme(value: BoardTheme) {
    setBoardTheme(value);
    localStorage.setItem("tempo-board-theme", value);
  }

  function changePieceSet(value: PieceSet) {
    setPieceSet(value);
    localStorage.setItem("tempo-piece-set", value);
  }

  function changeSound(value: boolean) {
    setSoundOn(value);
    localStorage.setItem("tempo-move-sound", String(value));
    if (value) playMoveSound(true);
  }

  async function rateCard(outcome: "again" | "correct") {
    if (databaseQueue && card.backendId) {
      try {
        const response = await fetch(
          `${API_URL}/api/cards/${card.backendId}/review`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              outcome,
              guided: attemptFailed,
              queue_entry_id: card.queueEntryId,
            }),
          },
        );
        if (!response.ok) throw new Error();
        setReviewed((count) => count + 1);
        setQueueNotice(
          outcome === "again"
            ? "Guided review complete · shuffled behind 4 cards"
            : "Correct · saved to your schedule",
        );
        await refreshDatabaseQueue();
        return;
      } catch {
        setQueueNotice("The local database could not save this result.");
        return;
      }
    }
    const firstClean = outcome === "correct" && !firstCleanPasses.has(card.id);
    const nextQueue = dailyQueue.slice(1);
    if (outcome === "again") {
      const key = `tempo-failures-${card.id}`;
      const failures = Number(localStorage.getItem(key) ?? 0) + 1;
      localStorage.setItem(key, String(failures));
      if (failures >= 3) setSuggestShorter(true);
      nextQueue.splice(Math.min(4, nextQueue.length), 0, activeCardIndex);
      setQueueNotice("Guided review complete · shuffled behind 4 cards");
    } else if (firstClean) {
      nextQueue.push(activeCardIndex);
      const nextPasses = new Set(firstCleanPasses).add(card.id);
      setFirstCleanPasses(nextPasses);
      localStorage.setItem(
        "tempo-first-clean-passes",
        JSON.stringify([...nextPasses]),
      );
      setQueueNotice(
        "First clean solve · one reinforcement at the end of today’s queue",
      );
    } else setQueueNotice("Correct · next review scheduled by FSRS");
    setDailyQueue(nextQueue);
    localStorage.setItem("tempo-daily-queue", JSON.stringify(nextQueue));
    setCardsLeft(nextQueue.length);
    localStorage.setItem("tempo-cards-left", String(nextQueue.length));
    setReviewed((count) => {
      const next = count + 1;
      localStorage.setItem("tempo-reviewed", String(next));
      return next;
    });
    const nextIndex = nextQueue[0] ?? 0;
    setActiveCardIndex(nextIndex);
    resetLine(practiceCards[nextIndex]);
  }

  function markMoveSeen(moveStep: number) {
    const key = `${card.backendId ?? card.id}:${card.revision ?? 1}:${moveStep}`;
    setSeenMoves((current) => {
      const next = new Set(current).add(key);
      localStorage.setItem(
        "tempo-seen-moves",
        JSON.stringify(Array.from(next)),
      );
      return next;
    });
  }

  function tryMove(from: Square, to: Square) {
    const currentTurn = new Chess(fen).turn() === "b" ? "black" : "white";
    if (
      locked ||
      step >= repertoireLine.length ||
      currentTurn !== trainedColor(card) ||
      card.kind === "endgame"
    )
      return;
    const position = new Chess(fen);
    let move: Move | null = null;
    try {
      move = position.move({ from, to, promotion: "q" });
    } catch {
      setFeedback("wrong");
      setShowHint(true);
      setAttemptFailed(true);
      setQueueNotice("Again recorded · replay the guided move");
      return;
    }
    if (!move) return;
    if (position.isCheckmate()) {
      setFeedback("complete");
      setTimeout(() => rateCard(attemptFailed ? "again" : "correct"), 650);
      return;
    }
    if (move.san !== repertoireLine[step]) {
      const alternateBranch = demoCards.some(
        (other) =>
          other.id !== card.id &&
          other.startingFen === card.startingFen &&
          other.moves
            .slice(0, step)
            .every((san, index) => san === repertoireLine[index]) &&
          other.moves[step] === move?.san,
      );
      if (alternateBranch) {
        setFeedback("branch");
        setShowHint(true);
        setQueueNotice(
          "Valid repertoire move · follow the arrow for today’s branch",
        );
        return;
      }
      setFeedback("wrong");
      setShowHint(true);
      setAttemptFailed(true);
      setQueueNotice("Again recorded · replay this move, then finish the line");
      return;
    }
    markMoveSeen(step);
    setFen(position.fen());
    setLastMove([move.from, move.to]);
    setOpponentLastMove(undefined);
    setFeedback("correct");
    setShowHint(false);
    setQueueNotice("");
    const opponentStep = step + 1;
    setStep(opponentStep);
    if (opponentStep >= repertoireLine.length) {
      setFeedback("complete");
      setTimeout(() => rateCard(attemptFailed ? "again" : "correct"), 650);
      return;
    }
    setLocked(true);
    window.setTimeout(() => {
      const replyPosition = new Chess(position.fen());
      const reply = replyPosition.move(repertoireLine[opponentStep]);
      const nextStep = opponentStep + 1;
      setFen(replyPosition.fen());
      setLastMove([reply.from, reply.to]);
      setOpponentLastMove([reply.from, reply.to]);
      setStep(nextStep);
      setLocked(false);
      setFeedback(nextStep >= repertoireLine.length ? "complete" : "ready");
      playMoveSound();
      if (nextStep >= repertoireLine.length)
        setTimeout(() => rateCard(attemptFailed ? "again" : "correct"), 650);
    }, 420);
  }

  const opponentName = trainedColor(card) === "white" ? "Black" : "White";
  const playerName = trainedColor(card) === "white" ? "White" : "Black";
  const feedbackCopy = {
    ready: {
      title: "Your move",
      body:
        card.kind === "puzzle"
          ? "Find the strongest continuation."
          : `Continue the line for ${playerName}.`,
    },
    correct: { title: "That's it", body: `${opponentName} is replying…` },
    branch: {
      title: "Also in your repertoire",
      body: "That move is valid. Replay the arrowed move for the branch being tested.",
    },
    wrong: {
      title: "Try that position again",
      body: "That move is legal, but it isn't in this repertoire.",
    },
    complete: {
      title: attemptFailed ? "Guided line complete" : "Line recalled",
      body: attemptFailed
        ? "Again will return after four other cards."
        : "Correct is being recorded automatically.",
    },
  }[feedback];

  const currentMoveKey = `${card.backendId ?? card.id}:${card.revision ?? 1}:${step}`;
  const isPlayerTurn =
    step < repertoireLine.length &&
    new Chess(fen).turn() === (trainedColor(card) === "white" ? "w" : "b");

  useEffect(() => {
    queueMicrotask(() => {
      if (!isPlayerTurn) {
        setTeachingEncounterKey(null);
        return;
      }
      if (teachingEncounterKey === currentMoveKey) return;
      if (seenMoves.has(currentMoveKey)) {
        setTeachingEncounterKey(null);
        return;
      }
      setTeachingEncounterKey(currentMoveKey);
      setSeenMoves((current) => {
        if (current.has(currentMoveKey)) return current;
        const next = new Set(current).add(currentMoveKey);
        localStorage.setItem("tempo-seen-moves", JSON.stringify([...next]));
        return next;
      });
      if (card.backendId) {
        void fetch(`${API_URL}/api/cards/${card.backendId}/teaching`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: card.revision ?? 1, ply: step }),
        }).catch(() => undefined);
      }
    });
  }, [card.backendId, card.revision, currentMoveKey, isPlayerTurn, seenMoves, step, teachingEncounterKey]);

  const showTeachingArrow =
    isPlayerTurn &&
    (showHint || feedback === "wrong" || teachingEncounterKey === currentMoveKey);
  const trainingShapes: DrawShape[] = [
    ...(opponentLastMove ? [{
        orig: opponentLastMove[0] as Key,
        dest: opponentLastMove[1] as Key,
        brush: "red",
      } as DrawShape] : []),
    ...(attemptFailed ? annotationToShapes(failureAnnotation) : []),
  ];

  useEffect(() => {
    const repertoireId = card.repertoireId;
    if (!attemptFailed || !repertoireId) return;
    let active = true;
    void loadPositionAnnotation(repertoireId, fen).then((value) => {
      if (active) setFailureAnnotation(value);
    });
    return () => { active = false; };
  }, [attemptFailed, card.repertoireId, fen]);
  const analysisUrl = lichessAnalysisUrl(
    repertoireLine.slice(0, step),
    card.startingFen,
  );
  const revealedMoves = repertoireLine.slice(
    0,
    feedback === "complete" ? repertoireLine.length : step,
  );

  const boardWorkspace = [
    "train",
    "tactics",
    "endgames",
    "builder",
    "games",
  ].includes(view);

  return (
    <main
      className={`app-shell${boardWorkspace ? " board-workspace-shell" : ""}`}
    >
      <header className="topbar">
        <button
          className="brand"
          onClick={() => setView("train")}
          aria-label="Tempo home"
        >
          <span className="brand-mark">T</span>
          <span>Tempo</span>
        </button>
        <nav className="nav" aria-label="Primary navigation">
          {(
            [
              "train",
              "tactics",
              "endgames",
              "repertoire",
              "builder",
              "games",
              "progress",
              "settings",
            ] as View[]
          ).map((item) => (
            <button
              className={view === item ? "active" : ""}
              key={item}
              onClick={() => setView(item)}
            >
              {item[0].toUpperCase() + item.slice(1)}
            </button>
          ))}
        </nav>
        <div className="top-actions">
          <button
            className="sound-toggle"
            aria-pressed={soundOn}
            aria-label={`${soundOn ? "Turn off" : "Turn on"} board sounds`}
            onClick={() => changeSound(!soundOn)}
          >
            <span aria-hidden="true">{soundOn ? "🔊" : "🔇"}</span>
            <span>Sound</span>
          </button>
          <button className="local-status" onClick={() => setShowImport(true)}>
            <span className="status-dot" /> Saved locally
          </button>
        </div>
      </header>

      {view === "train" && (
        <>
          <section className="training-header">
            <div>
              <p className="eyebrow">Today · {dateLabel}</p>
              <h1>
                {cardsLeft === 0 ? "You’re done for today" : "Daily training"}
              </h1>
            </div>
            <div className="session-count">
              <strong>{cardsLeft}</strong>
              <span>cards left</span>
            </div>
          </section>
          <section className="training-grid" id="train">
            <div className="board-column">
              <Chessboard
                fen={fen}
                expectedSan={repertoireLine[step]}
                lastMove={lastMove}
                locked={
                  locked || step >= repertoireLine.length || cardsLeft === 0
                }
                showHint={showTeachingArrow}
                shapes={trainingShapes}
                theme={boardTheme}
                pieceSet={pieceSet}
                onMove={tryMove}
                orientation={card.orientation}
              />
              <div className="board-tools">
                <button
                  onClick={() => {
                    if (!attemptFailed) {
                      setAttemptFailed(true);
                      setQueueNotice("Again recorded · finish with guidance");
                    }
                    setShowHint((value) => !value);
                  }}
                  disabled={feedback === "complete" || cardsLeft === 0}
                >
                  ⌁ <span>{showHint ? "Hide move" : "Show move"}</span>
                </button>
                <button
                  onClick={() => {
                    resetLine();
                    setAttemptFailed(true);
                    setShowHint(true);
                    setQueueNotice("Again recorded · restarted in guided mode");
                  }}
                >
                  ↻ <span>Restart</span>
                </button>
                <a
                  href={analysisUrl}
                  onClick={() => {
                    if (!attemptFailed) rateCard("again");
                  }}
                  target="_blank"
                  rel="noreferrer"
                >
                  ↗ <span>Analyze</span>
                </a>
                <button onClick={() => setEditorCard(card)}>
                  ✎ <span>Edit card</span>
                </button>
              </div>
            </div>
            <aside className="study-panel">
              <div className="card-meta">
                <span
                  className={`pill${card.kind === "puzzle" ? " puzzle" : ""}`}
                >
                  {card.kind === "puzzle" ? "Puzzle" : "Review"}
                </span>
                {queueNotice && <em>{queueNotice}</em>}
              </div>
              <div className="opening-title">
                <h2>{card.title}</h2>
                <span>{card.subtitle}</span>
              </div>
              <div
                className={`feedback ${feedback}`}
                role="status"
                aria-live="polite"
              >
                <span className="feedback-icon">
                  {feedback === "wrong"
                    ? "×"
                    : feedback === "complete"
                      ? "✓"
                      : "●"}
                </span>
                <div>
                  <strong>{feedbackCopy.title}</strong>
                  <p>{feedbackCopy.body}</p>
                </div>
              </div>
              {attemptFailed && failureAnnotation?.comment && (
                <div className="failure-note" role="note">
                  <strong>Note for this position</strong>
                  <p>{failureAnnotation.comment}</p>
                </div>
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
                            : "…"}
                        </b>
                        {move}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p>Nothing is revealed until you play it.</p>
                )}
              </div>
              {suggestShorter && card.kind === "opening" && (
                <div className="shorten-suggestion">
                  <strong>This prefix may be carrying too much at once.</strong>
                  <button
                    onClick={() =>
                      setEditorCard({ ...card, moves: card.moves.slice(0, -2) })
                    }
                  >
                    Preview one move shorter
                  </button>
                </div>
              )}
              <div className="ratings binary">
                <button
                  onClick={() => {
                    setAttemptFailed(true);
                    setShowHint(true);
                    setQueueNotice(
                      "Again recorded · finish the line with guidance",
                    );
                  }}
                >
                  <strong>Again</strong>
                </button>
                <button
                  className="primary"
                  disabled={attemptFailed}
                  onClick={() => rateCard("correct")}
                >
                  <strong>
                    {attemptFailed ? "Finish on the board" : "Correct"}
                  </strong>
                </button>
              </div>
            </aside>
          </section>
        </>
      )}
      {view === "tactics" && (
        <TacticsView
          theme={boardTheme}
          pieceSet={pieceSet}
          onQueueChanged={() => void refreshDatabaseQueue()}
        />
      )}
      {view === "endgames" && (
        <EndgamesView
          theme={boardTheme}
          pieceSet={pieceSet}
          onQueueChanged={() => void refreshDatabaseQueue()}
        />
      )}
      {view === "repertoire" && (
        <RepertoireView
          imported={importedRepertoires}
          onImport={() => setShowImport(true)}
          onBrowse={() => setShowTree(true)}
          onDeleteLocal={deleteLocalRepertoire}
          onRenameLocal={renameLocalRepertoire}
          onQueueChanged={refreshDatabaseQueue}
        />
      )}
      {view === "builder" && (
        <BuilderView
          theme={boardTheme}
          pieceSet={pieceSet}
          imported={importedRepertoires}
          settings={new Settings()}
        />
      )}
      {view === "games" && (
        <GamesView
          onAnalyze={() => setView("builder")}
          theme={boardTheme}
          pieceSet={pieceSet}
        />
      )}
      {view === "progress" && (
        <ProgressView
          reviewed={reviewed}
          cardsLeft={cardsLeft}
          totalCards={practiceCards.length}
        />
      )}
      {view === "settings" && (
        <SettingsView
          theme={boardTheme}
          pieceSet={pieceSet}
          sound={soundOn}
          onTheme={changeBoardTheme}
          onPieces={changePieceSet}
          onSound={changeSound}
        />
      )}
      {showImport && (
        <ImportDialogBox
          onClose={() => setShowImport(false)}
          onImported={addImportedRepertoire}
          onDatabaseUpdated={refreshDatabaseQueue}
          onViewRepertoire={() => setView("repertoire")}
        />
      )}
      {showTree && (
        <TreeBrowser
          onClose={() => setShowTree(false)}
          theme={boardTheme}
          pieceSet={pieceSet}
        />
      )}
      {editorCard && (
        <CardEditor
          card={editorCard}
          theme={boardTheme}
          pieceSet={pieceSet}
          onClose={() => setEditorCard(null)}
          onSave={(updated) => {
            setPracticeCards((current) =>
              current.map((item) => (item.id === updated.id ? updated : item)),
            );
            resetLine(updated);
            setSuggestShorter(false);
          }}
        />
      )}
      {!boardWorkspace && (
        <footer className="source-footer">
          Board interaction by{" "}
          <a
            href="https://github.com/lichess-org/chessground"
            target="_blank"
            rel="noreferrer"
          >
            Chessground
          </a>{" "}
          · Woodland sounds and chess assets from{" "}
          <a
            href="https://github.com/lichess-org/lila"
            target="_blank"
            rel="noreferrer"
          >
            Lichess
          </a>{" "}
          under AGPL-3.0+ · Puzzle positions from the public-domain{" "}
          <a
            href="https://database.lichess.org/#puzzles"
            target="_blank"
            rel="noreferrer"
          >
            Lichess database
          </a>
        </footer>
      )}
    </main>
  );
}
