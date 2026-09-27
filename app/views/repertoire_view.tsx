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
import { deleteRepertoireCommand } from "../lib/repertoire-delete-command";
import { applyOpportunityCommand } from "../lib/opportunity-command";
import {
  readWorkspaceResponse,
  invalidateWorkspaceData,
} from "../lib/workspace-data";
import { readJsonResponse } from "../lib/validated-data";
import {
  repertoireCoverageGapsSchema,
  repertoireCoverageSummarySchema,
  repertoireOpportunitiesSchema,
} from "../domain/schemas";
import type { z } from "zod";
import { reportDebugError } from "../lib/debug-reporting";
import { RepertoireStatistics } from "../components/repertoire-statistics";
import type { BoardTheme, PieceSet } from "../components/chessboard";

import { Notice } from "../components/task-tabs";

type CoverageSummary = z.infer<typeof repertoireCoverageSummarySchema>;
type CoverageGap = z.infer<typeof repertoireCoverageGapsSchema>["gaps"][number];
type RepertoireOpportunity = z.infer<
  typeof repertoireOpportunitiesSchema
>["opportunities"][number];
type GapTarget = Pick<
  CoverageGap,
  "gap_id" | "fen" | "move_uci" | "trained_color"
>;
type IntroductionPriorityStatus = {
  state: "fallback" | "partial" | "ready";
  personal_games: number;
  explorer: string;
  maia: string;
  updated_at: string | null;
  error: string | null;
};

export default function RepertoireView({
  imported,
  refreshRevision = 0,
  onImport,
  onPaste,
  onPasteGap,
  onBrowse,
  onResolveGap,
  onShowGamesAtPosition,
  onRepair,
  onDeleteLocal,
  onRenameLocal,
  onQueueChanged,
  onTrain,
  theme = "brown",
  pieceSet = "cburnett",
}: {
  imported: LocalRepertoire[];
  refreshRevision?: number;
  onImport: () => void;
  onPaste?: () => void;
  onPasteGap?: (repertoireId: string, gap: GapTarget) => void;
  onBrowse: (id: string) => void;
  onResolveGap: (repertoireId: string, gap: GapTarget) => void;
  onShowGamesAtPosition: (fen: string, repertoireId?: string) => void;
  onRepair: (id: string) => void;
  onDeleteLocal: (id: string) => void;
  onRenameLocal: (id: string, name: string) => void;
  onQueueChanged: () => Promise<void>;
  onTrain: () => void;
  theme?: BoardTheme;
  pieceSet?: PieceSet;
}) {
  const [backendItems, setBackendItems] = useState<RepertoireItem[]>([]);
  const [loaded, setLoaded] = useState(!usesLocalApi());
  const [libraryPage, setLibraryPage] = useState(0);
  const [error, setError] = useState("");
  const [coverageByRepertoire, setCoverageByRepertoire] = useState<
    Record<string, CoverageSummary>
  >({});
  const [gapsByRepertoire, setGapsByRepertoire] = useState<
    Record<string, CoverageGap[]>
  >({});
  const [opportunitiesByRepertoire, setOpportunitiesByRepertoire] = useState<
    Record<string, RepertoireOpportunity[]>
  >({});
  const [openOpportunities, setOpenOpportunities] = useState<string | null>(
    null,
  );
  const [scoutingRepertoire, setScoutingRepertoire] = useState<string | null>(
    null,
  );
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [statisticsRepertoireId, setStatisticsRepertoireId] = useState<string | null>(() =>
    typeof window === "undefined" ? null : sessionStorage.getItem("tempo-statistics-repertoire"));

  const loadBackend = useCallback(async () => {
    if (!usesLocalApi()) return;
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
          introduction_priority?: IntroductionPriorityStatus;
        }[];
      };
      setBackendItems(
        body.repertoires.map((item) => {
          const priority = item.introduction_priority;
          const priorityDetail = priority
            ? priority.error
              ? " · new-line priority fallback — refresh coverage to retry"
              : ` · new-line priority ${priority.state === "ready" ? "ready" : priority.state === "partial" ? "using available evidence" : "using structural fallback"}`
            : "";
          return {
            id: asRepertoireId(item.id),
            side: item.trained_color === "black" ? "black" : "white",
            title: item.name,
            sourceName: item.source_name,
            detail: `${item.line_count} unique ${item.line_count === 1 ? "line" : "lines"} · ${item.card_count} cards · ${item.graph_updated_at ? `${item.active_prefix_count ?? 0} active prefixes` : "active prefixes awaiting graph"}${priorityDetail}`,
            progress: 0,
            due: item.due_count,
            blockedDueCount: item.blocked_due_count ?? 0,
            blockedCardCount: item.blocked_card_count ?? 0,
            conflictCount: item.conflict_count ?? 0,
            backend: true,
            integrityStatus: item.integrity_status,
            integrityIssueCount: item.integrity_issue_count ?? 0,
          };
        }),
      );
      setLoaded(true);
      setError("");
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

  async function remove(item: RepertoireItem) {
    if (
      !window.confirm(
        `Delete “${item.title}”? Its cards and review history will also be removed.`,
      )
    )
      return;
    if (item.backend) {
      try {
        await deleteRepertoireCommand(item.id);
      } catch (deleteError) {
        setError(deleteError instanceof Error ? deleteError.message : "Could not delete this repertoire");
        return;
      }
      invalidateWorkspaceData();
      onDeleteLocal(item.id);
      try {
        await onQueueChanged();
        await loadBackend();
      } catch {
        setError("Repertoire deleted. Refresh failed; retry loading the workspace.");
      }
    } else onDeleteLocal(item.id);
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
  const loadCoverage = useCallback(async (repertoireId: string) => {
    const [summaryResponse, gapsResponse] = await Promise.all([
      fetch(`${API_URL}/api/repertoires/${repertoireId}/coverage`),
      fetch(`${API_URL}/api/repertoires/${repertoireId}/coverage/gaps`),
    ]);
    const summary = await readJsonResponse(
      summaryResponse,
      repertoireCoverageSummarySchema,
      "repertoire coverage",
    );
    const gaps = await readJsonResponse(
      gapsResponse,
      repertoireCoverageGapsSchema,
      "repertoire coverage gaps",
    );
    setCoverageByRepertoire((current) => ({
      ...current,
      [repertoireId]: summary,
    }));
    setGapsByRepertoire((current) => ({
      ...current,
      [repertoireId]: gaps.gaps,
    }));
  }, []);
  useEffect(() => {
    if (!refreshRevision) return;
    const timer = window.setTimeout(() => {
      for (const repertoireId of Object.keys(coverageByRepertoire)) {
        void loadCoverage(repertoireId).catch((failure) =>
          setError(
            failure instanceof Error
              ? failure.message
              : "Coverage unavailable.",
          ),
        );
      }
    }, 0);
    return () => window.clearTimeout(timer);
    // The revision is the event; rereading the existing coverage panels is intentional.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshRevision, loadCoverage]);
  async function refreshCoverage(repertoireId: string) {
    const response = await fetch(
      `${API_URL}/api/repertoires/${repertoireId}/coverage/refresh`,
      { method: "POST" },
    );
    if (!response.ok) throw new Error("Could not queue repertoire coverage.");
    await loadCoverage(repertoireId);
  }
  async function loadOpportunities(repertoireId: string) {
    const response = await fetch(
      `${API_URL}/api/repertoires/${repertoireId}/opportunities`,
    );
    const result = await readJsonResponse(
      response,
      repertoireOpportunitiesSchema,
      "repertoire opportunities",
    );
    setOpportunitiesByRepertoire((current) => ({
      ...current,
      [repertoireId]: result.opportunities,
    }));
    setScoutingRepertoire(null);
    setOpenOpportunities(repertoireId);
  }
  async function dismissOpportunity(
    repertoireId: string,
    opportunityId: string,
  ) {
    await applyOpportunityCommand(repertoireId, opportunityId, "dismiss");
    await loadOpportunities(repertoireId);
  }
  async function trainOpportunity(repertoireId: string, opportunityId: string) {
    const response = await fetch(
      `${API_URL}/api/repertoires/${repertoireId}/opportunities/${opportunityId}/train`,
      { method: "POST" },
    );
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as {
        detail?: string;
      };
      throw new Error(
        body.detail ?? `Could not queue decision (HTTP ${response.status}).`,
      );
    }
    await onQueueChanged();
    await loadOpportunities(repertoireId);
    onTrain();
  }
  async function refreshOpportunities(repertoireId: string) {
    const response = await fetch(
      `${API_URL}/api/repertoires/${repertoireId}/opportunities/refresh`,
      { method: "POST" },
    );
    if (!response.ok)
      throw new Error(
        `Could not queue repertoire scouting (HTTP ${response.status}).`,
      );
    setScoutingRepertoire(repertoireId);
  }
  const selectedStatisticsRepertoire = backendItems.find((item) => item.id === statisticsRepertoireId);
  if (selectedStatisticsRepertoire) {
    return <RepertoireStatistics repertoireId={selectedStatisticsRepertoire.id}
      repertoireName={selectedStatisticsRepertoire.title} theme={theme} pieceSet={pieceSet}
      onBack={() => { sessionStorage.removeItem("tempo-statistics-repertoire"); setStatisticsRepertoireId(null); }}
      onShowGamesAtPosition={onShowGamesAtPosition} />;
  }
  return (
    <section className="library-page" id="repertoire">
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
                <ActionMenu
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
                  <Button role="menuitem" onClick={() => exportPgn(item)}>
                    ⇩ Export PGN
                  </Button>
                  <Button
                    role="menuitem"
                    variant="danger"
                    className="delete-repertoire"
                    onClick={() => void remove(item)}
                  >
                    Delete
                  </Button>
                </ActionMenu>
              </div>
              <p>{item.detail}</p>
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
              {item.backend && coverageByRepertoire[item.id] && (
                <div className="coverage-summary">
                  <div>
                    <span>Required replies</span>
                    <strong>
                      {coverageByRepertoire[item.id].covered_branches} /{" "}
                      {coverageByRepertoire[item.id].required_branches}
                    </strong>
                  </div>
                  <div>
                    <span>Probability coverage</span>
                    <strong>
                      {coverageByRepertoire[item.id].probability_coverage ===
                      null
                        ? "—"
                        : `${Math.round(coverageByRepertoire[item.id].probability_coverage! * 1000) / 10}%`}
                    </strong>
                  </div>
                  <small>
                    {coverageByRepertoire[item.id].status}
                    {coverageByRepertoire[item.id].unknown_nodes
                      ? ` · ${coverageByRepertoire[item.id].unknown_nodes} positions awaiting data`
                      : ""}
                  </small>
                  {gapsByRepertoire[item.id]?.slice(0, 3).map((gap) => (
                    <span key={gap.gap_id}>
                      <Button onClick={() => onResolveGap(item.id, gap)}>
                        Fill {gap.move_uci} gap ·{" "}
                        {gap.probability === null
                          ? "unknown"
                          : `${Math.round(gap.probability * 1000) / 10}%`}
                      </Button>
                      {onPasteGap && (
                        <Button onClick={() => onPasteGap(item.id, gap)}>
                          Paste line for gap
                        </Button>
                      )}
                    </span>
                  ))}
                </div>
              )}
              {item.backend && openOpportunities === item.id && (
                <div
                  className="opportunities-panel"
                  aria-label="Repertoire opportunities"
                >
                  <strong>Opportunities</strong>
                  <Button
                    onClick={() =>
                      void refreshOpportunities(item.id).catch((failure) =>
                        setError(
                          failure instanceof Error
                            ? failure.message
                            : "Could not queue scouting.",
                        ),
                      )
                    }
                  >
                    Refresh scouting
                  </Button>
                  {scoutingRepertoire === item.id && (
                    <small>
                      Scouting queued in Background activity. Reopen
                      Opportunities to see new results.
                    </small>
                  )}
                  {opportunitiesByRepertoire[item.id]?.length === 0 && (
                    <p>
                      No current opportunities. Refresh scouting to check
                      existing evidence.
                    </p>
                  )}
                  {opportunitiesByRepertoire[item.id]?.map((opportunity) => {
                    const evidence = opportunity.evidence;
                    const cohort =
                      evidence.cohort && typeof evidence.cohort === "object"
                        ? (evidence.cohort as Record<string, unknown>)
                        : {};
                    const count = (key: string) =>
                      typeof evidence[key] === "number"
                        ? String(evidence[key])
                        : "—";
                    const percentage = (key: string) =>
                      typeof evidence[key] === "number"
                        ? `${Math.round(Number(evidence[key]) * 1000) / 10}%`
                        : "unavailable";
                    return (
                      <div key={opportunity.id} className="opportunity-item">
                        <strong>
                          {opportunity.evidence.analysis_based
                            ? "Recurring weak decision"
                            : opportunity.kind === "weak_known_decision"
                              ? "Weak known decision"
                              : opportunity.kind === "missing_response"
                                ? `Missing response: ${opportunity.opponent_move_uci}`
                                : `Post-gap weakness: ${opportunity.opponent_move_uci}`}
                        </strong>
                        <small>Position: {opportunity.fen_key}</small>
                        {opportunity.evidence.analysis_based ? (
                          <p>
                            Past {count("window_days")} days:{" "}
                            {count("encounter_count")} encounters,{" "}
                            {count("miss_count")} engine-confirmed mistakes in{" "}
                            {count("analyzed_count")} analyzed decisions.
                            Immediate loss {count("immediate_average_loss_cp")}{" "}
                            cp; observed change through your third later turn{" "}
                            {count("later_average_change_cp")} cp across{" "}
                            {count("later_sample_count")} complete games.
                          </p>
                        ) : opportunity.kind === "weak_known_decision" ? (
                          <p>
                            Reached {count("encounter_count")} times · correct{" "}
                            {count("success_count")} · missed{" "}
                            {count("miss_count")} · successful route{" "}
                            {count("route_success_count")} times. Priority
                            introduction within your daily limit.
                          </p>
                        ) : opportunity.kind === "missing_response" ? (
                          <p>
                            Maia {String(cohort.maia_elo ?? "cohort")}{" "}
                            {percentage("maia_probability")} (
                            {String(evidence.maia_status ?? "unknown")}) ·
                            Lichess {String(cohort.explorer_rating ?? "cohort")}{" "}
                            {percentage("explorer_probability")} (
                            {String(evidence.explorer_status ?? "unknown")},{" "}
                            {count("explorer_games")} games) · your games{" "}
                            {count("personal_count")}. Coverage missing.
                          </p>
                        ) : (
                          <p>
                            {count("supporting_games")} supporting games ·
                            largest following mistake {count("max_loss_cp")} cp
                            {opportunity.card_id
                              ? " · matching existing card"
                              : ""}
                            .
                          </p>
                        )}
                        {opportunity.card_id ? (
                          <Button
                            disabled={opportunity.admission_state === "queued"}
                            onClick={() =>
                              void trainOpportunity(
                                item.id,
                                opportunity.id,
                              ).catch((failure) =>
                                setError(
                                  failure instanceof Error
                                    ? failure.message
                                    : "Could not queue decision.",
                                ),
                              )
                            }
                          >
                            {opportunity.admission_state === "queued"
                              ? "In training queue"
                              : "Train this decision"}
                          </Button>
                        ) : (
                          opportunity.opponent_move_uci && (
                            <Button
                              onClick={() =>
                                onResolveGap(item.id, {
                                  gap_id:
                                    typeof evidence.coverage_node_id ===
                                    "string"
                                      ? `${evidence.coverage_node_id}:${opportunity.opponent_move_uci}`
                                      : "",
                                  fen: opportunity.fen,
                                  move_uci: opportunity.opponent_move_uci!,
                                  trained_color: opportunity.trained_color,
                                })
                              }
                            >
                              Investigate branch
                            </Button>
                          )
                        )}
                        {opportunity.kind === "post_gap_weakness" && (
                          <>
                            <Button
                              onClick={() =>
                                onShowGamesAtPosition(opportunity.fen, item.id)
                              }
                            >
                              View supporting games
                            </Button>
                            {Array.isArray(evidence.findings) &&
                              evidence.findings.length > 0 && (
                                <details>
                                  <summary>Engine and game evidence</summary>
                                  <ul>
                                    {evidence.findings
                                      .slice(0, 5)
                                      .map((finding, index) => {
                                        const support =
                                          finding && typeof finding === "object"
                                            ? (finding as Record<
                                                string,
                                                unknown
                                              >)
                                            : {};
                                        return (
                                          <li
                                            key={String(
                                              support.finding_id ?? index,
                                            )}
                                          >
                                            Game{" "}
                                            {String(
                                              support.game_id ?? "unknown",
                                            )}{" "}
                                            · ply{" "}
                                            {String(
                                              support.mistake_ply ?? "unknown",
                                            )}{" "}
                                            · loss{" "}
                                            {String(
                                              support.mistake_loss_cp ??
                                                "unknown",
                                            )}{" "}
                                            cp · analysis{" "}
                                            {String(
                                              support.analysis_version ??
                                                "unknown",
                                            )}
                                          </li>
                                        );
                                      })}
                                  </ul>
                                </details>
                              )}
                          </>
                        )}
                        <Button
                          onClick={() =>
                            void dismissOpportunity(
                              item.id,
                              opportunity.id,
                            ).catch((failure) =>
                              setError(
                                failure instanceof Error
                                  ? failure.message
                                  : "Could not dismiss opportunity.",
                              ),
                            )
                          }
                        >
                          Dismiss
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="repertoire-actions">
                <Button
                  className="browse-button"
                  onClick={() => onBrowse(item.id)}
                >
                  Browse tree
                </Button>
                {item.backend && (
                  <Button onClick={() => {
                    sessionStorage.setItem("tempo-statistics-repertoire", item.id);
                    setStatisticsRepertoireId(item.id);
                  }}>Statistics</Button>
                )}
                {item.backend && (
                  <Button
                    onClick={() =>
                      void (
                        coverageByRepertoire[item.id]
                          ? refreshCoverage(item.id)
                          : loadCoverage(item.id)
                      ).catch((failure) => {
                        reportDebugError(failure, {
                          kind: "api",
                          source: "repertoire-coverage",
                          operation: "load repertoire coverage",
                          endpoint: `${API_URL}/api/repertoires/${item.id}/coverage`,
                        });
                        setError(
                          failure instanceof Error
                            ? failure.message
                            : "Coverage unavailable",
                        );
                      })
                    }
                  >
                    {coverageByRepertoire[item.id]
                      ? "Refresh coverage"
                      : "Check coverage"}
                  </Button>
                )}
                {item.backend && (
                  <Button
                    onClick={() =>
                      void loadOpportunities(item.id).catch((failure) =>
                        setError(
                          failure instanceof Error
                            ? failure.message
                            : "Opportunities unavailable.",
                        ),
                      )
                    }
                  >
                    Opportunities
                  </Button>
                )}
              </div>
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
