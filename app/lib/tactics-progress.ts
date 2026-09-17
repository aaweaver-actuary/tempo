import { tacticProgressSchema } from "../domain/schemas";
import { readStoredValue } from "./validated-data";
export type TacticProgress = Record<
  string,
  {
    clean: number;
    index: number;
    cleanIds?: string[];
    discoveredIds?: string[];
  }
>;

export function tacticProgressKey(motif: string, stage: string) {
  return `${motif}:${stage}`;
}

export function readTacticProgress(): TacticProgress {
  if (typeof window === "undefined") return {};
  return (
    readStoredValue(
      localStorage,
      "tempo-tactics-progress-v2",
      tacticProgressSchema,
    ) ?? {}
  );
}

export function writeTacticProgress(progress: TacticProgress) {
  localStorage.setItem("tempo-tactics-progress-v2", JSON.stringify(progress));
}

export function advanceTacticProgress(
  progress: TacticProgress,
  key: string,
  clean: boolean,
  puzzleId?: string,
) {
  const current = progress[key] ?? { clean: 0, index: 0 };
  const cleanIds = [
    ...new Set([
      ...(current.cleanIds ?? []),
      ...(clean && puzzleId ? [puzzleId] : []),
    ]),
  ];
  const discoveredIds = [
    ...new Set([
      ...(current.discoveredIds ?? current.cleanIds ?? []),
      ...(puzzleId ? [puzzleId] : []),
    ]),
  ];
  return {
    ...progress,
    [key]: {
      clean: puzzleId ? cleanIds.length : current.clean + (clean ? 1 : 0),
      cleanIds,
      discoveredIds,
      index: puzzleId ? discoveredIds.length : current.index + 1,
    },
  };
}
