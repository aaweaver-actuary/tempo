import { BoardTheme, PieceSet } from "./components/chessboard";

export type PieceColor = "white" | "black";
export type GamePhase = "opening" | "middlegame" | "endgame";

export type Feedback = "ready" | "correct" | "branch" | "wrong" | "complete";

export type View =
  | "train"
  | "tactics"
  | "endgames"
  | "repertoire"
  | "analysis"
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
  DeckId: string;
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
  orientation?: PieceColor;
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
  start_fen: string;
  moves: string[];
  content_type: GamePhase | "tactic";
  repertoire_name: string;
  repertoire_source: string;
  source_ref?: string;
  is_main?: number;
  trained_color?: PieceColor;
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
};

export type CardNameSource = "title" | "moves" | "startingFen";

export type EngineStatus = "off" | "auth" | "loading" | "ready" | "error";
