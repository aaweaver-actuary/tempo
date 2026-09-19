import type {
  FenString,
  GameId,
  PieceColor,
  RepertoireId,
  SanMove,
} from "./shared";

export enum GameSpeed {
  Bullet = "bullet",
  Blitz = "blitz",
  Rapid = "rapid",
  Classical = "classical",
  Unknown = "unknown",
}

export enum GameProvider {
  Lichess = "lichess",
  ChessCom = "chess.com",
}

export enum GameSyncJobStatus {
  Queued = "queued",
  Running = "running",
  Paused = "paused",
  Retrying = "retrying",
  Complete = "complete",
  Failed = "failed",
}

export enum GameFindingKind {
  RepertoireLapse = "repertoire lapse",
  RepertoireGap = "repertoire gap",
  MajorMistake = "major mistake",
  Blunder = "blunder",
  FirstBigMistake = "first big mistake",
  TacticalMiss = "tactical miss",
}

export enum GameFindingStatus {
  Pending = "pending",
  Accepted = "accepted",
  Ignored = "ignored",
  Excluded = "excluded",
}

export enum GameResult {
  Won = "won",
  Lost = "lost",
  Draw = "draw",
  Unknown = "unknown",
}

export enum GameAnalysisState {
  Pending = "pending",
  Complete = "complete",
  Failed = "failed",
  Unknown = "unknown",
}

export enum GameCoverageStatus {
  Covered = "covered",
  OpponentGap = "opponent gap",
  PlayerDeviation = "player deviation",
  OutOfBook = "out of book",
  NoRepertoire = "no repertoire",
  Unknown = "unknown",
}

export type GameSpeedValue = `${GameSpeed}`;
export type GameProviderValue = `${GameProvider}`;
export type GameSyncJobStatusValue = `${GameSyncJobStatus}`;
export type GameFindingKindValue = `${GameFindingKind}`;
export type GameFindingStatusValue = `${GameFindingStatus}`;
export type GameResultValue = `${GameResult}`;
export type GameAnalysisStateValue = `${GameAnalysisState}`;
export type GameCoverageStatusValue = `${GameCoverageStatus}`;

export type GameViewRecord = {
  id: GameId;
  source: string;
  date: string;
  speed: GameSpeedValue;
  color: PieceColor;
  result: GameResultValue;
  opening: string;
  status: GameCoverageStatusValue;
  detail: string;
  flag: string;
  flagPly: number;
  moves: Array<SanMove>;
  startFen: FenString;
  analysisState?: GameAnalysisStateValue;
  repertoireId?: RepertoireId;
  matchedPlayerDecisions?: number;
  repertoireOpportunities?: number;
  deepestCoveredPly?: number;
  firstOpponentGapPly?: number;
  outOfBookPly?: number;
  adherence?: number;
  timeline?: Array<{ ply: number; kind: string }>;
  deviationCardId?: string;
};
