import { describe, expect, it, vi } from "vitest";
import proposalDocument from "../../contracts/product-v02/fixtures/proposal.json";
import decisionDocument from "../../contracts/product-v02/fixtures/proposal-accept.json";
import { createProductRepertoireReviewApi } from "../../app/features/product-api/repertoire-review-api";
import { ProposalDecisionSchema, ProposalSchema } from "../../app/features/product-api/contracts";

const proposal = ProposalSchema.parse(proposalDocument.payload);
const decision = ProposalDecisionSchema.parse(decisionDocument.payload);
const endpoints = {
  review: (rep: string) => `/repertoires/${encodeURIComponent(rep)}/review`,
  proposals: (rep: string) => `/repertoires/${encodeURIComponent(rep)}/proposals`,
  evidence: (key: string) => `/evidence?position=${encodeURIComponent(key)}`,
  preview: (rep: string) => `/repertoires/${encodeURIComponent(rep)}/preview`,
  decide: "/repertoires/scoped/decide",
};
const receipt = { owner_receipt_id: "owner-receipt", proposal_id: proposal.proposal_id,
  status: "preview", source_revision: proposal.source_revision, proposal, reason: null };

describe("private repertoire API composition", () => {
  it("constructs inertly, sends exact preview/decision and retains operation correlation", async () => {
    const request = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify(receipt)));
    const api = createProductRepertoireReviewApi({ endpoints, fetch: request });
    expect(request).not.toHaveBeenCalled();
    await api.preview(proposal);
    const preview = request.mock.calls[0];
    expect(preview[0]).toBe(endpoints.preview(proposal.repertoire_id));
    expect(JSON.parse(preview[1]!.body as string)).toEqual(proposal);
    await api.decide(decision, { operation_id: "stable-operation" });
    const command = request.mock.calls[1];
    expect(JSON.parse(command[1]!.body as string)).toEqual(decision);
    expect(command[1]!.headers).toMatchObject({ "Idempotency-Key": "stable-operation" });
  });

  it("fails optional requests without exposing server payloads", async () => {
    const request = vi.fn<typeof fetch>().mockResolvedValue(new Response("private server diagnostic", { status:409 }));
    const api = createProductRepertoireReviewApi({ endpoints, fetch: request });
    await expect(api.proposals("rep")).rejects.toThrow("Repertoire request failed (409)");
  });

  it("passes cancellation and rejects a different repertoire projection", async () => {
    const signal = new AbortController().signal;
    const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ repertoire_id:"other" })));
    const api = createProductRepertoireReviewApi({ endpoints, fetch: request });
    await expect(api.review("rep", { signal })).rejects.toThrow("Invalid repertoire review projection");
    expect(request.mock.calls[0][1]!.signal).toBe(signal);
  });
});
