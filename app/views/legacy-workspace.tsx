import { useState, useRef, useCallback, useEffect, useImperativeHandle, lazy } from "react";
import type { LegacyWorkspaceProps } from "../components/optional-workspace";
import { WorkbenchLaunch } from "../components/workbench-launch";
import SavedLocallyButton from "../components/buttons/SavedLocallyButton";
import { Button } from "../components/buttons/BaseButton";
import { TrainingRepairNotice } from "../components/TrainingRepairNotice";
import { backgroundFetch } from "../lib/background-fetch";
import { LEARNING_URL as API_URL, learningFetch as fetch, independentStudy } from "../lib/study-connection";
import { usesLocalApi } from "../utils/local";
import { repertoiresResponseSchema } from "../domain/schemas";
import { invalidateWorkspaceData } from "../lib/workspace-data";
import { invalidateTrainingQueueCache } from "./fetchAndInitializeQueue";
import { useShallow } from "zustand/react/shallow";
import { useTrainingStore } from "../state/training-store";
import type { AnalysisPasteContext } from "../AnalysisPasteDialog";
import { deleteCardCommand, pendingCardDeletion } from "../lib/card-delete-command";
import { View } from "../types";
import { RepertoireAuditMount } from "../features/product-api/RepertoireAuditMount";
const ImportDialogBox = lazy(() => import("../ImportDialogBox").then(module => ({ default: module.ImportDialogBox })));
const AnalysisPasteDialog = lazy(() => import("../AnalysisPasteDialog").then(module => ({ default: module.AnalysisPasteDialog })));
const CardEditor = lazy(() => import("./card_editor"));
const RepertoireView = lazy(() => import("./repertoire_view"));
const SettingsView = lazy(() => import("./settings_view"));
const StudiesView = lazy(() => import("./studies_view"));
const IntegrityRepairStatus = lazy(() => import("../components/integrity-repair-status").then(module => ({ default: module.IntegrityRepairStatus })));
const RepertoireIntegrityDialog = lazy(() => import("../components/repertoire-integrity-dialog").then(module => ({ default: module.RepertoireIntegrityDialog })));
const ServiceStatusPanel = lazy(() => import("../components/service-status-panel").then(module => ({ default: module.ServiceStatusPanel })));
const PendingDiscoverySaves = lazy(() => import("../components/pending-discovery-saves").then(module => ({ default: module.PendingDiscoverySaves })));
import { TreeBrowser } from "./tree_browser";

export function prepareLegacyWorkspaceNavigation(view: View | "analysis"): View {
  return view === "analysis" || view === "builder" || view === "compare" || view === "games" || view === "insights" || view === "progress" || view === "statistics"
    ? "train"
    : view;
}

/** Full workspace owner for authoring and integrity repair. */
export default function LegacyWorkspace(props: LegacyWorkspaceProps) {
  const { currentView, changeWorkspace, boardTheme, pieceSet, soundOn, changeBoardTheme,
    changePieceSet, changeSound, studyOnlyMode, incrementSafeBreak,
    reviewed, cardsLeft, totalCards,
    refreshDatabaseQueue, refreshQueueOnly, invalidateBranchEvidence,
    refreshQueueAfterRepertoireChange, refreshDatabaseQueueAfterRepertoireChange,
    addImportedRepertoire, renameLocalRepertoire, deleteLocalRepertoire,
    onCardSaved, finishPermanentDeletion } = props;
  const { importedRepertoires, showImport, showTree, editorCard,
    setShowImport, setShowTree, setEditorCard } = useTrainingStore(useShallow(state => ({
      importedRepertoires: state.importedRepertoires, showImport: state.showImport,
      showTree: state.showTree, editorCard: state.editorCard,
      setShowImport: state.setShowImport, setShowTree: state.setShowTree, setEditorCard: state.setEditorCard,
    })));
  const databaseQueue = useTrainingStore(state => state.isDatabaseQueueActive);
  const offlineQueue = useTrainingStore(state => state.isOfflineQueueActive);
  const [deletionRecoveryError, setDeletionRecoveryError] = useState("");
  const deletionRecoveryStarted = useRef(false);
  const recoverCardDeletion = useCallback(async () => {
    if (independentStudy) return;
    try {
      const pending = pendingCardDeletion();
      if (!pending) { setDeletionRecoveryError(""); return; }
      await deleteCardCommand(pending.cardId, pending.expectedRevision);
      await finishPermanentDeletion(pending.cardId);
      if (useTrainingStore.getState().editorCard?.backendId === pending.cardId) setEditorCard(null);
      setDeletionRecoveryError("");
    } catch (failure) { setDeletionRecoveryError(failure instanceof Error ? failure.message : "Could not confirm the earlier card deletion."); }
  }, [finishPermanentDeletion, setEditorCard]);
  useEffect(() => {
    if (independentStudy || !databaseQueue || offlineQueue || deletionRecoveryStarted.current) return;
    deletionRecoveryStarted.current = true;
    void recoverCardDeletion();
  }, [databaseQueue, offlineQueue, recoverCardDeletion]);
  const [repairRepertoireId, setRepairRepertoireId] = useState<string>();
  const [pasteContext, setPasteContext] = useState<AnalysisPasteContext | null>(
    null,
  );
  const [pasteRevision, setPasteRevision] = useState(0);
  const [pausedIntegrity, setPausedIntegrity] = useState<{
    id: string;
    issueCount: number;
    blockedDue: number;
  }>();
  const deferredRepairIds = useRef(new Set<string>());
  const nextIntegrityRequestSequence = useRef(0);
  const lastAcceptedIntegritySnapshotSequence = useRef(0);
  const pendingPreferredRepairIntent = useRef<{ repertoireId: string; minimumRequestSequence: number } | undefined>(undefined);
  const openRepairRepertoireId = useRef<string | undefined>(undefined);
  // Update dialog intent before React commits so an in-flight read observes direct actions.
  const openRepairDialog = useCallback((repertoireId: string) => {
    pendingPreferredRepairIntent.current = undefined;
    deferredRepairIds.current.delete(repertoireId);
    openRepairRepertoireId.current = repertoireId;
    setRepairRepertoireId(repertoireId);
  }, []);
  const deferRepairDialog = useCallback(() => {
    pendingPreferredRepairIntent.current = undefined;
    const currentRepairRepertoireId = openRepairRepertoireId.current;
    if (currentRepairRepertoireId) deferredRepairIds.current.add(currentRepairRepertoireId);
    openRepairRepertoireId.current = undefined;
    setRepairRepertoireId(undefined);
  }, []);
  const checkPendingIntegrity = useCallback(async (preferred?: string, passive = false) => {
    if (!usesLocalApi() || independentStudy) return;
    const integrityRequestSequence = ++nextIntegrityRequestSequence.current;
    if (preferred && !passive) {
      deferredRepairIds.current.delete(preferred);
      pendingPreferredRepairIntent.current = { repertoireId: preferred, minimumRequestSequence: integrityRequestSequence };
    }
    try {
      const response = await (passive ? backgroundFetch : fetch)(`${API_URL}/api/repertoires`);
      if (!response.ok) return;
      const data = await response.json();
      const parsed = repertoiresResponseSchema.parse(data);
      // Only a validated newer snapshot fences an older response; a started or failed read has no authority.
      if (integrityRequestSequence <= lastAcceptedIntegritySnapshotSequence.current) return;
      lastAcceptedIntegritySnapshotSequence.current = integrityRequestSequence;
      const candidate = parsed.repertoires.find(
        (item) =>
          item.integrity_status === "needs_repair" &&
          !deferredRepairIds.current.has(item.id),
      );
      const currentPreferredRepairIntent = pendingPreferredRepairIntent.current;
      const preferredRepairIntent = currentPreferredRepairIntent
        && integrityRequestSequence >= currentPreferredRepairIntent.minimumRequestSequence
        ? currentPreferredRepairIntent : undefined;
      const preferredCandidate = preferredRepairIntent
        ? parsed.repertoires.find(
            (item) =>
              item.id === preferredRepairIntent.repertoireId && item.integrity_status === "needs_repair",
          )
        : undefined;
      // Accepted reads settle current intent only when they began with or after that intent.
      if (preferredRepairIntent) {
        pendingPreferredRepairIntent.current = undefined;
        if (preferredCandidate && !openRepairRepertoireId.current) openRepairDialog(preferredCandidate.id);
      }
      const paused = preferredCandidate ?? candidate;
      const repairItems = parsed.repertoires.filter(
        (item) => item.integrity_status === "needs_repair",
      );
      setPausedIntegrity(
        paused
          ? {
              id: paused.id,
              issueCount: repairItems.reduce(
                (total, item) => total + (item.integrity_issue_count ?? 0),
                0,
              ),
              blockedDue: repairItems.reduce(
                (total, item) => total + (item.blocked_due_count ?? 0),
                0,
              ),
            }
          : undefined,
      );
    } catch {
      // The normal workspace refresh path reports the service error.
    }
  }, [openRepairDialog]);

  useEffect(() => {
    const onIntegrity = (event: Event) => {
      const detail = (event as CustomEvent<{ repertoireId?: string }>).detail;
      if (detail?.repertoireId) {
        void checkPendingIntegrity(detail.repertoireId);
      } else void checkPendingIntegrity();
    };
    window.addEventListener("tempo:integrity", onIntegrity);
    queueMicrotask(() => void checkPendingIntegrity());
    return () => window.removeEventListener("tempo:integrity", onIntegrity);
  }, [checkPendingIntegrity]);

  useImperativeHandle(props.controllerRef, () => ({
    checkIntegrity: checkPendingIntegrity,
    deferRepair: deferRepairDialog,
    prepareNavigation(view) {
      return prepareLegacyWorkspaceNavigation(view);
    },
  }), [checkPendingIntegrity, deferRepairDialog]);
  return props.children({
    topActions: <>
          {<WorkbenchLaunch />}
          {<SavedLocallyButton setShowImport={setShowImport} />}
          {<ServiceStatusPanel studyOnlyMode={studyOnlyMode} />}
          {<PendingDiscoverySaves
            interactionBlocked={Boolean(
              editorCard || showImport || pasteContext || repairRepertoireId,
            )}
            onQueueChanged={() => refreshDatabaseQueue()}
          />}
</>,
    trainingNotice: <>
      {deletionRecoveryError && <div className="ui-notice error" role="alert">{deletionRecoveryError} <Button onClick={() => void recoverCardDeletion()}>Check card deletion</Button></div>}
    </>,
    repairNotice: pausedIntegrity && <TrainingRepairNotice
                blockedDue={pausedIntegrity.blockedDue}
                issueCount={pausedIntegrity.issueCount}
                onResume={() => openRepairDialog(pausedIntegrity.id)}
              />,
    content: <>
        {currentView === "repertoire" && (
          <RepertoireAuditMount apiUrl={API_URL} request={fetch} onNavigate={intent => {
            if (intent.kind === "import") setShowImport(true);
            else if (intent.kind === "settings") changeWorkspace("settings");
            else if (intent.kind === "manual_edit") setShowTree(true);
          }}>
          <RepertoireView
            theme={boardTheme}
            pieceSet={pieceSet}
            imported={importedRepertoires}
            refreshRevision={pasteRevision}
            onImport={() => setShowImport(true)}
            onPaste={() => setPasteContext({})}
            onRepair={openRepairDialog}
            onDeleteLocal={deleteLocalRepertoire}
            onRenameLocal={renameLocalRepertoire}
            onQueueChanged={refreshDatabaseQueueAfterRepertoireChange}
          />
          </RepertoireAuditMount>
        )}
        {currentView === "studies" && (
          <StudiesView boardTheme={boardTheme} pieceSet={pieceSet} />
        )}
        {currentView === "settings" && (
          <SettingsView
            theme={boardTheme}
            pieceSet={pieceSet}
            sound={soundOn}
            onTheme={changeBoardTheme}
            onPieces={changePieceSet}
            onSound={changeSound}
          />
        )}
</>,
    dialogs: <>
      {showImport && (
        <ImportDialogBox
          onClose={() => {
            setShowImport(false);
            incrementSafeBreak();
          }}
          onImported={addImportedRepertoire}
          onDatabaseUpdated={refreshQueueAfterRepertoireChange}
          onViewRepertoire={() => changeWorkspace("repertoire")}
        />
      )}
      {pasteContext && (
        <AnalysisPasteDialog
          context={pasteContext}
          onClose={() => {
            setPasteContext(null);
            incrementSafeBreak();
          }}
          onSaved={(affectedRepertoireIds, conflictingRepertoireIds) => {
            invalidateBranchEvidence();
            setPasteRevision((revision) => revision + 1);
            void refreshQueueOnly();
            setPasteContext(null);
            if (conflictingRepertoireIds.length)
              openRepairDialog(conflictingRepertoireIds[0]);
            for (const repertoireId of affectedRepertoireIds) {
              window.dispatchEvent(
                new CustomEvent("tempo:integrity", {
                  detail: { repertoireId },
                }),
              );
            }
          }}
        />
      )}
      {showTree && (
        <TreeBrowser
          onClose={() => setShowTree(false)}
          theme={boardTheme}
          pieceSet={pieceSet}
        />
      )}
      {editorCard && (
        <CardEditor
          practiceCard={editorCard}
          boardTheme={boardTheme}
          pieceSet={pieceSet}
          onClose={() => {
            setEditorCard(null);
            incrementSafeBreak();
          }}
          onSave={onCardSaved}
          onDelete={async deleted => {
            if (!deleted.backendId) throw new Error("The card has no database identity. Refresh training.");
            await finishPermanentDeletion(deleted.backendId);
            setEditorCard(null);
          }}
        />
      )}
      {<IntegrityRepairStatus
        onReconcile={(repertoireId) => {
          invalidateBranchEvidence();
          deferredRepairIds.current.delete(repertoireId);
          invalidateWorkspaceData();
          invalidateTrainingQueueCache();
          void checkPendingIntegrity(undefined, true);
          const currentTrainingState = useTrainingStore.getState();
          const hasPlayableTrainingCard = currentTrainingState.cardsLeft > 0
            && Boolean(currentTrainingState.practiceCards[currentTrainingState.activeCardIndex]);
          if (!hasPlayableTrainingCard) void refreshDatabaseQueue(false).catch(() => undefined);
        }}
        onResume={openRepairDialog}
      />}
      {repairRepertoireId && (
        <RepertoireIntegrityDialog
          repertoireId={repairRepertoireId}
          theme={boardTheme}
          pieceSet={pieceSet}
          onClose={deferRepairDialog}
        />
      )}
</>,
    onEditCard: setEditorCard,
  });
}
