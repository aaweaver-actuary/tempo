import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { Proposal } from "../product-api/contracts";
import { Button } from "../../components/buttons/BaseButton";
import { DataTable, Field, Surface } from "../../components/ui";
import type { RepertoireReviewClient, RepertoireReviewRead, ReviewFinding, ReviewPosition } from "./api";
import { currentProposal, decisionFor, RepertoireReviewSession, validatedPreview } from "./state";
import { PositionViewer } from "./PositionViewer";
import { ProposalEvidence } from "./EvidencePanel";

export interface ProposalPanelProps {
  readonly proposal: Proposal;
  readonly read: RepertoireReviewRead;
  readonly api: RepertoireReviewClient;
  readonly session: RepertoireReviewSession;
  readonly position: ReviewPosition;
  readonly finding?: ReviewFinding;
}

/** The parent keys by proposal binding/selection. This component owns no content. */
export function ProposalPanel({ proposal, read, api, session, position, finding }: ProposalPanelProps) {
  useSyncExternalStore(session.subscribe, session.snapshot, session.snapshot);
  const [preview, setPreview] = useState<Proposal | null>(null);
  const [validated, setValidated] = useState(false);
  const [responses, setResponses] = useState(proposal.edits.map(edit => edit.proposed_response_uci));
  const [dirty, setDirty] = useState(false);
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, []);
  const displayed = preview ?? proposal;
  const operation = session.latest(displayed) ?? session.latest(proposal);
  const pending = operation?.state === "pending";
  const terminal = operation?.receipt?.status === "accepted" || operation?.receipt?.status === "dismissed";
  const stale = !currentProposal(displayed, read);
  const locked = busy || pending || terminal;

  function showPreview(value: Proposal) {
    setPreview(value); setResponses(value.edits.map(edit => edit.proposed_response_uci));
    setDirty(false); setValidated(true); setApproved(false); setError(null);
  }
  async function requestPreview() {
    const request = ++generation.current;
    setBusy(true); setValidated(false); setApproved(false); setError(null);
    try {
      const receipt = await api.preview(displayed);
      if (request === generation.current) showPreview(validatedPreview(receipt, read, proposal.proposal_id));
    } catch (cause) { if (request === generation.current) setError(cause instanceof Error ? cause.message : "Preview unavailable"); }
    finally { if (request === generation.current) setBusy(false); }
  }
  async function editPreview() {
    if (!api.prepareEdit) return;
    const request = ++generation.current;
    setBusy(true); setValidated(false); setApproved(false); setError(null);
    try {
      const replacement = await api.prepareEdit({ proposal: displayed, responses_uci: responses });
      if (request !== generation.current) return;
      if (!currentProposal(replacement, read) || replacement.proposal_id !== displayed.proposal_id) throw new Error("Edited proposal does not match the current source.");
      const decision = decisionFor(displayed, "edit", replacement);
      await session.submit(api, decision);
      if (request !== generation.current) return;
      const receipt = session.operation(decision)?.receipt;
      if (receipt) showPreview(validatedPreview(receipt, read, proposal.proposal_id, displayed.proposal_revision));
    } catch (cause) { if (request === generation.current) setError(cause instanceof Error ? cause.message : "Edited preview unavailable"); }
    finally { if (request === generation.current) setBusy(false); }
  }
  async function retry() {
    if (!operation) return;
    const request = ++generation.current;
    await session.submit(api, operation.decision);
    if (request !== generation.current) return;
    const receipt = session.operation(operation.decision)?.receipt;
    if (receipt?.status === "preview") {
      try { showPreview(validatedPreview(receipt, read, proposal.proposal_id, operation.decision.action === "edit" ? operation.decision.expected_proposal_revision : undefined)); }
      catch (cause) { setError(cause instanceof Error ? cause.message : "Preview unavailable"); }
    }
  }
  function accept() {
    if (!preview || !validated || dirty || !approved || stale || locked) return;
    setValidated(false); setApproved(false);
    void session.submit(api, decisionFor(preview, "accept"));
  }
  function dismiss() {
    if (locked) return;
    setValidated(false); setApproved(false);
    void session.submit(api, decisionFor(displayed, "dismiss"));
  }

  return <Surface className="repertoire-review-proposal" aria-label="Proposal detail">
    <h3>{displayed.title}</h3><p>{displayed.explanation}</p>
    <p>Proposal revision {displayed.proposal_revision} · source revision {displayed.source_revision}</p>
    <PositionViewer position={position} identity={`${displayed.repertoire_id}/${displayed.proposal_id}/${displayed.display_digest}`} />
    <h4>Repertoire changes</h4>
    <DataTable><thead><tr><th>Route / position</th><th>Current move</th><th>Proposed move / content</th></tr></thead>
      <tbody>{displayed.edits.map((edit, index) => <tr key={`${edit.route_id}/${index}`}>
        <td>{edit.route_id}<br /><code>{edit.position_key}</code></td>
        <td>{edit.previous_response_uci ?? "Missing response"}</td>
        <td>{edit.proposed_response_uci}<pre>{edit.displayed_pgn}</pre></td>
      </tr>)}</tbody></DataTable>
    <h4>Associated cards — included in acceptance</h4>
    {displayed.card_changes.length === 0 ? <p>No associated card changes.</p> : <ol aria-label="Associated card changes">{displayed.card_changes.map((change, index) => <li key={index}>
      <strong>{change.action === "add" ? "New card" : `${change.action} existing card`}</strong>
      <p>{change.presentation_id ?? "New identity assigned by owner"}{change.expected_revision ? ` · revision ${change.expected_revision}` : ""} · learner {change.learner_color}</p>
      <p>Start: <code>{change.starting_fen}</code></p><p>Moves: {change.moves.join(" ")}</p>
    </li>)}</ol>}
    <ProposalEvidence proposal={displayed} finding={finding} />
    <fieldset disabled={locked || stale || !api.prepareEdit}>
      <legend>Edit proposed responses</legend>
      {responses.map((response, index) => <Field key={index} label={`Proposed response ${index + 1} (UCI)`}>
        <input value={response} onChange={event => {
          generation.current += 1;
          setResponses(values => values.map((value, at) => at === index ? event.target.value : value));
          setDirty(true); setValidated(false); setApproved(false); setError(null);
        }} />
      </Field>)}
      <Button disabled={!dirty || responses.some(value => !/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(value))} onClick={() => { void editPreview(); }}>Validate edited preview</Button>
    </fieldset>
    {!api.prepareEdit && <p>Inline edit preview is unavailable. Use the Manual editor.</p>}
    {dirty && <p>Edited content needs a new validated preview and fresh approval.</p>}
    {stale && <p role="alert">Stale proposal: refresh the source before approving.</p>}
    {error && <p role="alert">{error}</p>}
    {operation && <div role="status" aria-live="polite">
      {pending ? <><p>Pending acknowledgment. No success is confirmed.</p>{operation.error && <p>{operation.error}</p>}
        <Button pending={session.isRunning(operation.decision)} onClick={() => { void retry(); }}>Retry pending operation</Button></>
        : <p>Owner status: {operation.receipt?.status}{operation.receipt?.reason ? ` · ${operation.receipt.reason}` : ""}</p>}
    </div>}
    <div className="repertoire-review-actions">
      <Button disabled={locked || dirty || stale} onClick={() => { void requestPreview(); }}>Preview proposal</Button>
      <Button disabled={locked} onClick={dismiss}>Dismiss proposal</Button>
    </div>
    <label className="repertoire-review-approval">
      <input type="checkbox" checked={approved} disabled={!preview || !validated || dirty || stale || locked} onChange={event => setApproved(event.target.checked)} />
      I approve these repertoire changes and all associated card changes.
    </label>
    <Button variant="primary" disabled={!preview || !validated || dirty || !approved || stale || locked} onClick={accept}>Accept displayed changes</Button>
    <small>Only the owning API can apply content or cards. Editing never publishes.</small>
  </Surface>;
}
