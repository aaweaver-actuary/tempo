import { Chess, type Color, type Square } from 'chess.js';

export type EndgameMaterial = {
  white: string;
  black: string;
  name: string;
};

function toFen(rows: (string | null)[][], turn: Color) {
  const placement = rows.map((row) => {
    let empty = 0;
    let value = '';
    for (const piece of row) {
      if (!piece) { empty += 1; continue; }
      if (empty) { value += String(empty); empty = 0; }
      value += piece;
    }
    return value + (empty ? String(empty) : '');
  }).join('/');
  return `${placement} ${turn} - - 0 1`;
}

function shuffledSquares(random: () => number) {
  const squares = Array.from({ length: 64 }, (_, index) => index);
  for (let index = squares.length - 1; index > 0; index -= 1) {
    const swap = Math.floor(random() * (index + 1));
    [squares[index], squares[swap]] = [squares[swap], squares[index]];
  }
  return squares;
}

export function generateLegalEndgameFen(material: EndgameMaterial, turn: Color = 'w', random = Math.random) {
  for (let attempt = 0; attempt < 1_000; attempt += 1) {
    const squares = shuffledSquares(random);
    const rows = Array.from({ length: 8 }, () => Array<string | null>(8).fill(null));
    for (const [color, pieces] of [['w', material.white], ['b', material.black]] as const) {
      for (const piece of pieces) {
        const index = squares.pop();
        if (index === undefined) throw new Error('Not enough squares for this material');
        rows[Math.floor(index / 8)][index % 8] = color === 'w' ? piece.toUpperCase() : piece.toLowerCase();
      }
    }
    const fen = toFen(rows, turn);
    try {
      const chess = new Chess(fen);
      const idleKing = chess.board().flat().find((piece) => piece?.type === 'k' && piece.color !== turn);
      if (!idleKing || chess.isAttacked(idleKing.square as Square, turn)) continue;
      if (!chess.isGameOver()) return fen;
    } catch { /* Try another placement. */ }
  }
  throw new Error('Could not generate a legal position for that material');
}
