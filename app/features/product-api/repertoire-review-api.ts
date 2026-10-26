import {
  ProbabilityEvidenceSchema, ProposalSchema, RepertoireChangeReceiptSchema,
  type Proposal, type ProposalDecision,
} from "./contracts";
import type {
  CommandOptions, ConflictRow, RepertoireReviewClient, RepertoireReviewRead, RequestOptions,
} from "../repertoire-review/api";

export interface RepertoireReviewEndpoints {
  review: (repertoireId: string) => string;
  proposals: (repertoireId: string) => string;
  evidence: (positionKey: string) => string;
  preview: (repertoireId: string) => string;
  decide: string;
}

/** Root supplies scope/URLs and authenticated fetch. No requests start on import. */
export function createProductRepertoireReviewApi({ endpoints, fetch: request = globalThis.fetch }: {
  endpoints: RepertoireReviewEndpoints;
  fetch?: typeof globalThis.fetch;
}): RepertoireReviewClient {
  async function json(url: string, options?: RequestOptions, body?: unknown, operationId?: string): Promise<unknown> {
    const response = await request(url, {
      method: body === undefined ? "GET" : "POST", signal: options?.signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      headers: {
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(operationId ? { "Idempotency-Key": operationId } : {}),
      },
    });
    if (!response.ok) throw new Error(`Repertoire request failed (${response.status})`);
    return response.json();
  }
  return {
    async review(repertoireId: string, options?: RequestOptions) {
      const value = await json(endpoints.review(repertoireId), options) as RepertoireReviewRead;
      if (!value || value.repertoire_id !== repertoireId || !Number.isSafeInteger(value.source_revision)
          || value.source_revision < 1 || typeof value.source_digest !== "string"
          || !Array.isArray(value.lines) || !Array.isArray(value.conflicts) || !Array.isArray(value.findings)) {
        throw new Error("Invalid repertoire review projection");
      }
      // Approval-bearing values use the shared strict schema before rendering.
      const conflicts = value.conflicts.map(conflict => {
        if (!Array.isArray(conflict.choices)) throw new Error("Invalid conflict choices");
        return { ...conflict, choices: conflict.choices.map((choice: ConflictRow["choices"][number]) => ({ ...choice,
          proposal: ProposalSchema.parse(choice.proposal) })) };
      });
      return { ...value, conflicts };
    },
    async proposals(repertoireId: string) {
      const values = await json(endpoints.proposals(repertoireId));
      if (!Array.isArray(values)) throw new Error("Invalid repertoire proposals");
      return values.map(value => ProposalSchema.parse(value));
    },
    async evidence(positionKey: string) {
      const values = await json(endpoints.evidence(positionKey));
      if (!Array.isArray(values)) throw new Error("Invalid probability evidence");
      return values.map(value => ProbabilityEvidenceSchema.parse(value));
    },
    async preview(proposal: Proposal, options?: RequestOptions) {
      return RepertoireChangeReceiptSchema.parse(await json(endpoints.preview(proposal.repertoire_id), options, proposal));
    },
    async decide(decision: ProposalDecision, options?: CommandOptions) {
      return RepertoireChangeReceiptSchema.parse(await json(endpoints.decide, options, decision, options?.operation_id));
    },
  };
}
