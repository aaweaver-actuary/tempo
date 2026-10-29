import type { BoardAppearance } from "@tempo/board-ui";
import { useEffect, useId, useState } from "react";
import type { Proposal } from "../product-api/contracts";
import { Button } from "../../components/buttons/BaseButton";
import { Field, Surface, TabList } from "../../components/ui";
import type { RepertoireNavigationIntent, RepertoireReviewClient, RepertoireReviewRead, ReviewFinding } from "./api";
import { RepertoireReviewSession, visibleConflicts } from "./state";
import { ConflictPanel } from "./ConflictPanel";
import { CoverageEvidence } from "./EvidencePanel";
import { PositionViewer } from "./PositionViewer";
import { ProposalPanel } from "./ProposalPanel";
import "./styles.css";

export interface RepertoireReviewViewProps {
  readonly appearance: BoardAppearance;
  readonly api: RepertoireReviewClient;
  readonly repertoireId: string;
  readonly repertoires?: readonly { readonly id: string; readonly name: string }[];
  readonly onRepertoireChange?: (repertoireId: string) => void;
  readonly onNavigate?: (intent: RepertoireNavigationIntent) => void;
  readonly session?: RepertoireReviewSession;
}
type Loaded = { readonly read: RepertoireReviewRead; readonly proposals: readonly Proposal[] };

export function RepertoireReviewView({ api, repertoireId, repertoires, onRepertoireChange, onNavigate, session, appearance }: RepertoireReviewViewProps) {
  const [ownSession] = useState(() => new RepertoireReviewSession());
  const [loaded, setLoaded] = useState<(Loaded & { api: RepertoireReviewClient; request: number }) | null>(null);
  const [failure, setFailure] = useState<{ repertoireId: string; message: string } | null>(null);
  const [request, setRequest] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    void Promise.all([api.review(repertoireId, { signal: controller.signal }), api.proposals(repertoireId)]).then(([read, proposals]) => {
      if (!active) return;
      if (read.repertoire_id !== repertoireId) throw new Error("The response names another repertoire.");
      setLoaded({ api, request, read, proposals: proposals.filter(proposal => proposal.repertoire_id === repertoireId) });
      setFailure(null);
    }).catch((cause: unknown) => {
      if (active) { setLoaded(null); setFailure({ repertoireId, message: cause instanceof Error ? cause.message : "Review unavailable" }); }
    });
    return () => { active = false; controller.abort(); };
  }, [api, repertoireId, request]);
  const current = loaded?.read.repertoire_id === repertoireId && loaded.api === api && loaded.request === request ? loaded : null;
  return <main className="repertoire-review" aria-label="Repertoire review">
    <header className="repertoire-review-header"><div><h2>Repertoire</h2><p>Review conflicts, coverage and familiar responses.</p></div>
      <div className="repertoire-review-actions">
        {repertoires && onRepertoireChange && <Field label="Repertoire"><select value={repertoireId} onChange={event => onRepertoireChange(event.target.value)}>
          {repertoires.map(repertoire => <option key={repertoire.id} value={repertoire.id}>{repertoire.name}</option>)}
        </select></Field>}
        <Button onClick={() => { setLoaded(null); setFailure(null); setRequest(value => value + 1); }}>Refresh review</Button>
        {onNavigate && <><Button onClick={() => onNavigate({ kind: "import", repertoire_id: repertoireId })}>Import lines</Button>
          <Button onClick={() => onNavigate({ kind: "manual_edit", repertoire_id: repertoireId })}>Manual editor</Button>
          <Button onClick={() => onNavigate({ kind: "settings", repertoire_id: repertoireId })}>Likelihood settings</Button></>}
      </div>
    </header>
    {failure?.repertoireId === repertoireId && <p role="alert">Repertoire review unavailable: {failure.message}. Retry with Refresh review.</p>}
    {!current && failure?.repertoireId !== repertoireId && <p role="status">Loading repertoire review…</p>}
    {current && <ReviewWorkspace key={`${repertoireId}/${current.read.source_revision}/${current.read.source_digest}/${request}`}
      {...current} appearance={appearance} api={api} session={session ?? ownSession} onNavigate={onNavigate} />}
  </main>;
}

const tabs = [
  ["conflicts", "Conflicts"], ["missing_reply", "Missing replies"], ["consistency", "Consistency"],
  ["common_blunder", "Common blunders"], ["lines", "Lines & positions"],
] as const;
type Tab = typeof tabs[number][0];
function ReviewWorkspace({ read, proposals, api, session, onNavigate, appearance }: Loaded & Pick<RepertoireReviewViewProps, "api" | "onNavigate" | "appearance"> & { session: RepertoireReviewSession }) {
  const [tab, setTab] = useState<Tab>(visibleConflicts(read).length ? "conflicts" : "missing_reply");
  const [selected, setSelected] = useState<string | null>(null);
  const tabsId = useId();
  const conflicts = visibleConflicts(read);
  const findings = read.findings.filter(finding => finding.kind === tab);
  const rows = tab === "conflicts" ? conflicts.map(row => ({ id: row.conflict_id, label: row.choices.map(choice => choice.move_uci).join(" / ") }))
    : tab === "lines" ? read.lines.map(line => ({ id: line.route_id, label: line.label }))
      : findings.map(finding => ({ id: finding.finding_id, label: finding.label }));
  const selectedId = rows.some(row => row.id === selected) ? selected : rows[0]?.id;
  const conflict = tab === "conflicts" ? conflicts.find(row => row.conflict_id === selectedId) : undefined;
  const finding = findings.find(row => row.finding_id === selectedId);
  const line = tab === "lines" ? read.lines.find(row => row.route_id === selectedId) : undefined;
  const proposal = finding?.proposal_id ? proposals.find(item => item.proposal_id === finding.proposal_id) : undefined;
  return <>
    <p>Source revision {read.source_revision} · {read.repertoire_id}</p>
    <TabList label="Repertoire review categories">{tabs.map(([id, label]) => <button key={id} type="button" role="tab"
      id={`${tabsId}-${id}`} aria-selected={tab === id} tabIndex={tab === id ? 0 : -1}
      aria-controls={`${tabsId}-panel`} onClick={() => { setTab(id); setSelected(null); }}>{label}</button>)}</TabList>
    <section role="tabpanel" id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-${tab}`} className="repertoire-review-workspace">
      <nav aria-label="Review items"><ul>{rows.map(row => <li key={row.id}><Button aria-pressed={selectedId === row.id} onClick={() => setSelected(row.id)}>{row.label}</Button></li>)}</ul></nav>
      <div className="repertoire-review-detail">
        {rows.length === 0 && <p>No supplied {tabs.find(([id]) => id === tab)?.[1].toLowerCase()}.</p>}
        {conflict && <ConflictPanel appearance={appearance} key={conflict.conflict_id} conflict={conflict} read={read} api={api} session={session} />}
        {line && <Surface><h3>{line.label}</h3><PositionViewer appearance={appearance} position={line.position} identity={line.route_id} /></Surface>}
        {finding && <FindingDetail appearance={appearance} key={`${finding.finding_id}/${proposal?.display_digest}`} finding={finding} proposal={proposal}
          read={read} api={api} session={session} onNavigate={onNavigate} />}
      </div>
    </section>
  </>;
}

function FindingDetail({ finding, proposal, read, api, session, onNavigate, appearance }: {
  appearance: BoardAppearance; finding: ReviewFinding; proposal?: Proposal; read: RepertoireReviewRead; api: RepertoireReviewClient;
  session: RepertoireReviewSession; onNavigate?: RepertoireReviewViewProps["onNavigate"];
}) {
  const existing = finding.kind === "common_blunder" ? finding.existing_card : null;
  return <Surface aria-label="Repertoire finding"><h3>{finding.label}</h3><CoverageEvidence finding={finding} />
    {existing ? <>
      <PositionViewer appearance={appearance} position={finding.position} identity={finding.finding_id} />
      <p>Response already covered by {existing.presentation_id} · {existing.introduced ? "introduced" : "unintroduced"}. Use this existing card.</p>
      <Button disabled={!onNavigate} onClick={() => onNavigate?.({ kind: existing.introduced ? "practice_existing" : "introduce_existing",
        repertoire_id: read.repertoire_id, presentation_id: existing.presentation_id, expected_revision: existing.revision })}>
        {existing.introduced ? "Practice existing card" : "Introduce existing card"}
      </Button>
    </> : proposal ? <ProposalPanel appearance={appearance} key={`${proposal.proposal_id}/${proposal.proposal_revision}/${proposal.display_digest}`}
      proposal={proposal} read={read} api={api} session={session} position={finding.position} finding={finding} /> : <>
      <PositionViewer appearance={appearance} position={finding.position} identity={finding.finding_id} /><p>No content proposal supplied. Refresh when analysis is ready.</p>
    </>}
  </Surface>;
}
