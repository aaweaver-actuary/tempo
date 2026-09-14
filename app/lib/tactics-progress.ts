export type TacticProgress = Record<string, { clean: number; index: number }>;

export function tacticProgressKey(motif: string, stage: string) {
  return `${motif}:${stage}`;
}

export function readTacticProgress(): TacticProgress {
  if (typeof window === 'undefined') return {};
  try { return JSON.parse(localStorage.getItem('tempo-tactics-progress-v2') ?? '{}'); }
  catch { return {}; }
}

export function writeTacticProgress(progress: TacticProgress) {
  localStorage.setItem('tempo-tactics-progress-v2', JSON.stringify(progress));
}

export function advanceTacticProgress(progress: TacticProgress, key: string, clean: boolean) {
  const current = progress[key] ?? { clean: 0, index: 0 };
  return { ...progress, [key]: { clean: current.clean + (clean ? 1 : 0), index: current.index + 1 } };
}
