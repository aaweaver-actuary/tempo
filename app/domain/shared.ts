import type { Square } from "chess.js";
import * as z from "zod";
import * as schemas from "./schemas/primitives";

export type Brand<TValue, TBrand extends string> = TValue & z.$brand<TBrand>;
export type CardId = z.infer<typeof schemas.cardIdSchema>;
export type RepertoireId = z.infer<typeof schemas.repertoireIdSchema>;
export type QueueEntryId = z.infer<typeof schemas.queueEntryIdSchema>;
export type DeckId = z.infer<typeof schemas.deckIdSchema>;
export type LineId = z.infer<typeof schemas.lineIdSchema>;
export type GameId = z.infer<typeof schemas.gameIdSchema>;
export type PuzzleId = z.infer<typeof schemas.puzzleIdSchema>;
export type FenString = z.infer<typeof schemas.fenStringSchema>;
export type FenKey = z.infer<typeof schemas.fenKeySchema>;
export type UciMove = z.infer<typeof schemas.uciMoveSchema>;
export type SanMove = z.infer<typeof schemas.sanMoveSchema>;
export type NonNegativeInteger = z.infer<
  typeof schemas.nonNegativeIntegerSchema
>;
export type IsoDateString = z.infer<typeof schemas.isoDateSchema>;
export type DomainDate = IsoDateString;
export type ChessMove = { uci: UciMove; san?: SanMove };
export type MoveSquares = readonly [Square, Square];
export const asCardId = (value: string): CardId =>
  schemas.cardIdSchema.parse(value);
export const asRepertoireId = (value: string): RepertoireId =>
  schemas.repertoireIdSchema.parse(value);
export const asQueueEntryId = (value: number): QueueEntryId =>
  schemas.queueEntryIdSchema.parse(value);
export const asDeckId = (value: string): DeckId =>
  schemas.deckIdSchema.parse(value);
export const asLineId = (value: string): LineId =>
  schemas.lineIdSchema.parse(value);
export const asGameId = (value: string): GameId =>
  schemas.gameIdSchema.parse(value);
export const asPuzzleId = (value: string): PuzzleId =>
  schemas.puzzleIdSchema.parse(value);
export const asFenString = (value: string): FenString =>
  schemas.fenStringSchema.parse(value);
export const asFenKey = (value: string): FenKey =>
  schemas.fenKeySchema.parse(value);
export const asUciMove = (value: string): UciMove =>
  schemas.uciMoveSchema.parse(value);
export const asSanMove = (value: string): SanMove =>
  schemas.sanMoveSchema.parse(value);
export const asNonNegativeInteger = (value: number): NonNegativeInteger =>
  schemas.nonNegativeIntegerSchema.parse(value);
export const asIsoDateString = (value: string): IsoDateString =>
  schemas.isoDateSchema.parse(value);
export function isFenString(value: string): value is FenString {
  return schemas.fenStringSchema.safeParse(value).success;
}
export function isUciMove(value: string): value is UciMove {
  return schemas.uciMoveSchema.safeParse(value).success;
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
