import type { PracticeCard } from "./cards";
import type { PieceColor, RepertoireId } from "./shared";

export enum RepertoireSourceKind {
  LocalPgn = "local-pgn",
  BackendService = "backend-service",
  TempoExamples = "tempo-examples",
  Lichess = "lichess",
  ChessCom = "chess-com",
  Generated = "generated",
  Unknown = "unknown",
}

export type RepertoireSource = `${RepertoireSourceKind}` | string;

export type RepertoireItem = {
  id: RepertoireId;
  side: PieceColor;
  title: string;
  sourceName: RepertoireSource;
  detail: string;
  progress: number;
  due: number;
  pgn?: string;
  backend?: boolean;
  conflictCount?: number;
};

export type LocalRepertoire = {
  id: RepertoireId;
  title: string;
  sourceName: RepertoireSource;
  side: PieceColor;
  pgn: string;
  cards: PracticeCard[];
};
