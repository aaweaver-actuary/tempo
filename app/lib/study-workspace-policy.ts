import { ViewEnum, type View } from '../types';

const studyWorkspaces = new Set<View>(['train', 'tactics', 'settings']);
/** Composition boundary, also applied to direct intents rather than only menus. */
export function canOpenWorkspace(view: View, study: boolean): boolean {
  return Object.values(ViewEnum).includes(view as ViewEnum) && (!study || studyWorkspaces.has(view));
}
