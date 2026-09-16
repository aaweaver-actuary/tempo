import { Chess, type Square } from "chess.js";

// Shared types and utility functions for the chess opening trainer application.
// This is a helper/base type for creating branded types, which are used to give
// primitive types unique identities within the application, thus ensuring you
// cannot use a GameId where a LineId is expected, or vice versa.
export type Brand<TValue, TBrand extends string> = TValue & {
  readonly __brand: TBrand;
};

export type CardId = Brand<string, "CardId">;
export type RepertoireId = Brand<string, "RepertoireId">;
export type QueueEntryId = Brand<number, "QueueEntryId">;
export type DeckId = Brand<string, "DeckId">;
export type LineId = Brand<string, "LineId">;
export type GameId = Brand<string, "GameId">;
export type PuzzleId = Brand<string, "PuzzleId">;
export type FenString = Brand<string, "FenString">;
export type FenKey = Brand<string, "FenKey">;
export type UciMove = Brand<string, "UciMove">;
export type SanMove = Brand<string, "SanMove">;
export type NonNegativeInteger = Brand<number, "NonNegativeInteger">;
export type IsoDateString = Brand<string, "IsoDateString">;
export type DomainDate = IsoDateString;

export type ChessMove = {
  uci: UciMove;
  san?: SanMove;
};

export type MoveSquares = readonly [Square, Square];

export const asCardId = (value: string): CardId => value as CardId;
export const asRepertoireId = (value: string): RepertoireId =>
  value as RepertoireId;
export const asQueueEntryId = (value: number): QueueEntryId =>
  value as QueueEntryId;
export const asDeckId = (value: string): DeckId => value as DeckId;
export const asLineId = (value: string): LineId => value as LineId;
export const asGameId = (value: string): GameId => value as GameId;
export const asPuzzleId = (value: string): PuzzleId => value as PuzzleId;
export const asIsoDateString = (value: string): IsoDateString =>
  value as IsoDateString;

const UCI_MOVE_RE = /^[a-h][1-8][a-h][1-8][qrbn]?$/i;

export function isFenString(value: string): value is FenString {
  try {
    void new Chess(value);
    return true;
  } catch {
    return false;
  }
}

export function asFenString(value: string): FenString {
  if (!isFenString(value)) {
    throw new Error(`Invalid FEN: ${value}`);
  }
  return value as FenString;
}

export function asFenKey(value: string): FenKey {
  return value as FenKey;
}

export function isUciMove(value: string): value is UciMove {
  return UCI_MOVE_RE.test(value.trim());
}

export function asUciMove(value: string): UciMove {
  if (!isUciMove(value)) {
    throw new Error(`Invalid UCI move: ${value}`);
  }
  return value.toLowerCase() as UciMove;
}

export function asSanMove(value: string): SanMove {
  const trimmed = value.trim();
  if (!trimmed) {
    throw new Error("SAN move cannot be empty");
  }
  return trimmed as SanMove;
}

export function asNonNegativeInteger(value: number): NonNegativeInteger {
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`Expected non-negative integer, got: ${value}`);
  }
  return value as NonNegativeInteger;
}

export enum PieceColorEnum {
  White = "white",
  Black = "black",
}

export enum GamePhaseEnum {
  Opening = "opening",
  Middlegame = "middlegame",
  Endgame = "endgame",
}

export enum FeedbackEnum {
  Ready = "ready",
  Correct = "correct",
  Branch = "branch",
  Wrong = "wrong",
  Complete = "complete",
}

export enum ViewEnum {
  Train = "train",
  Tactics = "tactics",
  Endgames = "endgames",
  Repertoire = "repertoire",
  Builder = "builder",
  Games = "games",
  Progress = "progress",
  Settings = "settings",
}

export type PieceColor = `${PieceColorEnum}`;
export type GamePhase = `${GamePhaseEnum}`;
export type Feedback = `${FeedbackEnum}`;

export type View = `${ViewEnum}`;
