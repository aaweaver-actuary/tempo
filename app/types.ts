import type { BoardTheme, PieceSet } from "./components/chessboard";

export type PieceColor = "white" | "black";
export type GamePhase = "opening" | "middlegame" | "endgame";

export type Feedback = "ready" | "correct" | "branch" | "wrong" | "complete";

type CardId = string;
type DeckId = string;

export type View =
  | "train"
  | "tactics"
  | "endgames"
  | "repertoire"
  | "builder"
  | "games"
  | "progress"
  | "settings";

export type ExplorerMove = {
  uci: string;
  san: string;
  white: number;
  draws: number;
  black: number;
};

export type AnalysisLine = {
  id: string;
  repertoireId: string;
  repertoireName: string;
  title: string;
  side: PieceColor;
  moves: string[];
  startingFen: string;
  validation?: LineValidation;
};

export type LineDiagnostic = {
  ply: number;
  move: string;
  kind: "null" | "invalid" | "illegal";
  message: string;
};

export type LineValidation = {
  valid: boolean;
  truncated: boolean;
  diagnostics: LineDiagnostic[];
};

export type CanonicalLine = Omit<AnalysisLine, "moves" | "validation"> & {
  moves: string[];
  validation: LineValidation;
};

export type BuilderHistoryEntry = { san: string; uci: string; fen: string };

export type BuilderSession = {
  version: 1;
  activeRepertoireByColor: Partial<Record<PieceColor, string>>;
  activeRepertoireId?: string;
  orientation: PieceColor;
  startingFen: string;
  history: BuilderHistoryEntry[];
  cursor: number;
  branchStart: number | null;
};

export type ArrowColors = "green" | "red" | "blue" | "yellow";

export type AnnotationArrow = {
  from: string;
  to: string;
  color: ArrowColors;
};

export type AnnotationSquare = {
  square: string;
  color: ArrowColors;
};

export type PositionAnnotation = {
  repertoireId: string;
  fenKey: string;
  comment: string;
  arrows: AnnotationArrow[];
  squares: AnnotationSquare[];
  updatedAt: string;
};

export type TeachingState = {
  cardId: CardId;
  revision: number;
  ply: number;
  taughtAt: string;
};

export type RepertoireItem = {
  id: string;
  side: PieceColor;
  title: string;
  sourceName: string;
  detail: string;
  progress: number;
  due: number;
  pgn?: string;
  backend?: boolean;
};

export type PackagedPuzzle = {
  DeckId: DeckId;
  DeckPosition: number;
  PuzzleId: string;
  FEN: string;
  Moves: string;
  Rating: number;
};

export type TablebaseResult = {
  category: string;
  moves?: { uci: string; san?: string; category?: string; dtz?: number }[];
};

export type PracticeCard = {
  id: string;
  kind: GamePhase | "puzzle";
  title: string;
  subtitle: string;
  startingFen: string;
  moves: string[];
  userMoveTarget: number;
  nextMove?: string;
  sourceUrl?: string;
  backendId?: string;
  queueEntryId?: number;
  queueCycle?: number;
  queueAttemptState?: string;
  attemptFailed?: boolean;
  suggestShorterPrefix?: boolean;
  orientation?: PieceColor;
  revision?: number;
  repertoireId?: string;
};

export type LocalRepertoire = {
  id: string;
  title: string;
  sourceName: string;
  side: PieceColor;
  pgn: string;
  cards: PracticeCard[];
};

export type BackendQueueCard = {
  id: string;
  queue_entry_id: number;
  cycle?: number;
  attempt_state?: string;
  attempt_failed?: boolean;
  recent_attempts_json?: string;
  start_fen: string;
  moves: string[];
  content_type: GamePhase | "tactic";
  repertoire_name: string;
  repertoire_source: string;
  source_ref?: string;
  is_main?: number;
  trained_color?: PieceColor;
  revision?: number;
  repertoire_id?: string;
};

export type GameViewRecord = {
  id: string;
  source: string;
  date: string;
  speed: string;
  color: PieceColor;
  result: string;
  opening: string;
  status: string;
  detail: string;
  flag: string;
  flagPly: number;
  moves: string[];
  startFen: string;
  analysisState?: string;
  repertoireId?: string;
};

export type CardNameSource = "title" | "moves" | "startingFen";

export type EngineStatus = "off" | "auth" | "loading" | "ready" | "error";

export type ShellModal = "import" | "tree" | "editor";

export type ShellState = {
  currentView: View;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  soundOn: boolean;
  databaseQueue: boolean;
  serviceError: string;
  modals: Record<ShellModal, boolean>;
};

export type QueueMode = "browser" | "local";

export type TrainingQueueState = {
  mode: QueueMode;
  practiceCards: PracticeCard[];
  dailyQueue: number[];
  activeCardIndex: number;
  cardsLeft: number;
  reviewed: number;
  queueNotice: string;
  importedRepertoires: LocalRepertoire[];
  firstCleanPasses: Set<string>;
};

export type AttemptLifecycleState = {
  fen: string;
  step: number;
  feedback: Feedback;
  lastMove: [string, string] | undefined;
  opponentLastMove: [string, string] | undefined;
  isLocked: boolean;
  boardAttempt: number;
  showHint: boolean;
  attemptFailed: boolean;
  failureFen: string;
  failureAnnotation: PositionAnnotation | undefined;
  teachingEncounterKey: string | null;
  teachingReadyCard: string;
};

export type PersistedSettingsState = {
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  soundOn: boolean;
  coverageTarget: number;
  maiaElo: string;
  maiaTranspositionPlies: number;
  explorerSpeeds: string;
  explorerRatings: string;
  engineWindowCp: number;
  arrowMetric: "stockfish" | "lichess" | "masters";
};
