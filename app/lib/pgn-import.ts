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
  const prefixCardIds = new Set<string>();
  let prefixOccurrences = 0;
  for (const block of gameBlocks(pgn)) {
    const chess = new Chess();
    try { chess.loadPgn(block, { strict: false }); } catch { continue; }
    const headers = chess.getHeaders();
    const allMoves = chess.history();
    const startingFen = asFenString(headers.FEN || STANDARD_FEN);
    const replay = new Chess(startingFen);
    const trainedChessColor = trainedColor === 'white' ? 'w' : 'b';
    const legalMoves: SanMove[] = [];
    const learnerMoveOffsets: number[] = [];
    for (const san of allMoves) {
      const movingColor = replay.turn();
      if (movingColor === trainedChessColor) learnerMoveOffsets.push(legalMoves.length);
      legalMoves.push(asSanMove(san));
      replay.move(san);
    }
    if (!learnerMoveOffsets.length || initialDepth <= 0) continue;

    const prefixDecisionCount = Math.min(initialDepth, learnerMoveOffsets.length);
    const prefixEndOffset = learnerMoveOffsets[prefixDecisionCount - 1] + 1;
    const prefixMoves = legalMoves.slice(0, prefixEndOffset);
    const title = headers.Opening || headers.Event || fileName.replace(/\.pgn$/i, '');
    const subtitle = headers.Variation || `Imported from ${fileName}`;
    const addCard = (
      segmentStartingFen: ReturnType<typeof asFenString>,
      segmentMoves: SanMove[],
      userMoveTarget: number,
      segmentKind: 'prefix' | 'decision',
    ) => {
      const identity = `${segmentStartingFen.split(' ').slice(0, 4).join(' ')}|${segmentMoves.join(' ')}`;
      const identifier = asCardId(`import-${stableId(identity)}`);
      if (segmentKind === 'prefix') {
        prefixOccurrences += 1;
        prefixCardIds.add(identifier);
      }
      cards.push({
        id: identifier,
        kind: 'opening',
        title,
        subtitle,
        startingFen: segmentStartingFen,
        moves: segmentMoves,
        userMoveTarget,
        orientation: trainedColor,
      });
    };
    addCard(startingFen, prefixMoves, prefixDecisionCount, 'prefix');

    const descendantReplay = new Chess(startingFen);
    for (const move of prefixMoves) descendantReplay.move(move);
    let descendantStartingFen = asFenString(descendantReplay.fen());
    let descendantMoves: SanMove[] = [];
    for (const san of legalMoves.slice(prefixEndOffset)) {
      const movingColor = descendantReplay.turn();
      descendantMoves.push(san);
      descendantReplay.move(san);
      if (movingColor !== trainedChessColor) continue;
      addCard(descendantStartingFen, descendantMoves, 1, 'decision');
      descendantStartingFen = asFenString(descendantReplay.fen());
      descendantMoves = [];
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
  return {
    repertoire,
    cards: repertoire.cards,
    duplicateLines: cards.length - uniqueCards.length,
    prefixCards: prefixCardIds.size,
    descendantCards: uniqueCards.filter((card) => !prefixCardIds.has(card.id)).length,
    sharedPrefixes: prefixOccurrences - prefixCardIds.size,
  };
}
