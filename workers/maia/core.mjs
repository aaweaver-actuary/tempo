// Browser-safe computation shared by the installed worker and legacy adapters.
// No model/session initialization, filesystem access, timers, or worker loops.
import { Chess } from "chess.js";
import { MaiaError } from "./errors.mjs";

export const SUPPORTED_RATINGS = Object.freeze([1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900]);
export const VOCABULARY_SIZE = 4352;
export const PROBABILITY_TOLERANCE = 1e-9;
const pieceTypes = ["P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k"];
const uciPattern = /^[a-h][1-8][a-h][1-8][qrbn]?$/;

export function validateRating(rating, { legacy = false } = {}) {
  if (legacy ? typeof rating !== "number" || !Number.isFinite(rating) || rating <= 0 : !SUPPORTED_RATINGS.includes(rating)) {
    throw new MaiaError("unsupported_rating", "Supported Maia ratings are 1100–1900 in steps of 100");
  }
}

export function mirrorSquare(square) { return `${square[0]}${9 - Number(square[1])}`; }
export function mirrorMove(move) { return `${mirrorSquare(move.slice(0, 2))}${mirrorSquare(move.slice(2, 4))}${move.slice(4)}`; }
export function mirrorFen(fen) {
  const [placement, active, castling, enPassant, halfmove, fullmove] = fen.split(" ");
  const swapColors = (rank) => [...rank].map((piece) => /[a-z]/.test(piece) ? piece.toUpperCase() : /[A-Z]/.test(piece) ? piece.toLowerCase() : piece).join("");
  const rights = [castling.includes("k") ? "K" : "", castling.includes("q") ? "Q" : "", castling.includes("K") ? "k" : "", castling.includes("Q") ? "q" : ""].join("") || "-";
  return `${placement.split("/").reverse().map(swapColors).join("/")} ${active === "w" ? "b" : "w"} ${rights} ${enPassant === "-" ? "-" : mirrorSquare(enPassant)} ${halfmove} ${fullmove}`;
}

function invalidFen(message, cause) { throw new MaiaError("invalid_fen", message, { cause }); }
const moveUci = (move) => `${move.from}${move.to}${move.promotion ?? ""}`;

/** Validate local chess coherence; this is not a retrograde reachability solver. */
export function validatePosition(fen) {
  if (typeof fen !== "string" || fen.length > 120) invalidFen("FEN must be a string of at most 120 characters");
  const fields = fen.split(" ");
  if (fields.length !== 6 || fields.some((field) => !field) || !/^[1-8prnbqkPRNBQK/]+$/.test(fields[0]) || !/^(0|[1-9]\d*)$/.test(fields[4]) || !Number.isSafeInteger(Number(fields[4])) || !/^[1-9]\d*$/.test(fields[5]) || !Number.isSafeInteger(Number(fields[5])) || !/^(?:K?Q?k?q?|-)$/.test(fields[2])) {
    invalidFen("FEN requires six fields, integer counters, and distinct ordered castling rights");
  }
  let board;
  try { board = new Chess(fen); } catch (error) { invalidFen(`Invalid FEN: ${error.message}`, error); }
  const kings = {};
  const material = { w: { total: 0, pawns: 0, queens: 0, rooks: 0, knights: 0, bishops: [0, 0] }, b: { total: 0, pawns: 0, queens: 0, rooks: 0, knights: 0, bishops: [0, 0] } };
  for (const rank of board.board()) for (const piece of rank) if (piece) {
    const counts = material[piece.color];
    counts.total += 1;
    if (piece.type === "k") kings[piece.color] = piece.square;
    else if (piece.type === "p") counts.pawns += 1;
    else if (piece.type === "q") counts.queens += 1;
    else if (piece.type === "r") counts.rooks += 1;
    else if (piece.type === "n") counts.knights += 1;
    else if (piece.type === "b") counts.bishops[(piece.square.charCodeAt(0) - 97 + Number(piece.square[1])) % 2] += 1;
  }
  for (const counts of Object.values(material)) {
    const minimumPromotions = Math.max(0, counts.queens - 1) + Math.max(0, counts.rooks - 2) + Math.max(0, counts.knights - 2) + counts.bishops.reduce((sum, count) => sum + Math.max(0, count - 1), 0);
    if (counts.pawns > 8 || counts.total > 16 || minimumPromotions > 8 - counts.pawns) invalidFen("Position exceeds standard chess material or promotion limits");
  }
  const whiteKing = kings.w;
  const blackKing = kings.b;
  if (Math.abs(whiteKing.charCodeAt(0) - blackKing.charCodeAt(0)) <= 1 && Math.abs(Number(whiteKing[1]) - Number(blackKing[1])) <= 1) invalidFen("Kings cannot occupy adjacent squares");
  const activeColor = board.turn();
  if (board.isAttacked(kings[activeColor === "w" ? "b" : "w"], activeColor)) invalidFen("The side not to move cannot already be in check");
  for (const [right, color, kingSquare, rookSquare] of [["K", "w", "e1", "h1"], ["Q", "w", "e1", "a1"], ["k", "b", "e8", "h8"], ["q", "b", "e8", "a8"]]) {
    if (fields[2].includes(right) && (board.get(kingSquare)?.type !== "k" || board.get(kingSquare)?.color !== color || board.get(rookSquare)?.type !== "r" || board.get(rookSquare)?.color !== color)) invalidFen("Castling rights require the matching home king and rook");
  }
  // The original target may legally be uncapturable or pinned. Validate its
  // preceding double push before chess.js canonicalizes such targets away.
  if (fields[3] !== "-") {
    const target = fields[3];
    const previousColor = activeColor === "w" ? "b" : "w";
    const pawnSquare = `${target[0]}${activeColor === "w" ? "5" : "4"}`;
    const sourceSquare = `${target[0]}${activeColor === "w" ? "7" : "2"}`;
    if (board.get(target) || board.get(sourceSquare) || board.get(pawnSquare)?.type !== "p" || board.get(pawnSquare)?.color !== previousColor || fields[4] !== "0") invalidFen("En-passant target is inconsistent with the preceding double pawn push");
  }
  const legalMoves = board.moves({ verbose: true }).map(moveUci).sort();
  return { board, fen: board.fen(), legalMoves, terminal: legalMoves.length === 0, terminalReason: legalMoves.length ? null : board.isCheckmate() ? "checkmate" : "stalemate" };
}

export function validateVocabularies(moveIndex, reverseIndex) {
  if (!moveIndex || !reverseIndex || Object.keys(moveIndex).length !== VOCABULARY_SIZE || Object.keys(reverseIndex).length !== VOCABULARY_SIZE) throw new MaiaError("model_provenance_mismatch", "Maia vocabularies must contain exactly 4352 entries");
  const seenIndexes = new Set();
  for (const [move, index] of Object.entries(moveIndex)) {
    if (!uciPattern.test(move) || !Number.isInteger(index) || index < 0 || index >= VOCABULARY_SIZE || seenIndexes.has(index) || reverseIndex[String(index)] !== move) throw new MaiaError("model_provenance_mismatch", "Maia move vocabularies must form an exact bijection");
    seenIndexes.add(index);
  }
}

export function preparePosition(fen, requestedMoves, moveIndex, reverseIndex) {
  const position = validatePosition(fen);
  if (position.terminal) throw new MaiaError("terminal_position", `Position is ${position.terminalReason}`);
  if (!Array.isArray(requestedMoves) || !requestedMoves.length || requestedMoves.length > 256 || requestedMoves.some((move) => typeof move !== "string" || !uciPattern.test(move)) || new Set(requestedMoves).size !== requestedMoves.length) throw new MaiaError("invalid_legal_moves", "Requested moves must be distinct UCI moves");
  const legalMoveSet = new Set(position.legalMoves);
  if (requestedMoves.some((move) => !legalMoveSet.has(move))) throw new MaiaError("invalid_legal_moves", "Requested move is not legal in the supplied position");
  const isBlack = position.board.turn() === "b";
  const normalizedFen = isBlack ? mirrorFen(position.fen) : position.fen;
  const normalizedBoard = new Chess(normalizedFen);
  const entriesByMove = new Map();
  for (const move of normalizedBoard.moves({ verbose: true })) {
    const modelMove = moveUci(move);
    const index = moveIndex[modelMove];
    if (!Number.isInteger(index) || index < 0 || index >= VOCABULARY_SIZE || reverseIndex[String(index)] !== modelMove) throw new MaiaError("model_provenance_mismatch", `Maia vocabulary omits or mismaps legal move ${modelMove}`);
    const originalMove = isBlack ? mirrorMove(modelMove) : modelMove;
    if (entriesByMove.has(originalMove)) throw new MaiaError("model_provenance_mismatch", "Duplicate decoded Maia move");
    entriesByMove.set(originalMove, { move: originalMove, index });
  }
  if (entriesByMove.size !== legalMoveSet.size || [...entriesByMove.keys()].some((move) => !legalMoveSet.has(move))) throw new MaiaError("model_provenance_mismatch", "Maia orientation changed the legal move universe");
  const tokens = new Float32Array(64 * 12);
  normalizedFen.split(" ")[0].split("/").forEach((rankText, rankFromTop) => {
    let file = 0;
    for (const piece of rankText) {
      if (/\d/.test(piece)) file += Number(piece);
      else { tokens[((7 - rankFromTop) * 8 + file) * 12 + pieceTypes.indexOf(piece)] = 1; file += 1; }
    }
  });
  return { tokens, entries: requestedMoves.map((move) => entriesByMove.get(move)), legalMoves: position.legalMoves, requestedMoves: [...requestedMoves], isBlack, normalizedFen };
}

/** Stable binary64 softmax conditional on exactly the requested move set. */
export function decodePredictions(logits, prepared) {
  if (!(logits instanceof Float32Array) || logits.length !== VOCABULARY_SIZE || !logits.every(Number.isFinite)) throw new MaiaError("invalid_model_output", "Expected 4352 finite float32 move logits");
  if (!prepared.entries.length || prepared.entries.length !== prepared.requestedMoves.length || new Set(prepared.entries.map((entry) => entry.move)).size !== prepared.entries.length || prepared.entries.some((entry, index) => entry.move !== prepared.requestedMoves[index] || !Number.isInteger(entry.index) || entry.index < 0 || entry.index >= VOCABULARY_SIZE)) throw new MaiaError("invalid_model_output", "Prepared move set is invalid");
  const maximum = Math.max(...prepared.entries.map((entry) => logits[entry.index]));
  const weights = prepared.entries.map((entry) => Math.exp(logits[entry.index] - maximum));
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  if (!Number.isFinite(total) || total <= 0) throw new MaiaError("invalid_model_output", "Maia softmax has invalid total weight");
  const predictions = prepared.entries.map((entry, index) => ({ move: entry.move, probability: weights[index] / total }));
  if (predictions.some((prediction) => !Number.isFinite(prediction.probability) || prediction.probability < 0) || Math.abs(predictions.reduce((sum, prediction) => sum + prediction.probability, 0) - 1) > PROBABILITY_TOLERANCE) throw new MaiaError("invalid_model_output", "Maia probabilities have invalid normalization");
  return predictions;
}
