import { Chess } from 'chess.js';
import type { LocalRepertoire, PracticeCard } from './domain';

const STANDARD_FEN = new Chess().fen();

function stableId(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function gameBlocks(pgn: string) {
  const blocks = pgn.trim().split(/\n(?=\s*\[Event\s)/).map((value) => value.trim()).filter(Boolean);
  return blocks.length ? blocks : [pgn];
}

export function parsePgnImport(fileName: string, pgn: string, trainedColor: 'white' | 'black', initialDepth: number) {
  const cards: PracticeCard[] = [];
  for (const block of gameBlocks(pgn)) {
    const chess = new Chess();
    try { chess.loadPgn(block, { strict: false }); } catch { continue; }
    const headers = chess.getHeaders();
    const allMoves = chess.history();
    const prefixLength = Math.min(allMoves.length, initialDepth * 2 - (trainedColor === 'white' ? 1 : 0));
    if (!prefixLength) continue;
    const startingFen = headers.FEN || STANDARD_FEN;
    const moves = allMoves.slice(0, prefixLength);
    const identity = `${startingFen.split(' ').slice(0, 4).join(' ')}|${moves.join(' ')}`;
    cards.push({
      id: `import-${stableId(identity)}`,
      kind: 'opening',
      title: headers.Opening || headers.Event || fileName.replace(/\.pgn$/i, ''),
      subtitle: headers.Variation || `Imported from ${fileName}`,
      startingFen,
      moves,
      userMoveTarget: Math.ceil(prefixLength / 2),
      orientation: trainedColor,
    });
  }
  const uniqueCards = [...new Map(cards.map((card) => [card.id, card])).values()];
  if (!uniqueCards.length) throw new Error('No playable main lines were found in this PGN.');
  const sourceName = fileName || 'Imported repertoire.pgn';
  const repertoire: LocalRepertoire = {
    id: `repertoire-${stableId(`${sourceName}|${trainedColor}|${uniqueCards.map((card) => card.id).join('|')}`)}`,
    title: sourceName.replace(/\.pgn$/i, ''),
    sourceName,
    side: trainedColor === 'white' ? 'White' : 'Black',
    pgn,
    cards: uniqueCards,
  };
  return { repertoire, cards: uniqueCards, duplicateLines: cards.length - uniqueCards.length };
}
