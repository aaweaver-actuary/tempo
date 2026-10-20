import type { View } from '../types';

const studyWorkspaces = new Set<View>(['train', 'tactics', 'endgames']);
/** Composition boundary, also applied to direct intents rather than only menus. */
export function canOpenWorkspace(view: View, study: boolean): boolean {
  return !study || studyWorkspaces.has(view);
}
