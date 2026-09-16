import type {
  FenString,
  LineId,
  NonNegativeInteger,
  PieceColor,
  RepertoireId,
  SanMove,
  UciMove,
} from "./shared";

export enum ValidationIssueKind {
  NullMove = "null",
  InvalidMove = "invalid",
  IllegalMove = "illegal",
}

export enum TablebaseCategory {
  Win = "win",
  Loss = "loss",
  Draw = "draw",
  BlessedLoss = "blessed-loss",
  CursedWin = "cursed-win",
  Unknown = "unknown",
}

export type ExplorerMove = {
  uci: UciMove;
  san: SanMove;
  white: NonNegativeInteger;
  draws: NonNegativeInteger;
  black: NonNegativeInteger;
};

export type AnalysisLine = {
  id: LineId;
  repertoireId: RepertoireId;
  repertoireName: string;
  title: string;
  side: PieceColor;
  moves: Array<UciMove | SanMove>;
  startingFen: FenString;
  validation?: LineValidation;
};

export type LineDiagnostic = {
  ply: number;
  move: string;
  kind: `${ValidationIssueKind}`;
  message: string;
};

export type LineValidation = {
  isValid: boolean;
  isTruncated: boolean;
  diagnostics: LineDiagnostic[];
};

export type CanonicalLine = Omit<AnalysisLine, "moves" | "validation"> & {
  moves: Array<UciMove>;
  validation: LineValidation;
};

export type BuilderHistoryEntry = {
  san: SanMove;
  uci: UciMove;
  fen: FenString;
};

export type BuilderSession = {
  version: 1;
  activeRepertoireByColor: Partial<Record<PieceColor, RepertoireId>>;
  activeRepertoireId?: RepertoireId;
  orientation: PieceColor;
  startingFen: FenString;
  history: BuilderHistoryEntry[];
  cursor: number;
  branchStart: number | null;
};

export type CardNameSource = "title" | "moves" | "startingFen";

export type TablebaseResult = {
  category: `${TablebaseCategory}`;
  moves?: {
    uci: UciMove;
    san?: SanMove;
    category?: `${TablebaseCategory}`;
    dtz?: number;
  }[];
};
