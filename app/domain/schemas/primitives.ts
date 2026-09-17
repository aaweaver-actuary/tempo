import * as z from "zod";
import { Chess } from "chess.js";

export const identifierSchema = z.string().trim().min(1).max(512);
export const cardIdSchema = identifierSchema.brand<"CardId">();
export const repertoireIdSchema = identifierSchema.brand<"RepertoireId">();
export const lineIdSchema = identifierSchema.brand<"LineId">();
export const gameIdSchema = identifierSchema.brand<"GameId">();
export const deckIdSchema = identifierSchema.brand<"DeckId">();
export const puzzleIdSchema = identifierSchema.brand<"PuzzleId">();
export const queueEntryIdSchema = z
  .number()
  .int()
  .positive()
  .brand<"QueueEntryId">();
export const nonNegativeIntegerSchema = z
  .number()
  .int()
  .nonnegative()
  .brand<"NonNegativeInteger">();
export const isoDateSchema = z.iso
  .datetime({ offset: true })
  .brand<"IsoDateString">();
export const squareSchema = z.templateLiteral([
  z.enum(["a", "b", "c", "d", "e", "f", "g", "h"]),
  z.enum(["1", "2", "3", "4", "5", "6", "7", "8"]),
]);
export const colorSchema = z.enum(["white", "black"]);
export const uciMoveSchema = z
  .string()
  .trim()
  .toLowerCase()
  .regex(/^[a-h][1-8][a-h][1-8][qrbn]?$/)
  .brand<"UciMove">();
export const sanMoveSchema = z
  .string()
  .trim()
  .min(1)
  .max(32)
  .brand<"SanMove">();

function isFen(value: string): boolean {
  try {
    new Chess(value);
    return value.split(/\s+/).length === 6;
  } catch {
    return false;
  }
}
export const fenStringSchema = z
  .string()
  .trim()
  .refine(isFen, "Invalid six-field FEN")
  .brand<"FenString">();
export const fenKeySchema = z
  .string()
  .trim()
  .refine(
    (value) => value.split(/\s+/).length === 4 && isFen(`${value} 0 1`),
    "Invalid four-field FEN key",
  )
  .brand<"FenKey">();
export const sqliteBooleanSchema = z
  .union([z.boolean(), z.literal(0), z.literal(1)])
  .transform((value) => Boolean(value));
