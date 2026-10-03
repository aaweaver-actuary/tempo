import { Chess } from "chess.js";
import { asFenString, asSanMove, type FenString, type SanMove } from "../types";

export function parseChessComPuzzlePgn(rawPgn: string): {
  startingFen: FenString;
  solutionSanMoves: SanMove[];
  sourceKind: "puzzle_rush";
  sourceRef: string | null;
  sourceUrl: string | null;
} {
  if (!rawPgn.trim()) throw new Error("Paste a Chess.com puzzle PGN before loading it.");

  // chess.js removes a standard-position FEN from getHeaders(). Preserve only
  // the explicit FEN in the initial tag block; chess.js parses the whole PGN.
  const headerBlock = rawPgn.trimStart().match(/^(?:\[\s*\w+\s+"(?:\\.|[^"\\])*"\s*\]\s*)+/)?.[0] ?? "";
  const fenTags = [...headerBlock.matchAll(/\[\s*FEN\s+"([^"\\]*)"\s*\]/gi)];
  const headerFen = fenTags.at(-1)?.[1].trim();
  if (!headerFen) throw new Error("This Chess.com puzzle PGN needs a nonempty FEN header for its setup position.");
  let setupBoard: Chess;
  try { setupBoard = new Chess(headerFen); }
  catch { throw new Error("The puzzle PGN has an invalid FEN. Copy the complete FEN header from Chess.com."); }

  const parsedPuzzle = new Chess();
  try { parsedPuzzle.loadPgn(rawPgn, { strict: false }); }
  catch { throw new Error("Could not read this Chess.com puzzle PGN. Check its formatting and that every mainline move is legal from its FEN."); }
  const headers = parsedPuzzle.getHeaders();
  const sourceRef = headers.PuzzleID?.trim() || null;
  const sourceUrl = headers.Link?.trim() || null;
  let hasPuzzleLink = false;
  if (sourceUrl) {
    try {
      const link = new URL(sourceUrl);
      hasPuzzleLink = ["http:", "https:"].includes(link.protocol) &&
        ["chess.com", "www.chess.com"].includes(link.hostname) && link.pathname.startsWith("/puzzles/");
    } catch { /* PuzzleID can identify a puzzle; the normal save validates its editable URL. */ }
  }
  if (!sourceRef && !hasPuzzleLink)
    throw new Error("Use a Chess.com puzzle PGN with a PuzzleID header or a Chess.com /puzzles/ Link. Ordinary game PGNs cannot be loaded here.");

  const mainlineMoves = parsedPuzzle.history();
  if (mainlineMoves.length < 2)
    throw new Error("The puzzle PGN must contain a setup move followed by at least one solution move.");
  if (mainlineMoves.includes("--")) throw new Error("The puzzle PGN must contain real moves, not null moves.");
  setupBoard.move(mainlineMoves[0]);
  return {
    startingFen: asFenString(setupBoard.fen()),
    solutionSanMoves: mainlineMoves.slice(1).map(asSanMove),
    sourceKind: "puzzle_rush", sourceRef, sourceUrl,
  };
}
