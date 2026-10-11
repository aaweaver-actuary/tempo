import { useCallback, useEffect, useRef, useState } from "react";
import { routeMovetext, type PrefixSource } from "../domain/prefix-comparison";
import { transitionRequest, transitionSummary, type PrefixTransitionPlan, type PrefixTransitionRequest, type PrefixTransitionSummary } from "../domain/prefix-transition";
import { planPrefixTransition, PrefixPreviewError } from "../lib/prefix-comparison";
import { reservePrefixTransition, clearResolvedPrefixTransition, readPrefixTransition, recoverPrefixTransition,
  subscribePrefixTransitions, unresolvedPrefixTransition, type PrefixTransitionOperation } from "../lib/prefix-transition-command";
import { Button } from "./buttons/BaseButton";

function TransitionConsequences({ summary, request }: { summary: PrefixTransitionSummary; request: PrefixTransitionRequest }) {
  const [routePage, setRoutePage] = useState(0);
  return <section aria-label="Exact transition consequences">
    <h3>{summary.repertoireName}</h3>
    <p><strong>Selected source routes: {summary.routes.length}</strong>. Only these source lines change depth.</p>
    <ul className="prefix-transition-routes">{summary.routes.slice(routePage * 50, (routePage + 1) * 50).map(route => <li key={route.id}>
      <strong>{route.name}</strong> · learner-decision depth {route.saved_depth} → {request.candidate_depths[route.id]}<br />
      {routeMovetext(route)}<br /><code>{route.id}</code>
      <details><summary>Exact source route</summary><code>{route.start_fen}</code><p>{route.trained_color} · UCI: {route.moves.join(" ")}</p></details>
    </li>)}</ul>
    {summary.routes.length > 50 && <div><p>Routes {routePage * 50 + 1}–{Math.min((routePage + 1) * 50, summary.routes.length)} of {summary.routes.length}</p>
      <Button disabled={!routePage} onClick={() => setRoutePage(page => page - 1)}>Previous reviewed routes</Button>
      <Button disabled={(routePage + 1) * 50 >= summary.routes.length} onClick={() => setRoutePage(page => page + 1)}>Next reviewed routes</Button></div>}
    <h4>Whole repertoire transition</h4>
    <dl className="prefix-transition-counts">{Object.entries({ Unchanged: summary.counts.unchanged, Reused: summary.counts.reused,
      Added: summary.counts.added, Retired: summary.counts.retired, "Retained shared": summary.counts.retainedShared,
      ...(summary.counts.conflicting ? { Conflicting: summary.counts.conflicting } : {}) }).map(([label, value]) =>
      <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p>Shared cards are counted once. Retained shared cards remain usable through their other ownership.</p>
    <h4>History and recovery consequences</h4>
    <p>Existing schedules, seeds and {summary.preservedReviews} recorded reviews stay on their original card identities.
      Retired cards are archived with their history. Added replacements start fresh; no review history or learning evidence transfers.</p>
    <p>Unfinished attempts retired: {summary.retiredAttempts} · pending commands receiving original-card conflicts: {summary.retiredCommands}
      · queued projections superseded: {summary.supersededQueue} · completed receipts retained for replay: {summary.replayedReceipts}.</p>
    <p>Warning: attempts saved only in another browser or offline device cannot be enumerated. Delayed work for an archived card conflicts on its original identity; it does not credit a replacement.</p>
    <p>There is no automatic undo. Unselected route depths remain unchanged.</p>
  </section>;
}
const stateLabels = { pending: "Prefix transition in progress", transport_unknown: "Delivery unconfirmed", stale: "Stale transition",
  blocked: "Prefix transition needs recovery", failed: "Prefix transition failed", published: "Prefix transition published" };

export function PrefixTransitionPanel({ repertoireId, repertoireName, source, selectedIds, depth, onBack, onPublished }: {
  repertoireId: string; repertoireName: string; source: PrefixSource | null; selectedIds: string[]; depth: number | null;
  onBack: (refresh: boolean) => void; onPublished: (operation: PrefixTransitionOperation, signal: AbortSignal) => Promise<void>;
}) {
  const [plan, setPlan] = useState<PrefixTransitionPlan | null>(null);
  const [operation, setOperation] = useState<PrefixTransitionOperation | null>(null);
  const [busy, setBusy] = useState(true);
  const [failure, setFailure] = useState<{ code: string; message: string } | null>(null);
  const [cardPage, setCardPage] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const controller = useRef<AbortController | null>(null);
  const submitting = useRef(false);
  const publishedCallback = useRef(onPublished);
  useEffect(() => { publishedCallback.current = onPublished; }, [onPublished]);
  const recover = useCallback(async (retryBlocked = false) => {
    if (submitting.current) return;
    submitting.current = true; setBusy(true); setFailure(null);
    const currentController = new AbortController(); controller.current?.abort(); controller.current = currentController;
    try {
      const resolved = await recoverPrefixTransition(repertoireId, { signal: currentController.signal, retryBlocked });
      if (currentController.signal.aborted) return;
      setOperation(resolved);
      if (resolved?.state === "published") await publishedCallback.current(resolved, currentController.signal);
    } catch (cause) {
      if (!currentController.signal.aborted) setFailure({ code: "service_error", message: cause instanceof Error ? cause.message : "Recovery unavailable. Check status again." });
    } finally {
      if (controller.current === currentController) { submitting.current = false; if (!currentController.signal.aborted) setBusy(false); }
    }
  }, [repertoireId]);
  useEffect(() => {
    let disposed = false;
    const initialController = new AbortController(); controller.current = initialController;
    const observe = () => {
      try { if (!disposed) setOperation(readPrefixTransition(repertoireId)); }
      catch (cause) { if (!disposed) setFailure({ code: "storage_error", message: (cause as Error).message }); }
    };
    const wake = () => {
      if (disposed || document.visibilityState === "hidden" || !navigator.onLine) return;
      try { if (readPrefixTransition(repertoireId)) void recover(); }
      catch (cause) { setFailure({ code: "storage_error", message: (cause as Error).message }); }
    };
    queueMicrotask(async () => {
      if (disposed) return;
      try {
        const saved = readPrefixTransition(repertoireId);
        if (saved) { setOperation(saved); await recover(); return; }
        if (!source || depth === null) throw new Error("Refresh the comparison to select an exact shortening.");
        const planned = await planPrefixTransition(repertoireId, source, selectedIds, depth, initialController.signal);
        if (!disposed && !initialController.signal.aborted) setPlan(planned);
      } catch (cause) {
        if (!disposed && !initialController.signal.aborted) setFailure({ code: cause instanceof PrefixPreviewError ? cause.code : "service_error", message: (cause as Error).message });
      } finally { if (!disposed && !submitting.current) setBusy(false); }
    });
    const unsubscribe = subscribePrefixTransitions(observe);
    window.addEventListener("online", wake); window.addEventListener("focus", wake); document.addEventListener("visibilitychange", wake);
    return () => { disposed = true; controller.current?.abort(); unsubscribe(); window.removeEventListener("online", wake);
      window.removeEventListener("focus", wake); document.removeEventListener("visibilitychange", wake); };
  }, [repertoireId, source, selectedIds, depth, recover]);

  async function confirm() {
    if (submitting.current || !plan || !source || depth === null || plan.status !== "ready" || plan.blockers.length || failure) return;
    submitting.current = true; setBusy(true);
    const currentController = new AbortController(); controller.current?.abort(); controller.current = currentController;
    try {
      const checked = await planPrefixTransition(repertoireId, source, selectedIds, depth, currentController.signal);
      currentController.signal.throwIfAborted();
      if (checked.plan_id !== plan.plan_id || checked.status !== "ready")
        throw new PrefixPreviewError("stale_plan", "This exact transition changed. Refresh the comparison and review the new consequences before confirming.");
      const saved = await reservePrefixTransition(repertoireId, transitionRequest(plan, Object.fromEntries(selectedIds.map(id => [id, depth]))), transitionSummary(plan, source, repertoireName), currentController.signal);
      setOperation(saved);
      const resolved = await recoverPrefixTransition(repertoireId, { signal: currentController.signal });
      currentController.signal.throwIfAborted();
      setOperation(resolved);
      if (resolved?.state === "published") await publishedCallback.current(resolved, currentController.signal);
    } catch (cause) {
      if (!currentController.signal.aborted) setFailure({ code: cause instanceof PrefixPreviewError ? cause.code : "service_error", message: (cause as Error).message });
    } finally { if (controller.current === currentController) { submitting.current = false; if (!currentController.signal.aborted) setBusy(false); } }
  }
  const summary = operation?.summary ?? (plan && source ? transitionSummary(plan, source, repertoireName) : null);
  const request = operation?.request ?? (plan && depth !== null ? transitionRequest(plan, Object.fromEntries(selectedIds.map(id => [id, depth]))) : null);
  return <section className="prefix-transition-panel" aria-label="Prefix transition confirmation and recovery">
    <h3 ref={heading} tabIndex={-1}>{operation ? stateLabels[operation.state] : "Confirm shorter prefix transition"}</h3>
    {busy && <p role="status">{operation ? "Checking the original durable operation…" : "Checking the exact authoritative transition…"}</p>}
    {failure && <p role="alert"><strong>{["stale_plan", "stale_snapshot"].includes(failure.code) ? "Stale transition" : "Transition unavailable"}</strong>: {failure.message}</p>}
    {operation && <div role={operation.state === "published" || operation.state === "pending" ? "status" : "alert"}>
      <p>{operation.message ?? "Waiting for authoritative confirmation."}</p>
      {operation.failureCode && <p>Service error: <code>{operation.failureCode}</code></p>}
      {operation.transitionPhase && <p>Publication phase: {operation.transitionPhase.replaceAll("_", " ")}</p>}
      <details><summary>Recovery identity</summary><code>{operation.operationId}</code><p>Approved plan: <code>{operation.request.plan_id}</code></p></details>
      {unresolvedPrefixTransition(operation) && <>
        <p>Conflicting structural edits remain paused. You may close this dialog and resume from the repertoire notice.</p>
        <Button disabled={busy} onClick={() => void recover()}>Check status</Button>
        {operation.state === "blocked" && <Button disabled={busy} onClick={() => void recover(true)}>Retry publication recovery</Button>}
      </>}
      {operation.state === "published" && <Button disabled={busy} onClick={() => void recover()}>Refresh published comparison</Button>}
      {["failed", "stale"].includes(operation.state) && <Button disabled={busy} onClick={() => {
        clearResolvedPrefixTransition(repertoireId, operation.operationId); onBack(true);
      }}>Review a fresh comparison</Button>}
    </div>}
    {summary && request && <TransitionConsequences summary={summary} request={request} />}
    {plan && !operation && <>
      {plan.blockers.length > 0 && <div role="alert"><strong>Unsupported or blocked shortening</strong>
        <ul>{plan.blockers.map(blocker => <li key={`${blocker.code}:${blocker.object_id}:${blocker.reason}`}>{blocker.reason} <code>{blocker.object_id}</code></li>)}</ul>
        <p>No application has been submitted. The original repertoire remains available for study.</p></div>}
      {plan.status === "no_op" && <p role="status">These selected routes already use this depth. Refresh the comparison.</p>}
      <details><summary>Reviewed card dispositions</summary><ul>{plan.cards.slice(cardPage * 50, (cardPage + 1) * 50).map(card => <li key={card.card_id}>
        <code>{card.card_id}</code>: {card.classification.replaceAll("_", " ")}</li>)}</ul>
        {plan.cards.length > 50 && <><p>Cards {cardPage * 50 + 1}–{Math.min((cardPage + 1) * 50, plan.cards.length)} of {plan.cards.length}</p>
          <Button disabled={!cardPage} onClick={() => setCardPage(page => page - 1)}>Previous reviewed cards</Button>
          <Button disabled={(cardPage + 1) * 50 >= plan.cards.length} onClick={() => setCardPage(page => page + 1)}>Next reviewed cards</Button></>}
      </details>
      <Button disabled={busy || plan.status !== "ready" || Boolean(plan.blockers.length) || Boolean(failure)} onClick={() => void confirm()}>Confirm shortening</Button>
    </>}
    {!operation && <Button disabled={busy} onClick={() => onBack(Boolean(failure) || plan?.status === "no_op")}>{failure ? "Review a fresh comparison" : "Back to comparison"}</Button>}
  </section>;
}
