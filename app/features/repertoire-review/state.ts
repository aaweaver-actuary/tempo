import type { Proposal, ProposalDecision, RepertoireChangeReceipt } from "../product-api/contracts";
import type { ConflictRow, RepertoireReviewClient, RepertoireReviewRead } from "./api";

export function decisionFor(proposal: Proposal, action: ProposalDecision["action"], edited: Proposal | null = null): ProposalDecision {
  return {
    proposal_id: proposal.proposal_id, expected_proposal_revision: proposal.proposal_revision,
    expected_source_revision: proposal.source_revision, display_digest: proposal.display_digest,
    action, edited_proposal: edited,
  };
}
export function currentProposal(proposal: Proposal, read: RepertoireReviewRead): boolean {
  return proposal.repertoire_id === read.repertoire_id && proposal.source_revision === read.source_revision && proposal.source_digest === read.source_digest;
}
/** Scope display only. The Repertoire owner detects conflicts and enforces the Study block. */
export function visibleConflicts(read: RepertoireReviewRead): readonly ConflictRow[] {
  return read.conflicts.filter(row => row.repertoire_id === read.repertoire_id &&
    row.position.target_fen.split(" ")[1] === (row.position.learner_color === "white" ? "w" : "b"));
}
export function validatedPreview(receipt: RepertoireChangeReceipt, read: RepertoireReviewRead, proposalId: string): Proposal {
  const proposal = receipt.proposal;
  if (receipt.status !== "preview" || receipt.proposal_id !== proposalId || !proposal ||
      proposal.proposal_id !== proposalId || !currentProposal(proposal, read) || receipt.source_revision !== read.source_revision) {
    throw new Error(receipt.reason ?? "The owner did not return a current validated preview. Refresh before approving.");
  }
  return proposal;
}

export interface ReviewOperation {
  readonly operation_id: string;
  readonly decision: ProposalDecision;
  readonly state: "pending" | "complete";
  readonly receipt: RepertoireChangeReceipt | null;
  readonly error: string | null;
}
/** Transport identity/uncertain acknowledgments, never repertoire or Study state. */
export class RepertoireReviewSession {
  private readonly operations = new Map<string, ReviewOperation>();
  private readonly running = new Map<string, Promise<void>>();
  private readonly listeners = new Set<() => void>();
  private version = 0;
  constructor(private readonly newId: () => string = () => crypto.randomUUID()) {}
  readonly subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  readonly snapshot = () => this.version;
  private changed() { this.version += 1; this.listeners.forEach(listener => listener()); }
  operation(decision: ProposalDecision): ReviewOperation | undefined { return this.operations.get(JSON.stringify(decision)); }
  isRunning(decision: ProposalDecision): boolean { return this.running.has(JSON.stringify(decision)); }
  submit(api: RepertoireReviewClient, decision: ProposalDecision): Promise<void> {
    const key = JSON.stringify(decision);
    const inFlight = this.running.get(key);
    if (inFlight) return inFlight;
    const previous = this.operations.get(key);
    if (previous?.state === "complete") return Promise.resolve();
    const operation: ReviewOperation = previous ?? {
      operation_id: this.newId(), decision, state: "pending", receipt: null, error: null,
    };
    this.operations.set(key, { ...operation, error: null });
    // Start in a microtask so the in-flight fence exists even for a synchronous throw.
    const work = Promise.resolve().then(() => api.decide(operation.decision, { operation_id: operation.operation_id }))
      .then(receipt => {
        if (receipt.proposal_id !== decision.proposal_id || receipt.source_revision < decision.expected_source_revision) {
          throw new Error("Unexpected acknowledgment; outcome is still pending.");
        }
        this.operations.set(key, { ...operation, state: "complete", receipt, error: null });
      }).catch((error: unknown) => {
        this.operations.set(key, { ...operation, state: "pending", error: error instanceof Error ? error.message : "Acknowledgment unavailable" });
      }).finally(() => { this.running.delete(key); this.changed(); });
    this.running.set(key, work);
    this.changed();
    return work;
  }
}
