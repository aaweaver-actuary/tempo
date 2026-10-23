import { ActionMenu, Surface } from "../components/ui";
import { Button } from "../components/buttons/BaseButton";
import { useState, useCallback, useEffect } from "react";
import {
  LocalRepertoire,
  PieceColor,
  RepertoireItem,
  asRepertoireId,
} from "../types";
import { usesLocalApi } from "../utils/local";
import { API_URL } from "../const";
import { renameRepertoireCommand } from "../lib/repertoire-rename-command";
import { deleteRepertoireCommand, pendingRepertoireDeletion, type LearnedCardsPolicy } from "../lib/repertoire-delete-command";
import { RepertoireDeleteDialog } from "../components/repertoire-delete-dialog";
import {
  readWorkspaceResponse,
  invalidateWorkspaceData,
} from "../lib/workspace-data";
import { reportDebugError } from "../lib/debug-reporting";
import { PrefixDiagnostics } from "../components/prefix-diagnostics";
import { PrefixComparisonDialog } from "../components/prefix-comparison-dialog";
import { OpeningSegmentation } from "../components/opening-segmentation";
import { CanonicalPrefixDialog } from "../components/canonical-prefix-dialog";
import { canonicalPrefixSchema, type CanonicalPrefix } from "../domain/canonical-prefix";
import type { BoardTheme, PieceSet } from "../components/chessboard";

import { Notice } from "../components/task-tabs";

export default function RepertoireView({
  imported,
  refreshRevision = 0,
  onImport,
  onPaste,
  onRepair,
  onDeleteLocal,
  onRenameLocal,
  onQueueChanged,
  theme = "brown",
  pieceSet = "cburnett",
}: {
  imported: LocalRepertoire[];
  refreshRevision?: number;
  onImport: () => void;
  onPaste?: () => void;
  onRepair: (id: string) => void;
  onDeleteLocal: (id: string) => void;
  onRenameLocal: (id: string, name: string) => void;
  onQueueChanged: () => Promise<void>;
  theme?: BoardTheme;
  pieceSet?: PieceSet;
}) {
  const [diagnosticsRepertoireId, setDiagnosticsRepertoireId] = useState<string | null>(null);
  const [segmentationRepertoireId, setSegmentationRepertoireId] = useState<string | null>(null);
  const [comparisonRepertoireId, setComparisonRepertoireId] = useState<string | null>(null);
  const [prefixRepertoireId, setPrefixRepertoireId] = useState<string | null>(null);
  const [backendItems, setBackendItems] = useState<RepertoireItem[]>([]);
  const [loaded, setLoaded] = useState(!usesLocalApi());
  const [libraryPage, setLibraryPage] = useState(0);
  const [error, setError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<RepertoireItem | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [pendingDeletePolicy, setPendingDeletePolicy] = useState<LearnedCardsPolicy>();
  const [openMenu, setOpenMenu] = useState<string | null>(null);

  const loadBackend = useCallback(async () => {
    if (!usesLocalApi()) return true;
    try {
      const response = await readWorkspaceResponse(
        `${API_URL}/api/repertoires`,
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = (await response.json()) as {
        repertoires: {
          id: string;
          name: string;
          source_name: string;
          line_count: number;
          card_count: number;
          active_prefix_count?: number;
          graph_updated_at?: string | null;
          due_count: number;
          blocked_due_count?: number;
          blocked_card_count?: number;
          conflict_count?: number;
          integrity_status?: "unchecked" | "clean" | "needs_repair";
          integrity_issue_count?: number;
          trained_color?: PieceColor;
          canonical_prefix?: CanonicalPrefix;
        }[];
      };
      setBackendItems(
        body.repertoires.map((item) => {
          return {
            id: asRepertoireId(item.id),
            side: item.trained_color === "black" ? "black" : "white",
            title: item.name,
            sourceName: item.source_name,
            detail: item.id === "__retained_cards__" ? `${item.card_count} kept cards · original schedules and history` : `${item.line_count} unique ${item.line_count === 1 ? "line" : "lines"} · ${item.card_count} cards · ${item.graph_updated_at ? `${item.active_prefix_count ?? 0} active prefixes` : "active prefixes awaiting graph"}`,
            progress: 0,
            due: item.due_count,
            blockedDueCount: item.blocked_due_count ?? 0,
            blockedCardCount: item.blocked_card_count ?? 0,
            conflictCount: item.conflict_count ?? 0,
            backend: true,
            integrityStatus: item.integrity_status,
            integrityIssueCount: item.integrity_issue_count ?? 0,
            canonicalPrefix: item.canonical_prefix ? canonicalPrefixSchema.parse(item.canonical_prefix) : undefined,
          };
        }),
      );
      setLoaded(true);
      setError("");
      try {
        const pending = pendingRepertoireDeletion();
        if (pending) {
          setPendingDeletePolicy(pending.learnedCards);
          setDeleteTarget(current => current ?? {
            id: asRepertoireId(pending.repertoireId), title: body.repertoires.find(item => item.id === pending.repertoireId)?.name ?? "Pending repertoire",
            side: "white", detail: "", sourceName: "", progress: 0, due: 0, backend: true,
          });
        }
      } catch (failure) {
        reportDebugError(failure, {
          kind: "ui", source: "repertoire-delete-recovery", operation: "load pending repertoire deletion",
        });
        setError(`Pending repertoire deletion unavailable: ${failure instanceof Error ? failure.message : "recovery record unreadable"}. Check browser recovery data before retrying deletion.`);
      }
      return true;
    } catch (failure) {
      reportDebugError(failure, {
        kind: "api",
        source: "repertoire-view",
        operation: "load repertoires",
        endpoint: `${API_URL}/api/repertoires`,
      });
      setError(
        `Repertoires unavailable: ${failure instanceof Error ? failure.message : "connection failed"}.`,
      );
      return false;
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadBackend();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadBackend, refreshRevision]);
  const backendSources = new Set(backendItems.map((item) => item.sourceName));
  const importedItems: RepertoireItem[] = (
    usesLocalApi()
      ? []
      : imported.filter((item) => !backendSources.has(item.sourceName))
  ).map((item) => ({
    id: item.id,
    side: item.side,
    title: item.title,
    sourceName: item.sourceName,
    detail: `${item.cards.length} unique ${item.cards.length === 1 ? "line" : "lines"} · stored in this browser`,
    progress: 0,
    due: 0,
    pgn: item.pgn,
  }));
  const repertoires = [...backendItems, ...importedItems];

  async function rename(item: RepertoireItem) {
    const value = window.prompt("Repertoire nickname", item.title)?.trim();
    if (!value) return;
    if (item.backend) {
      try {
        await renameRepertoireCommand(item.id, value);
        invalidateWorkspaceData();
        await loadBackend();
      } catch (renameError) {
        setError(renameError instanceof Error ? renameError.message : "Could not rename this repertoire");
      }
    } else onRenameLocal(item.id, value);
  }

  async function remove(item: RepertoireItem, learnedCards: LearnedCardsPolicy) {
    setDeleteBusy(true);
    setDeleteError("");
    if (item.backend) {
      try {
        await deleteRepertoireCommand(item.id, learnedCards);
      } catch (deleteError) {
        setDeleteError(deleteError instanceof Error ? deleteError.message : "Could not delete this repertoire");
        try { setPendingDeletePolicy(pendingRepertoireDeletion()?.learnedCards); } catch { /* The validation error is displayed above. */ }
        setDeleteBusy(false);
        return;
      }
      invalidateWorkspaceData();
      onDeleteLocal(item.id);
      await loadBackend();
      let queueRefreshFailed = false;
      try {
        await onQueueChanged();
      } catch {
        queueRefreshFailed = true;
      }
      if (queueRefreshFailed)
        setError("Repertoire deleted. Training queue refresh failed; retry loading the workspace.");
    } else onDeleteLocal(item.id);
    setDeleteTarget(null);
    setPendingDeletePolicy(undefined);
    setDeleteBusy(false);
  }

  function exportPgn(item?: RepertoireItem) {
    if ((item?.backend || !item) && usesLocalApi()) {
      const link = document.createElement("a");
      link.href = item
        ? `${API_URL}/api/repertoires/${item.id}/export.pgn`
        : `${API_URL}/api/repertoires/export.pgn`;
      link.click();
      return;
    }
    const text =
      item?.pgn ??
      repertoires
        .flatMap((entry) => (entry.pgn ? [entry.pgn] : []))
        .join("\n\n");
    const url = URL.createObjectURL(
      new Blob([text], { type: "application/x-chess-pgn" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = item ? `${item.title}.pgn` : "tempo-repertoires.pgn";
    link.click();
    URL.revokeObjectURL(url);
  }
  const diagnosticsRepertoire = backendItems.find(item => item.id === diagnosticsRepertoireId);
  if (diagnosticsRepertoire) return <PrefixDiagnostics key={diagnosticsRepertoire.id} repertoireId={diagnosticsRepertoire.id}
    onBack={() => setDiagnosticsRepertoireId(null)} />;
  const segmentationRepertoire = backendItems.find(item => item.id === segmentationRepertoireId);
  if (segmentationRepertoire) return <div>
    <Button onClick={() => setSegmentationRepertoireId(null)}>← Repertoires</Button>
    <h2>{segmentationRepertoire.title}</h2>
    <OpeningSegmentation repertoireId={segmentationRepertoire.id} theme={theme} pieceSet={pieceSet} initiallyOpened />
  </div>;
  return (
    <section className="library-page" id="repertoire">
      {deleteTarget && <RepertoireDeleteDialog key={deleteTarget.id} name={deleteTarget.title}
        localDemo={!deleteTarget.backend}
        pendingPolicy={pendingDeletePolicy} busy={deleteBusy} error={deleteError}
        onClose={() => setDeleteTarget(null)} onConfirm={policy => remove(deleteTarget, policy)} />}
      {backendItems.filter(item => item.id === comparisonRepertoireId).map(item => <PrefixComparisonDialog
        key={`${item.id}:${refreshRevision}`} repertoireId={item.id} repertoireName={item.title}
        onClose={() => setComparisonRepertoireId(null)} />)}
      {backendItems.filter(item => item.id === prefixRepertoireId).map(item => <CanonicalPrefixDialog key={item.id}
        repertoireId={item.id} repertoireName={item.title} side={item.side} theme={theme} pieceSet={pieceSet}
        onClose={() => setPrefixRepertoireId(null)} onSaved={async () => {
          invalidateWorkspaceData();
          if (!await loadBackend()) throw new Error("Saved prefix metadata could not be reloaded.");
          // The queue callback reports training failures independently of confirmed prefix metadata.
          void onQueueChanged().catch(() => undefined);
        }} />)}
      {error && (
        <Notice
          error
          onRetry={() => {
            invalidateWorkspaceData();
            void loadBackend();
          }}
        >
          {loaded ? "Showing previously loaded records. " : ""}
          {error}
        </Notice>
      )}
      {!loaded && !error && <Notice>Loading repertoires…</Notice>}
      <div className="page-heading compact">
        <h1>Repertoire</h1>
        <div className="heading-actions">
          <ActionMenu className="action-menu" label="More">
            <Button disabled={!loaded} onClick={() => exportPgn()}>
              ⇩ Export all PGN
            </Button>
          </ActionMenu>
          {onPaste && <Button onClick={onPaste}>Paste analysis</Button>}
          <Button
            variant="primary"
            className="primary-button"
            onClick={(event) => {
              event.currentTarget.focus();
              onImport();
            }}
          >
            ＋ Import PGN
          </Button>
        </div>
      </div>
      <div className="library-grid">
        {repertoires
          .slice(libraryPage * 50, (libraryPage + 1) * 50)
          .map((item) => (
            <Surface as="article" className="repertoire-card" key={item.id}>
              <div className="repertoire-top">
                <span className="side-badge">{item.side}</span>
                <span>{item.due ? `${item.due} due` : "Up to date"}</span>
              </div>
              <div className="mini-board" aria-hidden="true">
                {Array.from({ length: 16 }).map((_, index) => (
                  <i key={index} />
                ))}
              </div>
              <div className="repertoire-name">
                <h2>{item.title}</h2>
                {item.id !== "__retained_cards__" && <ActionMenu
                  className="card-menu"
                  label="•••"
                  summaryAriaLabel={`More actions for ${item.title}`}
                  menuRole="menu"
                  open={openMenu === item.id}
                  onToggle={(event) =>
                    setOpenMenu(event.currentTarget.open ? item.id : null)
                  }
                >
                  <Button role="menuitem" onClick={() => void rename(item)}>
                    Rename
                  </Button>
                  {item.backend && <Button role="menuitem" onClick={event => { event.currentTarget.closest("details")?.querySelector("summary")?.focus(); setComparisonRepertoireId(item.id); setOpenMenu(null); }}>Compare prefix depths</Button>}
                  {item.backend && <Button role="menuitem" onClick={() => { setDiagnosticsRepertoireId(item.id); setOpenMenu(null); }}>Prefix difficulty</Button>}
                  {item.backend && <Button role="menuitem" onClick={() => { setSegmentationRepertoireId(item.id); setOpenMenu(null); }}>Recommended segmentation</Button>}
                  {item.backend && <Button role="menuitem" onClick={() => { setPrefixRepertoireId(item.id); setOpenMenu(null); }}>Canonical prefix…</Button>}
                  <Button role="menuitem" onClick={() => exportPgn(item)}>
                    ⇩ Export PGN
                  </Button>
                  <Button
                    role="menuitem"
                    variant="danger"
                    className="delete-repertoire"
                    onClick={event => {
                      try {
                        const pending = pendingRepertoireDeletion();
                        if (pending && pending.repertoireId !== item.id) throw new Error("Confirm the earlier repertoire deletion before removing another repertoire.");
                        event.currentTarget.closest("details")?.querySelector("summary")?.focus();
                        setPendingDeletePolicy(pending?.learnedCards);
                        setDeleteError(""); setDeleteTarget(item); setOpenMenu(null);
                      } catch (failure) { setError(String(failure)); }
                    }}
                  >
                    Delete
                  </Button>
                </ActionMenu>}
              </div>
              <p>{item.detail}</p>
              {Boolean(item.canonicalPrefix?.moves_uci.length) && <p className="canonical-prefix-summary">
                <strong>Canonical prefix: {item.canonicalPrefix?.san}</strong><br />Discoveries start after this opening.
              </p>}
              {item.integrityStatus === "needs_repair" && (
                <>
                  <p className="warning-text">
                    {item.blockedDueCount ?? 0} due and{" "}
                    {item.blockedCardCount ?? 0} total opening cards paused ·{" "}
                    {item.integrityIssueCount ?? 0} integrity issues
                  </p>
                  <Button onClick={() => onRepair(item.id)}>
                    Resume repair
                  </Button>
                </>
              )}
              {Boolean(item.conflictCount) && (
                <p className="warning-text">
                  {item.conflictCount} trained-move{" "}
                  {item.conflictCount === 1 ? "conflict" : "conflicts"} to
                  resolve
                </p>
              )}
              <small className="source-name">{item.sourceName}</small>

            </Surface>
          ))}
        <Button
          className="new-repertoire-card"
          onClick={(event) => {
            event.currentTarget.focus();
            onImport();
          }}
        >
          <span>＋</span>
          <strong>Add a repertoire</strong>
          <small>PGN files stay on this computer</small>
        </Button>
      </div>
      {repertoires.length > 50 && (
        <div className="pagination" aria-label="Library pages">
          <Button
            disabled={libraryPage === 0}
            onClick={() => setLibraryPage((value) => value - 1)}
          >
            Previous
          </Button>
          <span>Page {libraryPage + 1}</span>
          <Button
            disabled={(libraryPage + 1) * 50 >= repertoires.length}
            onClick={() => setLibraryPage((value) => value + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </section>
  );
}
