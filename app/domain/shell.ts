import type { BoardTheme, PieceSet } from "../components/chessboard";
import type { View } from "./shared";

export enum EngineStatusEnum {
  Off = "off",
  Auth = "auth",
  Loading = "loading",
  Ready = "ready",
  Error = "error",
  Stale = "stale",
  RateLimited = "rate-limited",
  Offline = "offline",
}

export enum ShellModalEnum {
  Import = "import",
  Tree = "tree",
  Editor = "editor",
}

export enum ArrowMetricsEnum {
  Stockfish = "stockfish",
  Lichess = "lichess",
  Masters = "masters",
}

export type EngineStatus = `${EngineStatusEnum}`;
export type ShellModal = `${ShellModalEnum}`;
export type ArrowMetrics = `${ArrowMetricsEnum}`;

export type ShellState = {
  currentView: View;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  isSoundEnabled: boolean;
  databaseQueue: boolean;
  serviceError: string;
  modals: Record<ShellModal, boolean>;
};

export type PersistedSettingsState = {
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  isSoundEnabled: boolean;
  coverageTarget: number;
  maiaElo: string;
  maiaTranspositionPlies: number;
  explorerSpeeds: string;
  explorerRatings: string;
  engineWindowCp: number;
  arrowMetric: ArrowMetrics;
};
