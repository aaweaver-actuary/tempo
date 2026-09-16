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
  NoRepertoire = "no repertoire",
  Unknown = "unknown",
}

export type GameSpeedValue = `${GameSpeed}`;
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
};
