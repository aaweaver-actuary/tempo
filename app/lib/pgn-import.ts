import { Chess } from 'chess.js';
import {
  asCardId,
  asFenString,
  asRepertoireId,
  asSanMove,
  type LocalRepertoire,
  type PracticeCard,
  type SanMove,
} from '../types';

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
    const startingFen = asFenString(headers.FEN || STANDARD_FEN);
    const replay = new Chess(startingFen);
    let segmentStartingFen = startingFen;
    let segmentMoves: SanMove[] = [];
    let learnerDecisions = 0;
    for (const san of allMoves) {
      const movingColor = replay.turn();
      segmentMoves.push(asSanMove(san));
      replay.move(san);
      if (movingColor !== (trainedColor === 'white' ? 'w' : 'b')) continue;
      const identity = `${segmentStartingFen.split(' ').slice(0, 4).join(' ')}|${segmentMoves.join(' ')}`;
      cards.push({
        id: asCardId(`import-${stableId(identity)}`),
        kind: 'opening',
        title: headers.Opening || headers.Event || fileName.replace(/\.pgn$/i, ''),
        subtitle: headers.Variation || `Imported from ${fileName}`,
        startingFen: segmentStartingFen,
        moves: segmentMoves,
        userMoveTarget: 1,
        orientation: trainedColor,
      });
      learnerDecisions += 1;
      if (learnerDecisions >= initialDepth) break;
      segmentStartingFen = asFenString(replay.fen());
      segmentMoves = [];
    }
  }
  const uniqueCards = [...new Map(cards.map((card) => [card.id, card])).values()];
  if (!uniqueCards.length) throw new Error('No playable main lines were found in this PGN.');
  const sourceName = fileName || 'Imported repertoire.pgn';
  const repertoire: LocalRepertoire = {
    id: asRepertoireId(`repertoire-${stableId(`${sourceName}|${trainedColor}|${uniqueCards.map((card) => card.id).join('|')}`)}`),
    title: sourceName.replace(/\.pgn$/i, ''),
    sourceName,
    side: trainedColor === 'white' ? 'white' : 'black',
    pgn,
    cards: uniqueCards,
  };
  repertoire.cards = uniqueCards.map((card) => ({
    ...card,
    repertoireId: repertoire.id,
    revision: 1,
  }));
  return { repertoire, cards: repertoire.cards, duplicateLines: cards.length - uniqueCards.length };
}
