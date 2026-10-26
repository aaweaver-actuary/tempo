import type { EngineEvidence, Proposal } from "../product-api/contracts";
import type { ReviewFinding } from "./api";

function percent(value: number | null | undefined) { return value === null || value === undefined ? "unknown" : `${(value * 100).toFixed(1)}%`; }

export function CoverageEvidence({ finding }: { finding: ReviewFinding }) {
  return <section aria-label="Likelihood evidence">
    <p>Position reach: {percent(finding.reach_probability)} · Missing reply mass: {percent(finding.missing_mass)}</p>
    <p>Evidence status: {finding.evidence_status}</p>
    {finding.probability_evidence.length === 0 && <p>Conditional reply likelihood: unknown · Sample count: unknown · Unknown mass: unknown</p>}
    {finding.probability_evidence.map(evidence => <article key={evidence.evidence_id}>
      <p>{evidence.provider}{evidence.model ? ` / ${evidence.model}` : ""} · {evidence.basis}</p>
      <p>{evidence.population
        ? `Population: ${evidence.population.rating_min}–${evidence.population.rating_max}; ${evidence.population.time_controls.join(", ")}`
        : `Personal account: ${evidence.personal_account ?? "unknown"}`}</p>
      <p>Observed: {evidence.observed_at ?? "unknown"} · Sample count: {evidence.counts?.total_count ?? "unknown"}</p>
      <p>Conditional reply likelihood{finding.reply_uci ? ` (${finding.reply_uci})` : ""}: {percent(finding.reply_uci
        ? evidence.distribution?.moves.find(move => move.move_uci === finding.reply_uci)?.probability : null)}</p>
      <p>Unknown mass: {percent(evidence.distribution?.unknown_mass)}</p>
      {evidence.unavailable_reason && <p>Unavailable: {evidence.unavailable_reason}</p>}
    </article>)}
    <small>Incomplete, stale and unavailable evidence cannot establish full coverage.</small>
  </section>;
}

function score(value: EngineEvidence["best"]) {
  if (!value?.score) return `inconclusive${value?.unavailable_reason ? ` (${value.unavailable_reason})` : ""}`;
  return value.score.kind === "mate" ? `mate ${value.score.value}` : `${value.score.value} CP`;
}
export function ProposalEvidence({ proposal, finding }: { proposal: Proposal; finding?: ReviewFinding }) {
  const supports = [...new Set(proposal.supporting_position_keys)];
  return <section aria-label="Proposal evidence">
    <p>Maximum loss: {proposal.max_loss_cp} CP · default 30 CP, from the learner’s perspective.</p>
    {proposal.threshold_adjustment_reason && <p>Threshold adjustment: {proposal.threshold_adjustment_reason}</p>}
    <h4>{supports.length} distinct supporting repertoire positions</h4>
    {supports.length === 0 ? <p>No supporting positions supplied.</p> : <ul>{supports.map(key => <li key={key}>
      <code>{key}</code>
      <p>Similarity / differences: {finding?.similarity_differences.find(item => item.supporting_position_key === key)?.description ?? "not supplied"}</p>
    </li>)}</ul>}
    {proposal.engine_evidence.length === 0 && <p>Engine evidence unavailable; no CP approval evidence supplied. Manual conflict choices remain owner-validated.</p>}
    {proposal.engine_evidence.map(evidence => <article key={evidence.evidence_id}>
      <p>{evidence.context.engine_version} · {evidence.context.variant} · learner {evidence.context.learner_color}</p>
      <p>Budget: depth {evidence.context.max_depth ?? "unknown"}, nodes {evidence.context.max_nodes ?? "unknown"}, time {evidence.context.max_time_ms ?? "unknown"} ms; threads {evidence.context.threads}; hash {evidence.context.hash_mb} MB</p>
      <p>Evaluation target: <code>{evidence.context.target_fen}</code></p>
      <p>Evaluation start: <code>{evidence.context.starting_fen}</code></p>
      <p>Evaluation history: {evidence.context.history_uci.join(" ") || "Starting position"}</p>
      <p>Best: {evidence.best?.move_uci ?? "unknown"} · {score(evidence.best)}</p>
      {evidence.unavailable_reason && <p>Inconclusive: {evidence.unavailable_reason}</p>}
      <ul>{evidence.candidates.map(candidate => {
        const bestScore = evidence.best?.score;
        const candidateScore = candidate.score;
        const loss = bestScore?.kind === "centipawns" && candidateScore?.kind === "centipawns" ? bestScore.value - candidateScore.value : null;
        return <li key={candidate.move_uci}>
          {candidate.move_uci}: {score(candidate)} · depth {candidate.depth ?? "unknown"} · nodes {candidate.nodes ?? "unknown"}
          {loss !== null ? ` · loss ${loss} CP` : " · mate/inconclusive: no ordinary CP comparison"}
        </li>;
      })}</ul>
    </article>)}
    <small>The Repertoire owner validates move quality and the exact proposal. Displayed subtraction does not approve content.</small>
  </section>;
}
