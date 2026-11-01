import { Component, Suspense, createRef, type ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import LegacyWorkspace from '../../app/views/legacy-workspace';
import type { LegacyWorkspaceProps } from '../../app/components/optional-workspace';
import { IDBFactory } from 'fake-indexeddb';
import { closeStudyIntentStorage, studyIntentStore } from '../../database/browser/study-intent-storage';
import { useTrainingStore } from '../../app/state/training-store';
import { mapQueueCardToPracticeCard } from '../../app/domain/adapters/practice-card-adapters';

const deletion = vi.hoisted(() => ({ pending: false, failEditor: false, finish: undefined as (() => void) | undefined }));
vi.mock('../../app/lib/card-delete-command', () => ({
  pendingCardDeletion: () => deletion.pending ? { cardId: 'a', expectedRevision: 1 } : null,
  deleteCardCommand: () => new Promise<void>(resolve => { deletion.finish = resolve; }),
}));
vi.mock('../../app/lib/study-connection', async importOriginal => ({
  ...await importOriginal<typeof import('../../app/lib/study-connection')>(), independentStudy: false,
}));
vi.mock('../../app/utils/local', async importOriginal => ({ ...await importOriginal<typeof import('../../app/utils/local')>(), usesLocalApi: () => false }));
vi.mock('../../app/ImportDialogBox', () => ({ ImportDialogBox: ({ onClose }: { onClose: () => void }) => <button onClick={onClose}>Close import</button> }));
vi.mock('../../app/views/card_editor', () => ({ default: ({ practiceCard, onClose }: { practiceCard: { backendId: string }, onClose: () => void }) => { if (deletion.failEditor) throw new Error('workspace editor failed'); return <button onClick={onClose}>Editor {practiceCard.backendId}</button>; } }));
vi.mock('../../app/views/tree_browser', () => ({ TreeBrowser: ({ onClose }: { onClose: () => void }) => <button onClick={onClose}>Close tree</button> }));
let seenImported: unknown;
vi.mock('../../app/views/repertoire_view', () => ({ default: ({ imported }: { imported: unknown }) => { seenImported = imported; return <div>Repertoire</div>; } }));
vi.mock('../../app/features/product-api/RepertoireAuditMount', () => ({ RepertoireAuditMount: ({ children, onNavigate }: { children: React.ReactNode, onNavigate: (intent: { kind: string }) => void }) => <>{children}<button onClick={() => onNavigate({ kind: 'manual_edit' })}>Open tree</button></> }));
vi.mock('../../app/components/integrity-repair-status', () => ({ IntegrityRepairStatus: () => null }));
vi.mock('../../app/components/service-status-panel', () => ({ ServiceStatusPanel: () => null }));
vi.mock('../../app/components/pending-discovery-saves', () => ({ PendingDiscoverySaves: () => null }));
vi.mock('../../app/components/workbench-launch', () => ({ WorkbenchLaunch: () => null }));
const card = (id: string, entry: number) => mapQueueCardToPracticeCard({ id, revision: 1, queue_entry_id: entry,
  start_fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', moves: ['e2e4'], content_type: 'opening', repertoire_name: 'Fixture', repertoire_source: 'PGN' });
const a = card('a', 11), b = card('b', 12);
const noop = () => {};
const asyncNoop = async () => {};
class WorkspaceFailureBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <div>Workspace unavailable</div> : this.props.children; }
}
function mount() {
  const props: LegacyWorkspaceProps = { controllerRef: createRef(), currentView: 'repertoire', changeWorkspace: noop,
    boardTheme: 'brown', pieceSet: 'cburnett', soundOn: false, changeBoardTheme: noop, changePieceSet: noop,
    changeSound: noop, studyOnlyMode: false, safeBreakCounter: 0, incrementSafeBreak: noop, attemptPhase: 'active',
    importedRepertoires: useTrainingStore.getState().importedRepertoires,
    databaseQueue: useTrainingStore.getState().isDatabaseQueueActive, offlineQueue: useTrainingStore.getState().isOfflineQueueActive,
    onIntegrityReconciled: noop, reviewed: 0, cardsLeft: 2, totalCards: 2, refreshDatabaseQueue: asyncNoop, refreshQueueOnly: asyncNoop,
    invalidateBranchEvidence: noop, refreshQueueAfterRepertoireChange: asyncNoop,
    refreshDatabaseQueueAfterRepertoireChange: asyncNoop, addImportedRepertoire: noop, renameLocalRepertoire: noop,
    deleteLocalRepertoire: noop, onCardSaved: noop, finishPermanentDeletion: asyncNoop,
    children: slots => <>{slots.topActions}{slots.content}{slots.dialogs}<button onClick={() => slots.onEditCard?.(a)}>Edit A</button><button onClick={() => slots.onEditCard?.(b)}>Edit B</button></> };
  return render(<WorkspaceFailureBoundary><Suspense fallback={null}><LegacyWorkspace {...props} /></Suspense></WorkspaceFailureBoundary>);
}
afterEach(() => { cleanup(); closeStudyIntentStorage(); vi.unstubAllGlobals(); deletion.pending = false; deletion.failEditor = false; deletion.finish = undefined; });
it('import_editor_state_is_workspace_owned_and_unmounting_preserves_active_attempt', async () => {
  vi.stubGlobal('indexedDB', new IDBFactory());
  const store = await studyIntentStore();
  await store.put({ key: 'review:original-save', kind: 'review', attemptId: 'original-save', requestId: 'review-attempt:original-save', endpoint: '/api/cards/a/review', createdAt: '2026-11-01T00:00:00Z', payload: { cardId: 'a' } });
  const saved = await store.get('review:original-save');
  useTrainingStore.getState().hydrateLocalQueue([a, b], true);
  useTrainingStore.setState({ pendingReviewError: 'save identity retained' });
  const before = useTrainingStore.getState();
  const mounted = mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Open local data menu' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Import or transfer data' }));
  fireEvent.click(await screen.findByText('Close import'));
  fireEvent.click(await screen.findByText('Open tree'));
  fireEvent.click(screen.getByText('Close tree'));
  fireEvent.click(screen.getByText('Edit A'));
  fireEvent.click(await screen.findByText('Editor a'));
  mounted.unmount();
  const after = useTrainingStore.getState();
  expect(after.attempt).toBe(before.attempt);
  expect(after.practiceCards).toBe(before.practiceCards);
  expect(after.activeCardIndex).toBe(before.activeCardIndex);
  expect(after.pendingReviewError).toBe(before.pendingReviewError);
  expect(await store.get('review:original-save')).toEqual(saved);
});
it('deletion_recovery_closes_only_the_matching_current_editor', async () => {
  deletion.pending = true;
  useTrainingStore.setState({ isDatabaseQueueActive: true, isOfflineQueueActive: false });
  mount();
  await waitFor(() => expect(deletion.finish).toBeTypeOf('function'));
  fireEvent.click(screen.getByText('Edit A'));
  await screen.findByText('Editor a');
  fireEvent.click(screen.getByText('Edit B'));
  await screen.findByText('Editor b');
  await act(async () => deletion.finish?.());
  expect(screen.getByText('Editor b')).toBeTruthy();
});

it('deletion_recovery_closes_the_matching_editor', async () => {
  deletion.pending = true;
  useTrainingStore.setState({ isDatabaseQueueActive: true, isOfflineQueueActive: false });
  mount();
  await waitFor(() => expect(deletion.finish).toBeTypeOf('function'));
  fireEvent.click(screen.getByText('Edit A'));
  await screen.findByText('Editor a');
  await act(async () => deletion.finish?.());
  await waitFor(() => expect(screen.queryByText('Editor a')).toBeNull());
});

it('workspace_has_no_private_training_store_or_queue_view_import', async () => {
  const { readFile } = await import('node:fs/promises');
  const source = await readFile('app/views/legacy-workspace.tsx', 'utf8');
  expect(source).not.toMatch(/from\s+["'][^"']*(training-store|fetchAndInitializeQueue)["']/);
  expect(source).not.toMatch(/useTrainingStore/);
});

it('workspace_failure_preserves_active_attempt_queue_and_pending_save', async () => {
  vi.stubGlobal('indexedDB', new IDBFactory());
  const store = await studyIntentStore();
  await store.put({ key: 'review:failure-save', kind: 'review', attemptId: 'failure-save',
    requestId: 'review-attempt:failure-save', endpoint: '/api/cards/a/review',
    createdAt: '2026-11-01T00:00:00Z', payload: { cardId: 'a' } });
  const saved = await store.get('review:failure-save');
  useTrainingStore.getState().hydrateLocalQueue([a, b], true);
  const before = useTrainingStore.getState();
  deletion.failEditor = true;
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    mount();
    fireEvent.click(await screen.findByText('Edit A'));
    await screen.findByText('Workspace unavailable');
    const after = useTrainingStore.getState();
    expect(after.attempt).toBe(before.attempt);
    expect(after.practiceCards).toBe(before.practiceCards);
    expect(after.activeCardIndex).toBe(before.activeCardIndex);
    expect(await store.get('review:failure-save')).toEqual(saved);
  } finally { error.mockRestore(); }
});

it('workspace_receives_the_original_imported_content_reference', async () => {
  const imported = useTrainingStore.getState().importedRepertoires;
  mount();
  await screen.findByText('Repertoire');
  expect(seenImported).toBe(imported);
});
