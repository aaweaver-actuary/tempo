import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type {
  PracticeCard,
  Feedback,
  PositionAnnotation,
  LocalRepertoire,
  FenString,
  MoveSquares,
} from "../types";
import { demoCards } from "../samples";
import { BoardTheme, PieceSet } from "../components/chessboard";
import { initialTrainingState, attemptEntryKey, isAttemptPlayable, isCurrentAttempt, type AttemptPhase, type AttemptToken } from "../domain/attempt";
import { asFenString } from "../domain/shared";
import { STANDARD_FEN } from "../const";

export type TrainingStoreState = {
  practiceCards: PracticeCard[];
  importedRepertoires: LocalRepertoire[];
  activeCardIndex: number;
  currentFenString: FenString;
  step: number;
  feedback: Feedback;
  lastMove: MoveSquares | undefined;
  opponentLastMove: MoveSquares | undefined;
  attempt: AttemptToken;
  reviewSaveError: string;
  boardAttempt: number;
  showHint: boolean;
  cardsLeft: number;
  reviewed: number;
  showImport: boolean;
  showTree: boolean;
  editorCard: PracticeCard | null;
  suggestShorter: boolean;
  seenMoves: Set<string>;
  teachingEncounterKey: string | null;
  teachingReadyCard: string;
  firstCleanPasses: Set<string>;
  isAttemptFailed: boolean;
  failureAnnotation: PositionAnnotation | undefined;
  failureFen: FenString | undefined;
  dailyQueue: number[];
  queueNotice: string;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  isSoundEnabled: boolean;
  isDatabaseQueueActive: boolean;
  serviceError: string;
  setPracticeCards: (
    cards: PracticeCard[] | ((current: PracticeCard[]) => PracticeCard[]),
  ) => void;
  setImportedRepertoires: (
    repertoires:
      | LocalRepertoire[]
      | ((current: LocalRepertoire[]) => LocalRepertoire[]),
  ) => void;
  setActiveCardIndex: (index: number | ((current: number) => number)) => void;
  setCurrentFenString: (
    fen: FenString | ((current: FenString) => FenString),
  ) => void;
  setStep: (step: number | ((current: number) => number)) => void;
  setFeedback: (feedback: Feedback | ((current: Feedback) => Feedback)) => void;
  setLastMove: (
    move: MoveSquares | undefined | ((current: MoveSquares | undefined) => MoveSquares | undefined),
  ) => void;
  setOpponentLastMove: (
    move: MoveSquares | undefined | ((current: MoveSquares | undefined) => MoveSquares | undefined),
  ) => void;
  setAttemptPhase: (phase: AttemptPhase, expected?: AttemptToken) => void;
  setReviewSaveError: (error: string) => void;
  hydrateLocalQueue: (cards: PracticeCard[], advance?: boolean) => void;
  setBoardAttempt: (value: number | ((value: number) => number)) => void;
  setShowHint: (value: boolean | ((value: boolean) => boolean)) => void;
  setCardsLeft: (cardsLeft: number | ((current: number) => number)) => void;
  setReviewed: (reviewed: number | ((value: number) => number)) => void;
  setShowImport: (show: boolean | ((current: boolean) => boolean)) => void;
  setShowTree: (show: boolean | ((current: boolean) => boolean)) => void;
  setEditorCard: (
    card:
      | PracticeCard
      | null
      | ((current: PracticeCard | null) => PracticeCard | null),
  ) => void;
  setSuggestShorter: (value: boolean | ((current: boolean) => boolean)) => void;
  setSeenMoves: (
    moves: Set<string> | ((value: Set<string>) => Set<string>),
  ) => void;
  setTeachingEncounterKey: (
    key: string | null | ((current: string | null) => string | null),
  ) => void;
  setTeachingReadyCard: (key: string | ((current: string) => string)) => void;
  setFirstCleanPasses: (
    passes: Set<string> | ((current: Set<string>) => Set<string>),
  ) => void;
  setAttemptFailed: (failed: boolean | ((current: boolean) => boolean)) => void;
  setFailureAnnotation: (
    annotation:
      | PositionAnnotation
      | undefined
      | ((
          current: PositionAnnotation | undefined,
        ) => PositionAnnotation | undefined),
  ) => void;
  setFailureFen: (fen: FenString | undefined | ((current: FenString | undefined) => FenString | undefined)) => void;
  setDailyQueue: (queue: number[] | ((current: number[]) => number[])) => void;
  setQueueNotice: (notice: string | ((current: string) => string)) => void;
  setBoardTheme: (
    theme: BoardTheme | ((current: BoardTheme) => BoardTheme),
  ) => void;
  setPieceSet: (pieceSet: PieceSet | ((current: PieceSet) => PieceSet)) => void;
  setSoundOn: (value: boolean | ((current: boolean) => boolean)) => void;
  setDatabaseQueue: (value: boolean | ((current: boolean) => boolean)) => void;
  setServiceError: (error: string | ((current: string) => string)) => void;
  resetTrainingLine: (nextCard?: PracticeCard) => void;
  hydrateQueue: (payload: {
    practiceCards: PracticeCard[];
    dailyQueue?: number[];
    activeCardIndex?: number;
    cardsLeft?: number;
    reviewed?: number;
  }) => void;
  initializeCardState: (
    card: PracticeCard,
    overrides?: Partial<{
      feedback: Feedback;
      attemptFailed: boolean;
      showHint: boolean;
      failureFen: FenString;
    }>,
  ) => void;
  getCard: () => PracticeCard;
};

const defaultState = {
  practiceCards: [...demoCards],
  importedRepertoires: [],
  activeCardIndex: 0,
  currentFenString: asFenString(STANDARD_FEN),
  step: 0,
  feedback: "ready" as const,
  lastMove: undefined,
  opponentLastMove: undefined,
  attempt: { entryKey: "", generation: 0, phase: "complete" as AttemptPhase },
  reviewSaveError: "",
  boardAttempt: 0,
  showHint: false,
  cardsLeft: 0,
  reviewed: 0,
  showImport: false,
  showTree: false,
  editorCard: null,
  suggestShorter: false,
  seenMoves: new Set<string>(),
  teachingEncounterKey: null,
  teachingReadyCard: "",
  firstCleanPasses: new Set<string>(),
  isAttemptFailed: false,
  failureAnnotation: undefined,
  failureFen: undefined,
  dailyQueue: Array.from(
    { length: 12 },
    (_, index) => index % demoCards.length,
  ),
  queueNotice: "",
  boardTheme: "brown" as BoardTheme,
  pieceSet: "cburnett" as PieceSet,
  isSoundEnabled: true,
  isDatabaseQueueActive: false,
  serviceError: "",
};

export const selectTrainingViewState = (state: TrainingStoreState) => ({
  boardAttempt: state.boardAttempt,
  attemptFailed: state.isAttemptFailed,
  lastMove: state.lastMove,
  opponentLastMove: state.opponentLastMove,
  isLocked: !isAttemptPlayable(state.attempt),
  attempt: state.attempt,
  reviewSaveError: state.reviewSaveError,
  step: state.step,
  feedback: state.feedback,
  showHint: state.showHint,
  queueNotice: state.queueNotice,
  failureAnnotation: state.failureAnnotation,
  failureFen: state.failureFen,
  suggestShorter: state.suggestShorter,
  currentFenString: state.currentFenString,
  teachingEncounterKey: state.teachingEncounterKey,
});

export function selectHomeViewState(state: TrainingStoreState) {
  return {
    practiceCards: state.practiceCards,
    importedRepertoires: state.importedRepertoires,
    activeCardIndex: state.activeCardIndex,
    currentFenString: state.currentFenString,
    currentStepIndex: state.step,
    evaluationFeedback: state.feedback,
    lastMove: state.lastMove,
    opponentLastMove: state.opponentLastMove,
    isViewLocked: !isAttemptPlayable(state.attempt),
    attempt: state.attempt,
    totalBoardAttempts: state.boardAttempt,
    showHint: state.showHint,
    cardsLeft: state.cardsLeft,
    reviewed: state.reviewed,
    showImport: state.showImport,
    showTree: state.showTree,
    editorCard: state.editorCard,
    suggestShorter: state.suggestShorter,
    seenMoves: state.seenMoves,
    teachingEncounterKey: state.teachingEncounterKey,
    teachingReadyCard: state.teachingReadyCard,
    firstCleanPasses: state.firstCleanPasses,
    attemptFailed: state.isAttemptFailed,
    failureAnnotation: state.failureAnnotation,
    failureFen: state.failureFen,
    dailyQueue: state.dailyQueue,
    queueNotice: state.queueNotice,
    boardTheme: state.boardTheme,
    pieceSet: state.pieceSet,
    soundOn: state.isSoundEnabled,
    databaseQueue: state.isDatabaseQueueActive,
    serviceError: state.serviceError,
  };
}

export const selectTrainingActions = (state: TrainingStoreState) => ({
  setPracticeCards: state.setPracticeCards,
  setImportedRepertoires: state.setImportedRepertoires,
  setActiveCardIndex: state.setActiveCardIndex,
  setCurrentFenString: state.setCurrentFenString,
  setStep: state.setStep,
  setFeedback: state.setFeedback,
  setLastMove: state.setLastMove,
  setOpponentLastMove: state.setOpponentLastMove,
  setAttemptPhase: state.setAttemptPhase,
  setReviewSaveError: state.setReviewSaveError,
  hydrateLocalQueue: state.hydrateLocalQueue,
  setBoardAttempt: state.setBoardAttempt,
  setShowHint: state.setShowHint,
  setCardsLeft: state.setCardsLeft,
  setReviewed: state.setReviewed,
  setShowImport: state.setShowImport,
  setShowTree: state.setShowTree,
  setEditorCard: state.setEditorCard,
  setSuggestShorter: state.setSuggestShorter,
  setSeenMoves: state.setSeenMoves,
  setTeachingEncounterKey: state.setTeachingEncounterKey,
  setTeachingReadyCard: state.setTeachingReadyCard,
  setFirstCleanPasses: state.setFirstCleanPasses,
  setAttemptFailed: state.setAttemptFailed,
  setFailureAnnotation: state.setFailureAnnotation,
  setFailureFen: state.setFailureFen,
  setDailyQueue: state.setDailyQueue,
  setQueueNotice: state.setQueueNotice,
  setBoardTheme: state.setBoardTheme,
  setPieceSet: state.setPieceSet,
  setSoundOn: state.setSoundOn,
  setDatabaseQueue: state.setDatabaseQueue,
  setServiceError: state.setServiceError,
  initializeCardState: state.initializeCardState,
  hydrateQueue: state.hydrateQueue,
  resetTrainingLine: state.resetTrainingLine,
});

export const useTrainingStore = create<TrainingStoreState>((set, get) => ({
  ...defaultState,
  setPracticeCards: (value) =>
    set((state) => ({
      practiceCards:
        typeof value === "function" ? value(state.practiceCards) : value,
    })),
  setImportedRepertoires: (value) =>
    set((state) => ({
      importedRepertoires:
        typeof value === "function" ? value(state.importedRepertoires) : value,
    })),
  setActiveCardIndex: (value) =>
    set((state) => ({
      activeCardIndex:
        typeof value === "function" ? value(state.activeCardIndex) : value,
    })),
  setCurrentFenString: (value) =>
    set((state) => ({
      currentFenString:
        typeof value === "function" ? value(state.currentFenString) : value,
    })),
  setStep: (value) =>
    set((state) => ({
      step: typeof value === "function" ? value(state.step) : value,
    })),
  setFeedback: (value) =>
    set((state) => ({
      feedback: typeof value === "function" ? value(state.feedback) : value,
    })),
  setLastMove: (value) =>
    set((state) => ({
      lastMove: typeof value === "function" ? value(state.lastMove) : value,
    })),
  setOpponentLastMove: (value) =>
    set((state) => ({
      opponentLastMove:
        typeof value === "function" ? value(state.opponentLastMove) : value,
    })),
  setAttemptPhase: (phase, expected) => set(state =>
    expected && !isCurrentAttempt(state.attempt, expected) ? {} :
      { attempt: { ...state.attempt, phase } }),
  setReviewSaveError: reviewSaveError => set({ reviewSaveError }),
  setBoardAttempt: (value) =>
    set((state) => ({
      boardAttempt:
        typeof value === "function" ? value(state.boardAttempt) : value,
    })),
  setShowHint: (value) =>
    set((state) => ({
      showHint: typeof value === "function" ? value(state.showHint) : value,
    })),
  setCardsLeft: (value) =>
    set((state) => ({
      cardsLeft: typeof value === "function" ? value(state.cardsLeft) : value,
    })),
  setReviewed: (value) =>
    set((state) => ({
      reviewed: typeof value === "function" ? value(state.reviewed) : value,
    })),
  setShowImport: (value) =>
    set((state) => ({
      showImport: typeof value === "function" ? value(state.showImport) : value,
    })),
  setShowTree: (value) =>
    set((state): { showTree: boolean } => ({
      showTree: typeof value === "function" ? value(state.showTree) : value,
    })),
  setEditorCard: (value) =>
    set((state) => ({
      editorCard: typeof value === "function" ? value(state.editorCard) : value,
    })),
  setSuggestShorter: (value) =>
    set((state) => ({
      suggestShorter:
        typeof value === "function" ? value(state.suggestShorter) : value,
    })),
  setSeenMoves: (value) =>
    set((state) => ({
      seenMoves: typeof value === "function" ? value(state.seenMoves) : value,
    })),
  setTeachingEncounterKey: (value) =>
    set((state) => ({
      teachingEncounterKey:
        typeof value === "function" ? value(state.teachingEncounterKey) : value,
    })),
  setTeachingReadyCard: (value) =>
    set((state) => ({
      teachingReadyCard:
        typeof value === "function" ? value(state.teachingReadyCard) : value,
    })),
  setFirstCleanPasses: (value) =>
    set((state) => ({
      firstCleanPasses:
        typeof value === "function" ? value(state.firstCleanPasses) : value,
    })),
  setAttemptFailed: (value) =>
    set(state => {
      const failed = typeof value === "function" ? value(state.isAttemptFailed) : value;
      return { isAttemptFailed: failed, attempt: isAttemptPlayable(state.attempt)
        ? { ...state.attempt, phase: failed ? "guided" : "playerTurn" } : state.attempt };
    }),
  setFailureAnnotation: (value) =>
    set((state) => ({
      failureAnnotation:
        typeof value === "function" ? value(state.failureAnnotation) : value,
    })),
  setFailureFen: (value) =>
    set((state) => ({
      failureFen: typeof value === "function" ? value(state.failureFen) : value,
    })),
  setDailyQueue: (value) =>
    set((state) => ({
      dailyQueue: typeof value === "function" ? value(state.dailyQueue) : value,
    })),
  setQueueNotice: (value) =>
    set((state) => ({
      queueNotice:
        typeof value === "function" ? value(state.queueNotice) : value,
    })),
  setBoardTheme: (value) =>
    set((state) => ({
      boardTheme: typeof value === "function" ? value(state.boardTheme) : value,
    })),
  setPieceSet: (value) =>
    set((state) => ({
      pieceSet: typeof value === "function" ? value(state.pieceSet) : value,
    })),
  setSoundOn: (value) =>
    set((state) => ({
      isSoundEnabled:
        typeof value === "function" ? value(state.isSoundEnabled) : value,
    })),
  setDatabaseQueue: (value) =>
    set((state) => ({
      isDatabaseQueueActive:
        typeof value === "function"
          ? value(state.isDatabaseQueueActive)
          : value,
    })),
  setServiceError: (value) =>
    set((state) => ({
      serviceError:
        typeof value === "function" ? value(state.serviceError) : value,
    })),
  resetTrainingLine: (
    nextCard = get().practiceCards[get().activeCardIndex] ?? demoCards[0],
  ) => {
    const start = initialTrainingState(nextCard);
    set({
      currentFenString: start.fen,
      step: start.step,
      feedback: "ready",
      lastMove: start.lastMove,
      opponentLastMove: start.lastMove,
      attempt: { entryKey: attemptEntryKey(nextCard), generation: get().attempt.generation + 1, phase: "playerTurn" },
      reviewSaveError: "",
      showHint: false,
      teachingEncounterKey: null,
      isAttemptFailed: false,
      failureAnnotation: undefined,
      failureFen: undefined,
    });
  },
  hydrateQueue: ({
    practiceCards,
    dailyQueue,
    activeCardIndex,
    cardsLeft,
    reviewed,
  }) => {
    const nextQueue = dailyQueue ?? get().dailyQueue;
    const nextIndex = activeCardIndex ?? get().activeCardIndex;
    const nextCardsLeft = cardsLeft ?? nextQueue.length;
    const nextReviewed = reviewed ?? get().reviewed;
    set({
      practiceCards,
      dailyQueue: nextQueue,
      activeCardIndex: nextIndex,
      cardsLeft: nextCardsLeft,
      reviewed: nextReviewed,
    });
  },
  initializeCardState: (card, overrides = {}) => {
    const start = initialTrainingState(card);
    const nextFeedback =
      overrides.feedback ?? (card.attemptFailed ? "wrong" : "ready");
    const nextAttemptFailed =
      overrides.attemptFailed ?? Boolean(card.attemptFailed);
    const nextShowHint = overrides.showHint ?? nextAttemptFailed;
    set({
      currentFenString: start.fen,
      step: start.step,
      feedback: nextFeedback,
      lastMove: start.lastMove,
      opponentLastMove: start.lastMove,
      attempt: { entryKey: attemptEntryKey(card), generation: get().attempt.generation + 1, phase: nextAttemptFailed ? "guided" : "playerTurn" },
      reviewSaveError: "",
      showHint: nextShowHint,
      teachingEncounterKey: null,
      isAttemptFailed: nextAttemptFailed,
      failureAnnotation: undefined,
      failureFen: nextAttemptFailed ? (overrides.failureFen ?? start.fen) : undefined,
    });
  },
  hydrateLocalQueue: (practiceCards, advance = false) => {
    const current = get();
    const retainedIndex = advance ? -1 : practiceCards.findIndex(card => attemptEntryKey(card) === current.attempt.entryKey);
    const activeCardIndex = Math.max(0, retainedIndex);
    const card = practiceCards[activeCardIndex];
    const queueState = { practiceCards, dailyQueue: practiceCards.map((_, index) => index), cardsLeft: practiceCards.length, activeCardIndex, isDatabaseQueueActive: true, serviceError: "" };
    if (retainedIndex >= 0) { set(queueState); return; }
    const start = card ? initialTrainingState(card) : { fen: asFenString(STANDARD_FEN), step: 0, lastMove: undefined };
    const failed = Boolean(card?.attemptFailed);
    set({ ...queueState, currentFenString: start.fen, step: start.step, lastMove: start.lastMove,
      opponentLastMove: start.lastMove, feedback: failed ? "wrong" : "ready", showHint: failed,
      teachingEncounterKey: null, isAttemptFailed: failed, failureAnnotation: undefined,
      failureFen: failed ? start.fen : undefined, reviewSaveError: "",
      attempt: { entryKey: card ? attemptEntryKey(card) : "", generation: current.attempt.generation + 1, phase: card ? failed ? "guided" : "playerTurn" : "complete" } });
  },
  getCard: () => get().practiceCards[get().activeCardIndex] ?? demoCards[0],
}));

export function useTrainingStoreState<T>(
  selector: (state: TrainingStoreState) => T,
) {
  return useTrainingStore(useShallow(selector));
}
