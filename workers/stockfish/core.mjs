import { Chess } from "chess.js";
import { WireError } from "./wire.mjs";
const moveUci = (move) => `${move.from}${move.to}${move.promotion ?? ""}`;
function invalidFen(message, cause) { throw new WireError("invalid_fen", message, { cause }); }
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

export function applyUci(board, move) {
  try { return board.move({ from: move.slice(0, 2), to: move.slice(2, 4), ...(move.length === 5 ? { promotion: move[4] } : {}) }); }
  catch { throw new WireError('illegal_history', 'Illegal move sequence'); }
}

export function prepareJob(job) {
  const { board } = validatePosition(job.fen);
  for (const move of job.moves) applyUci(board, move);
  const legalMoves = board.moves({ verbose: true }).map(moveUci);
  if (!legalMoves.length) throw new WireError('terminal_position', 'No legal roots');
  if (new Set(job.search_moves).size !== job.search_moves.length) throw new WireError('duplicate_search_move', 'Duplicate restricted roots');
  if (job.search_moves.some(move => !legalMoves.includes(move))) throw new WireError('illegal_search_move', 'Restricted root is illegal after history');
  if (job.max_depth === null && job.max_nodes === null) throw new WireError('unsupported_search_limits', 'A depth or node limit is required');
  const roots = job.search_moves.length ? job.search_moves : legalMoves;
  return { board, roots, multiPv: Math.min(job.multi_pv, roots.length) };
}

export function validateVariations(job, variations) {
  const prepared = prepareJob(job);
  if (variations.length !== prepared.multiPv || !variations.length) throw new WireError('incomplete_engine_output', 'Incomplete MultiPV iteration');
  const roots = new Set();
  for (const [index, variation] of variations.entries()) {
    if (variation.rank !== index + 1 || variation.depth !== variations[0].depth || variation.depth < 1 || variation.depth > 128 || (job.max_depth !== null && variation.depth > job.max_depth) || variation.moves.length < 1 || variation.moves.length > 256 || !prepared.roots.includes(variation.moves[0]) || roots.has(variation.moves[0])) throw new WireError('invalid_engine_output', 'Invalid coherent PV/root/depth');
    const board = new Chess(prepared.board.fen());
    try { for (const move of variation.moves) applyUci(board, move); }
    catch { throw new WireError('invalid_engine_output', 'Illegal PV'); }
    roots.add(variation.moves[0]);
  }
  return variations;
}

/** Only a complete ordered exact MultiPV cycle becomes an immutable snapshot. */
export class IterationCollector {
  constructor(job) { this.job = job; this.prepared = prepareJob(job); this.pending = []; this.complete = null; this.metrics = {}; }
  receive(line) {
    if (typeof line !== 'string' || Buffer.byteLength(line) > 64 * 1024) throw new WireError('invalid_engine_output', 'Oversized UCI line');
    if (line.startsWith('bestmove ')) {
      const best = /^bestmove ([a-h][1-8][a-h][1-8][qrbn]?)(?: ponder ([a-h][1-8][a-h][1-8][qrbn]?))?$/.exec(line.trim());
      if (!best) throw new WireError('invalid_engine_output', 'Malformed bestmove');
      const move = best[1];
      if (best[2]) { const board = new Chess(this.prepared.board.fen()); try { applyUci(board, move); applyUci(board, best[2]); } catch { throw new WireError('invalid_engine_output', 'Illegal ponder move'); } }
      if (!this.prepared.roots.includes(move)) throw new WireError('invalid_engine_output', 'Illegal bestmove');
      if (!this.complete) throw new WireError('incomplete_engine_output', 'No complete exact iteration');
      return validateVariations(this.job, structuredClone(this.complete));
    }
    if (!line.startsWith('info ') || !line.includes(' pv ')) return null;
    const depth = Number(line.match(/\bdepth (\d+)/)?.[1]);
    const rank = Number(line.match(/\bmultipv (\d+)/)?.[1] ?? 1);
    const score = line.match(/\bscore (cp|mate) (-?\d+)\b/);
    if (!Number.isSafeInteger(depth) || depth < 1 || depth > 128 || (this.job.max_depth !== null && depth > this.job.max_depth) || !Number.isSafeInteger(rank) || rank < 1 || rank > this.prepared.multiPv || !score) throw new WireError('invalid_engine_output', 'Malformed scored PV');
    this.metrics = { last_reported_depth: depth, nodes: Number(line.match(/\bnodes (\d+)/)?.[1] ?? 0), engine_time_ms: Number(line.match(/\btime (\d+)/)?.[1] ?? 0) };
    if (/\b(?:lowerbound|upperbound)\b/.test(line)) { this.pending = []; return null; }
    if (rank === 1) this.pending = [];
    if (rank !== this.pending.length + 1 || (this.pending.length && this.pending[0].depth !== depth)) { this.pending = []; return null; }
    const variation = { rank, moves: line.split(' pv ')[1].trim().split(/\s+/), score: { kind: score[1] === 'cp' ? 'centipawns' : 'mate', value: Number(score[2]) }, score_perspective: 'side_to_move', depth };
    this.pending.push(variation);
    if (this.pending.length === this.prepared.multiPv) { validateVariations(this.job, this.pending); this.complete = structuredClone(this.pending); }
    return null;
  }
}
