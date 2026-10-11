import type { FenKey, RepertoireId, UciMove } from "../types";
import { canonicalFenKey } from "../utils/canonical-line";
import type { IndexedPosition } from "./position-similarity";

export type RepertoireAlternativeSnapshot = {
  generation: number;
  positions: readonly IndexedPosition[];
  movesByRepertoire: ReadonlyMap<RepertoireId, ReadonlyMap<FenKey, ReadonlySet<UciMove>>>;
};

export function buildRepertoireAlternativeSnapshot(
  generation: number,
  positions: readonly IndexedPosition[],
): RepertoireAlternativeSnapshot {
  const movesByRepertoire = new Map<RepertoireId, Map<FenKey, Set<UciMove>>>();
  for (const position of positions) {
    if (position.nextUci === undefined) continue;
    // Existing validated position preparation owns normalization. Branding these
    // identities must not add another FEN or move normalization to the old scan.
    const repertoireId = position.repertoireId as RepertoireId;
    const positionKey = canonicalFenKey(position.fen) as FenKey;
    let movesByPosition = movesByRepertoire.get(repertoireId);
    if (!movesByPosition) {
      movesByPosition = new Map();
      movesByRepertoire.set(repertoireId, movesByPosition);
    }
    let nextMoves = movesByPosition.get(positionKey);
    if (!nextMoves) {
      nextMoves = new Set();
      movesByPosition.set(positionKey, nextMoves);
    }
    nextMoves.add(position.nextUci as UciMove);
  }
  return { generation, positions, movesByRepertoire };
}

/** Undefined means unavailable evidence; a ready empty snapshot returns false. */
export function lookupRepertoireAlternative(
  snapshot: RepertoireAlternativeSnapshot | null,
  generation: number,
  repertoireId: RepertoireId | undefined,
  fen: string,
  nextUci: string,
): boolean | undefined {
  if (!snapshot || snapshot.generation !== generation) return undefined;
  const positionKey = canonicalFenKey(fen) as FenKey;
  return repertoireId !== undefined &&
    (snapshot.movesByRepertoire.get(repertoireId)?.get(positionKey)?.has(nextUci as UciMove) ?? false);
}
