import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { candidateDepths, exactRouteLines, routeMovetext, structuralMetricLabels,
  type PrefixSource, type PrefixComparison, type RoutePrefix } from "../domain/prefix-comparison";
import { comparePrefixDepth, PrefixPreviewError, readPrefixSource } from "../lib/prefix-comparison";
import { useDialogFocus } from "../hooks/use-dialog-focus";
import { Button } from "./buttons/BaseButton";
import CloseButton from "./buttons/CloseButton";

type CandidateResult = { depth: number; comparison: PrefixComparison };
const errorLabels: Record<string, string> = {
  stale_snapshot: "Stale preview", unsupported_source: "Unsupported source", unsupported_backend: "Unsupported backend",
  graph_not_ready: "Graph not ready", evaluation_busy: "Study work is active", limit_exceeded: "Preview limit exceeded",
  invalid_selection: "Invalid selection", service_error: "Service unavailable",
};
const signed = (value: number) => value > 0 ? `+${value}` : String(value);

function ScopeResults({ comparison, title }: { comparison: PrefixComparison["selected"]; title: string }) {
  const [identitiesOpened, setIdentitiesOpened] = useState(false);
  const [identityPages, setIdentityPages] = useState({ added_card_ids: 0, removed_card_ids: 0, unchanged_card_ids: 0 });
  return <section className="prefix-scope-results" aria-label={title}>
    <h4>{title}</h4>
    <table><thead><tr><th>Metric</th><th>Saved</th><th>Candidate</th><th>Delta</th></tr></thead>
      <tbody>{Object.entries(structuralMetricLabels).map(([key, label]) => {
        const metric = key as keyof typeof structuralMetricLabels;
        return <tr key={key}><th scope="row">{label}</th><td>{comparison.current.metrics[metric]}</td>
          <td>{comparison.proposed.metrics[metric]}</td><td>{signed(comparison.delta[metric])}</td></tr>;
      })}</tbody></table>
    <p>Cards added: {comparison.added_card_ids.length} · removed: {comparison.removed_card_ids.length} · unchanged: {comparison.unchanged_card_ids.length}</p>
    <p>Additional board starts: {comparison.additional_starts} · reduced starts: {comparison.reduced_starts}</p>
    <details onToggle={event => setIdentitiesOpened(event.currentTarget.open)}><summary>Card identities</summary>{identitiesOpened && (["added_card_ids", "removed_card_ids", "unchanged_card_ids"] as const).map(kind => {
      const firstIdentity = identityPages[kind] * 50;
      return <div key={kind}><strong>{kind.replace("_card_ids", "")}</strong>
        <p>Showing {comparison[kind].length ? firstIdentity + 1 : 0}–{Math.min(firstIdentity + 50, comparison[kind].length)} of {comparison[kind].length}</p>
        <ul>{comparison[kind].slice(firstIdentity, firstIdentity + 50).map(id => <li key={id}><code>{id}</code></li>)}</ul>
        {comparison[kind].length > 50 && <><Button disabled={identityPages[kind] === 0} onClick={() => setIdentityPages(previous => ({ ...previous, [kind]: previous[kind] - 1 }))}>Previous {kind.replace("_card_ids", "")} identities</Button>
          <Button disabled={firstIdentity + 50 >= comparison[kind].length} onClick={() => setIdentityPages(previous => ({ ...previous, [kind]: previous[kind] + 1 }))}>Next {kind.replace("_card_ids", "")} identities</Button></>}
      </div>;
    })}</details>
  </section>;
}

export function PrefixComparisonDialog({ repertoireId, repertoireName, onClose }: {
  repertoireId: string; repertoireName: string; onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  const [source, setSource] = useState<PrefixSource | null>(null);
  const currentSource = useRef<PrefixSource | null>(null);
  const [route, setRoute] = useState<RoutePrefix | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [depthText, setDepthText] = useState("");
  const [filter, setFilter] = useState("");
  const [linePage, setLinePage] = useState(0);
  const [results, setResults] = useState<CandidateResult[]>([]);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [working, setWorking] = useState(false);
  const [loading, setLoading] = useState(true);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const sourceGeneration = useRef(0);
  const comparisonGeneration = useRef(0);
  const sourceController = useRef<AbortController | null>(null);
  const comparisonController = useRef<AbortController | null>(null);
  const monitorController = useRef<AbortController | null>(null);

  const cancelComparison = useCallback(() => {
    ++comparisonGeneration.current;
    comparisonController.current?.abort(); comparisonController.current = null;
    setResults([]); setWorking(false);
  }, []);
  const reportError = useCallback((cause: unknown) => {
    cancelComparison();
    const code = cause instanceof PrefixPreviewError ? cause.code : "service_error";
    setError({ code, message: cause instanceof Error ? cause.message : "Comparison unavailable. Refresh and retry." });
    setCheckedAt(null);
    if (code === "stale_snapshot") {
      ++sourceGeneration.current;
      currentSource.current = null; setSource(null); setRoute(null); setSelectedIds([]);
      monitorController.current?.abort();
    }
  }, [cancelComparison]);
  const loadSource = useCallback(async () => {
    const generation = ++sourceGeneration.current;
    cancelComparison(); sourceController.current?.abort(); monitorController.current?.abort();
    const controller = new AbortController(); sourceController.current = controller;
    currentSource.current = null; setSource(null); setRoute(null); setSelectedIds([]); setLinePage(0); setError(null); setCheckedAt(null); setLoading(true);
    try {
      const next = await readPrefixSource(repertoireId, controller.signal);
      if (controller.signal.aborted || generation !== sourceGeneration.current) return;
      currentSource.current = next; setSource(next); setCheckedAt(new Date().toLocaleTimeString());
    } catch (cause) {
      if (!controller.signal.aborted && generation === sourceGeneration.current) { setLoading(false); reportError(cause); }
    } finally { if (generation === sourceGeneration.current) setLoading(false); }
  }, [cancelComparison, repertoireId, reportError]);

  useEffect(() => {
    let disposed = false;
    const sourceEpoch = sourceGeneration;
    const comparisonEpoch = comparisonGeneration;
    queueMicrotask(() => { if (!disposed) void loadSource(); });
    return () => {
      disposed = true; ++sourceEpoch.current; ++comparisonEpoch.current;
      currentSource.current = null; sourceController.current?.abort(); comparisonController.current?.abort(); monitorController.current?.abort();
    };
  }, [loadSource]);
  useEffect(() => {
    async function checkFreshness() {
      const expectedSource = currentSource.current;
      if (!expectedSource || document.visibilityState === "hidden" || monitorController.current || comparisonController.current) return;
      const generation = sourceGeneration.current;
      const controller = new AbortController(); monitorController.current = controller;
      try {
        const latest = await readPrefixSource(repertoireId, controller.signal);
        if (controller.signal.aborted || generation !== sourceGeneration.current || currentSource.current !== expectedSource) return;
        if (latest.snapshot_id !== expectedSource.snapshot_id || latest.graph_generation !== expectedSource.graph_generation)
          throw new PrefixPreviewError("stale_snapshot", "Source or graph changed. Refresh the source and select again.");
        setCheckedAt(new Date().toLocaleTimeString());
      } catch (cause) {
        if (!controller.signal.aborted && generation === sourceGeneration.current) reportError(cause);
      } finally { if (monitorController.current === controller) monitorController.current = null; }
    }
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        monitorController.current?.abort(); cancelComparison(); setCheckedAt(null);
      } else void checkFreshness();
    };
    const interval = window.setInterval(() => void checkFreshness(), 30_000);
    window.addEventListener("focus", checkFreshness); document.addEventListener("visibilitychange", onVisibility);
    return () => { window.clearInterval(interval); window.removeEventListener("focus", checkFreshness); document.removeEventListener("visibilitychange", onVisibility); };
  }, [cancelComparison, repertoireId, reportError]);

  function selectRoute(nextRoute: RoutePrefix | null) {
    cancelComparison(); setError(null); setRoute(nextRoute);
    setSelectedIds(source && nextRoute?.moves.length ? exactRouteLines(source.lines, nextRoute).map(line => line.id) : []);
  }
  async function compare() {
    const expectedSource = currentSource.current;
    if (!expectedSource) return;
    cancelComparison(); setError(null);
    let depths: number[];
    try { depths = candidateDepths(depthText); }
    catch (cause) { setError({ code: "invalid_selection", message: (cause as Error).message }); return; }
    if (!selectedIds.length) return;
    monitorController.current?.abort();
    const generation = comparisonGeneration.current;
    const sourceRequest = sourceGeneration.current;
    const controller = new AbortController(); comparisonController.current = controller;
    const ownsRequest = () => !controller.signal.aborted && generation === comparisonGeneration.current
      && sourceRequest === sourceGeneration.current && currentSource.current === expectedSource;
    setWorking(true);
    try {
      const nextResults: CandidateResult[] = [];
      for (const depth of depths) {
        const comparison = await comparePrefixDepth(repertoireId, expectedSource, selectedIds, depth, controller.signal);
        if (!ownsRequest()) return;
        nextResults.push({ depth, comparison });
      }
      const latest = await readPrefixSource(repertoireId, controller.signal);
      if (!ownsRequest()) return;
      if (latest.snapshot_id !== expectedSource.snapshot_id || latest.graph_generation !== expectedSource.graph_generation)
        throw new PrefixPreviewError("stale_snapshot", "Source or graph changed during comparison. Refresh the source and select again.");
      setResults(nextResults); setCheckedAt(new Date().toLocaleTimeString());
    } catch (cause) { if (ownsRequest()) reportError(cause); }
    finally {
      if (comparisonController.current === controller) comparisonController.current = null;
      if (ownsRequest()) setWorking(false);
    }
  }

  const selectedLines = source?.lines.filter(line => selectedIds.includes(line.id)) ?? [];
  const savedDistribution = [...new Set(selectedLines.map(line => line.saved_depth))].sort((left, right) => left - right)
    .map(depth => `Depth ${depth}: ${selectedLines.filter(line => line.saved_depth === depth).length} lines`).join(" · ");
  const roots = source ? [...new Map(source.lines.map(line => [JSON.stringify([line.start_fen, line.trained_color]),
    { start_fen: line.start_fen, trained_color: line.trained_color, moves: [] } as RoutePrefix])).entries()].sort(([left], [right]) => left.localeCompare(right)) : [];
  const rootKey = route ? JSON.stringify([route.start_fen, route.trained_color]) : "";
  const matchingLines = useMemo(() => source?.lines.filter(line => `${line.name} ${line.id} ${line.moves.join(" ")}`.toLowerCase().includes(filter.toLowerCase())) ?? [], [source, filter]);
  const displayedLines = useMemo(() => matchingLines.slice(linePage * 50, (linePage + 1) * 50), [matchingLines, linePage]);
  const lineLabels = useMemo(() => new Map(displayedLines.map(line => [line.id,
    routeMovetext({ ...line, moves: line.moves.slice(0, 12) }) + (line.moves.length > 12 ? " …" : "")
  ])), [displayedLines]);
  return <div className="modal-backdrop"><div className="ui-dialog prefix-comparison-dialog" tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="prefix-comparison-title" ref={dialogRef}>
    <header className="prefix-comparison-header"><h2 id="prefix-comparison-title">Compare prefix depths · {repertoireName}</h2><CloseButton onClose={onClose} /></header>
    <p><strong>Read-only preview</strong> · Depth means learner decisions from each source line’s starting position.</p>
    <p>Structural efficiency is not evidence of improved learning or measured time savings.</p>
    {error && <div role="alert"><strong>{errorLabels[error.code] ?? "Comparison unavailable"}</strong>: {error.message}</div>}
    <Button disabled={loading} onClick={() => void loadSource()}>Refresh source</Button>
    {loading && <p role="status">Loading authoritative source…</p>}
    {source && <>
      <details><summary>Authoritative snapshot</summary><p>Snapshot: <code>{source.snapshot_id}</code><br />Graph generation: {source.graph_generation}</p></details>
      <p>Last freshness check: {checkedAt ?? "unverified"}. External edits are checked every 30 seconds while visible and on focus.</p>
      <fieldset><legend>Exact move-route prefix</legend>
        <label>Starting position and trained color<select value={rootKey} onChange={event => selectRoute(roots.find(([key]) => key === event.target.value)?.[1] ?? null)}>
          <option value="">Choose a saved starting position</option>{roots.map(([key, root], index) => <option key={key} value={key}>{root.trained_color} · Root {index + 1}: {root.start_fen}</option>)}
        </select></label>
        {route && Array.from({ length: route.moves.length + 1 }, (_, index) => {
          const parentRoute = { ...route, moves: route.moves.slice(0, index) };
          const nextMoves = [...new Set(exactRouteLines(source.lines, parentRoute).map(line => line.moves[index]).filter(Boolean))].sort();
          if (!nextMoves.length) return null;
          return <label key={index}>Move {index + 1}<select value={route.moves[index] ?? ""} onChange={event => selectRoute({ ...parentRoute, moves: event.target.value ? [...parentRoute.moves, event.target.value] : parentRoute.moves })}>
            <option value="">Stop at this prefix</option>{nextMoves.map(move => <option key={move} value={move}>{routeMovetext({ ...parentRoute, moves: [...parentRoute.moves, move] })}</option>)}
          </select></label>;
        })}
        {route && <p>Route filter: {routeMovetext(route) || "No moves chosen"}. Starting FEN: <code>{route.start_fen}</code></p>}
        <p>Only this exact move order is matched. Transposed incoming routes are not included automatically. The checklist below is the final selected scope.</p>
      </fieldset>
      <p><strong>Selected source lines: {selectedIds.length}</strong></p>
      <p>Current saved depths: {savedDistribution || "No selected lines"}</p>
      {!selectedIds.length && <p role="status">Empty selection — choose a move route or source lines.</p>}
      <details><summary>Selected source IDs</summary><ul>{selectedIds.map(id => <li key={id}><code>{id}</code></li>)}</ul></details>
      <label>Filter source lines by name, ID or UCI moves<input value={filter} onChange={event => { setFilter(event.target.value); setLinePage(0); }} /></label>
      <div className="prefix-source-lines">{displayedLines.map(line => <label key={line.id}>
        <input type="checkbox" checked={selectedIds.includes(line.id)} onChange={event => {
          cancelComparison(); setError(null); setSelectedIds(previous => event.target.checked ? [...previous, line.id].sort() : previous.filter(id => id !== line.id));
        }} />
        <span><strong>{line.name}</strong> · saved depth {line.saved_depth}<br />{lineLabels.get(line.id)}<br /><code>{line.id}</code>
          <details><summary>Exact source route</summary><code>{line.start_fen}</code><br />{line.trained_color} · UCI: {line.moves.join(" ")}</details></span>
      </label>)}</div>
      <p>Source lines {matchingLines.length ? linePage * 50 + 1 : 0}–{Math.min((linePage + 1) * 50, matchingLines.length)} of {matchingLines.length} matching lines. Filtering does not change selection.</p>
      {matchingLines.length > 50 && <div><Button disabled={linePage === 0} onClick={() => setLinePage(value => value - 1)}>Previous source lines</Button>
        <Button disabled={(linePage + 1) * 50 >= matchingLines.length} onClick={() => setLinePage(value => value + 1)}>Next source lines</Button></div>}
      <label>Candidate learner-decision depths<input inputMode="numeric" placeholder="e.g. 2, 3" value={depthText} onChange={event => {
        cancelComparison(); setError(null); setDepthText(event.target.value);
      }} /></label>
      <p>Enter one to four depths, 1–20. Each candidate uses that depth for every selected line. Longer previews are allowed.</p>
      <Button disabled={working || !selectedIds.length} onClick={() => void compare()}>Compare depths</Button>
      {working && <p role="status">Comparing selected lines…</p>}
    </>}
    {results.map(({ depth, comparison }) => <section key={depth} className="prefix-candidate" aria-label={`Candidate depth ${depth}`}>
      <h3>Candidate depth {depth}</h3>
      <p>{comparison.status === "no_change" ? comparison.depth_configuration_changed
        ? "No structural change; requested depth differs from saved depth." : "No change from saved depths." : "Structure changes in this preview."}</p>
      <div className="prefix-comparison-scopes"><ScopeResults comparison={comparison.selected} title="Selected scope" /><ScopeResults comparison={comparison.whole_repertoire} title="Whole repertoire" /></div>
      {comparison.selected.removed_card_ids.some(id => comparison.whole_repertoire.unchanged_card_ids.includes(id)) && <p>Cards removed from the selected scope but retained by unselected lines: {comparison.selected.removed_card_ids.filter(id => comparison.whole_repertoire.unchanged_card_ids.includes(id)).length}.</p>}
      <p>Shared cards are counted once in the whole repertoire. Unchanged identities do not imply history transfer.</p>
      <details><summary>Per-line requested and effective depths</summary><ul>{comparison.line_depths.map(line => <li key={line.line_id}>
        <code>{line.line_id}</code>: saved {line.current_depth}, requested {line.requested_depth}; effective {line.current_effective_depth} → {line.proposed_effective_depth}
      </li>)}</ul></details>
    </section>)}
  </div></div>;
}
