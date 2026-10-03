import { useCallback, useMemo, useState } from "react";
import { Chess, type Square } from "chess.js";
import { asSanMove, type SanMove } from "../types";
import { editFenSquare, fenAfterMoves, moveFenPiece } from "../utils/fen";

export const EMPTY_SETUP_FEN = "8/8/8/8/8/8/8/8 w - - 0 1";

function renderableSetup(fen: string): boolean {
  const fields = fen.trim().split(/\s+/);
  if (fields.length !== 6 || !/^[wb]$/.test(fields[1]) ||
      !/^(-|K?Q?k?q?)$/.test(fields[2]) || !/^(-|[a-h][36])$/.test(fields[3]) ||
      !/^\d+$/.test(fields[4]) || !/^[1-9]\d*$/.test(fields[5])) return false;
  const ranks = fields[0].split("/");
  return ranks.length === 8 && ranks.every(rank => /^[1-8prnbqkPRNBQK]+$/.test(rank) &&
    [...rank].reduce((count, piece) => count + (/\d/.test(piece) ? Number(piece) : 1), 0) === 8);
}

export function playablePositionError(fen: string): string | undefined {
  try {
    const board = new Chess(fen);
    const oppositeKing = board.board().flat().find(piece => piece?.type === "k" && piece.color !== board.turn());
    if (!oppositeKing || board.isAttacked(oppositeKing.square, board.turn()))
      return "The side not moving cannot be in check.";
    return undefined;
  } catch {
    return "Set up a playable position with both kings before recording a solution.";
  }
}

export function solutionUciMoves(fen: string, moves: readonly string[]): string[] {
  const board = new Chess(fen);
  return moves.map(san => {
    const move = board.move(san);
    return `${move.from}${move.to}${move.promotion ?? ""}`;
  });
}

export function usePositionSolutionEditor(initialFen: string, initialMoves: SanMove[] = [], confirmReset = false) {
  const [startingFen, setFenText] = useState(initialFen);
  const [boardFen, setBoardFen] = useState(renderableSetup(initialFen) ? initialFen : EMPTY_SETUP_FEN);
  const [moves, setMoves] = useState(initialMoves);
  const [cursor, setCursor] = useState(0);
  const [workingCursor, setWorkingCursor] = useState(0);
  const [tab, setTab] = useState<"position" | "solution">("position");
  const [piece, setPiece] = useState<string | null>(confirmReset ? null : "B");
  const [promotion, setPromotion] = useState("q");
  const [error, setError] = useState("");
  const [pendingFen, setPendingFen] = useState<string | null>(null);
  const setStartingFen = useCallback((fen: string) => {
    setFenText(fen);
    if (renderableSetup(fen)) setBoardFen(fen);
    setCursor(0);
    setWorkingCursor(0);
  }, []);
  function applyStartingFen(fen: string) {
    setStartingFen(fen);
    if (confirmReset) { setMoves([]); setTab("position"); }
    setError("");
  }
  function changeStartingFen(fen: string) {
    if (fen === startingFen) return;
    if (confirmReset && moves.length) setPendingFen(fen);
    else applyStartingFen(fen);
  }
  function loadPositionAndSolution(fen: string, solutionMoves: readonly SanMove[]) {
    setFenText(fen);
    setBoardFen(fen);
    setMoves([...solutionMoves]);
    setCursor(0);
    setWorkingCursor(0);
    setTab("solution");
    setPendingFen(null);
    setPiece(null);
    setPromotion("q");
    setError("");
  }
  const positionError = playablePositionError(startingFen);
  const previewFen = useMemo(() => {
    try { return fenAfterMoves(moves, cursor, startingFen); }
    catch { return boardFen; }
  }, [moves, cursor, startingFen, boardFen]);
  function playSolution(from: Square, to: Square) {
    try {
      const board = new Chess(previewFen);
      const move = board.move({ from, to, promotion });
      setMoves([...moves.slice(0, cursor), asSanMove(move.san)]);
      setCursor(cursor + 1);
      setWorkingCursor(cursor + 1);
      setError("");
    } catch { setError("That move is not legal from this position."); }
  }
  function playSanSolution(text: string): boolean {
    const tokens = text.trim().replace(/(^|\s)\d+\.(?:\.\.)?/g, "$1 ").trim().split(/\s+/);
    if (!text.trim() || tokens.every(token => !token)) {
      setError("Enter a SAN move or a sequence of SAN moves.");
      return false;
    }
    if (positionError || pendingFen !== null) {
      setError("Confirm a playable starting position before recording a solution.");
      return false;
    }
    const board = new Chess(previewFen);
    const enteredMoves: SanMove[] = [];
    for (const [index, token] of tokens.entries()) {
      try {
        const move = board.move(token, { strict: true });
        if (move.san === "--") throw new Error("Null moves are not valid tactic moves.");
        enteredMoves.push(asSanMove(move.san));
      }
      catch {
        setError(`Move ${index + 1} “${token}” is not legal SAN from this position.`);
        return false;
      }
    }
    setMoves([...moves.slice(0, cursor), ...enteredMoves]);
    setCursor(cursor + enteredMoves.length);
    setWorkingCursor(cursor + enteredMoves.length);
    setError("");
    return true;
  }
  return {
    startingFen, boardFen, setStartingFen, changeStartingFen, loadPositionAndSolution, positionError,
    moves, setMoves, cursor, setCursor, workingCursor, tab, setTab, piece, setPiece,
    promotion, setPromotion, error, setError, previewFen, pendingFen,
    confirmStartingChange() { if (pendingFen !== null) applyStartingFen(pendingFen); setPendingFen(null); },
    cancelStartingChange() { setPendingFen(null); },
    placePiece(square: Square) { if (piece !== null) changeStartingFen(editFenSquare(boardFen, square, piece)); },
    moveSetupPiece(from: Square, to: Square) { changeStartingFen(moveFenPiece(boardFen, from, to)); },
    playSolution, playSanSolution,
  };
}

export type PositionSolutionState = ReturnType<typeof usePositionSolutionEditor>;
