import type {
  EngineEvidence, ProbabilityEvidence, Proposal, ProposalDecision,
  RepertoireChangeReceipt, RepertoireReviewApi,
} from "../product-api/contracts";

/** Read projections for this screen, not alternative content/evidence DTOs. */
export type ReviewPosition = Pick<EngineEvidence["context"],
  "starting_fen" | "history_uci" | "target_fen" | "variant" | "learner_color"> & {
  readonly position_key: string;
};
export interface ReviewLine {
  readonly route_id: string;
  readonly label: string;
  readonly position: ReviewPosition;
}
export interface ConflictRow {
  readonly conflict_id: string;
  readonly repertoire_id: string;
  readonly position: ReviewPosition;
  readonly affected_routes: readonly string[];
  readonly study_block: { readonly status: "blocked" | "unknown"; readonly reason: string };
  /** Repertoire supplies complete alternative proposals, including affected cards. */
  readonly choices: readonly { readonly move_uci: string; readonly proposal: Proposal }[];
}
export interface ReviewFinding {
  readonly finding_id: string;
  readonly kind: "missing_reply" | "consistency" | "common_blunder";
  readonly label: string;
  readonly position: ReviewPosition;
  readonly proposal_id: string | null;
  readonly reach_probability: number | null;
  readonly reply_uci: string | null;
  readonly missing_mass: number | null;
  readonly evidence_status: "current" | "stale" | "unavailable";
  readonly probability_evidence: readonly ProbabilityEvidence[];
  readonly similarity_differences: readonly {
    readonly supporting_position_key: string; readonly description: string;
  }[];
  readonly existing_card: {
    readonly presentation_id: string; readonly revision: number; readonly introduced: boolean;
  } | null;
}
export interface RepertoireReviewRead {
  readonly repertoire_id: string;
  readonly source_revision: number;
  readonly source_digest: string;
  readonly lines: readonly ReviewLine[];
  readonly conflicts: readonly ConflictRow[];
  readonly findings: readonly ReviewFinding[];
}
export interface RequestOptions { readonly signal?: AbortSignal }
export interface CommandOptions extends RequestOptions { readonly operation_id: string }

/** #501 injects concrete owners. No default endpoint, provider or global store. */
export interface RepertoireReviewClient extends RepertoireReviewApi {
  review(repertoireId: string, options?: RequestOptions): Promise<RepertoireReviewRead>;
  preview(proposal: Proposal, options?: RequestOptions): Promise<RepertoireChangeReceipt>;
  decide(decision: ProposalDecision, options?: CommandOptions): Promise<RepertoireChangeReceipt>;
  /** Constructs coherent edits/card changes; no publication. UI never rewrites PGN/evidence. */
  prepareEdit?(request: { readonly proposal: Proposal; readonly responses_uci: readonly string[] }, options?: RequestOptions): Promise<Proposal>;
}

export type RepertoireNavigationIntent =
  | { readonly kind: "import" | "manual_edit" | "settings"; readonly repertoire_id: string }
  | { readonly kind: "practice_existing" | "introduce_existing"; readonly repertoire_id: string;
      readonly presentation_id: string; readonly expected_revision: number };
