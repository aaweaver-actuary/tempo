import { installPrefixTransitionTestLocks } from "./support/prefix-transition-locks";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { deleteRepertoireCommand } from "../../app/lib/repertoire-delete-command";
import { saveCanonicalPrefixCommand } from "../../app/lib/canonical-prefix-command";
import { recoverBranchReceipt, saveBranchCommand } from "../../app/lib/branch-command";
import { recoverBranchRemovalReceipt, removeBranchCommand } from "../../app/lib/branch-removal-command";
import { reviseCardCommand } from "../../app/lib/card-revision-command";
import { deleteCardCommand } from "../../app/lib/card-delete-command";
import { acceptPrefixSplitCommand } from "../../app/lib/prefix-split-command";
import { recoverAnalysisPasteReceipt, saveAnalysisPasteCommand } from "../../app/lib/analysis-paste-command";
import { prepareFindingCard } from "../../app/lib/finding-card-command";
import { applyOpportunityCommand } from "../../app/lib/opportunity-command";
import { saveTacticCapture } from "../../app/lib/tactic-capture-command";
import { enqueueIntegrityRepair, pendingIntegrityRepairs, flushIntegrityRepairs } from "../../app/lib/integrity-repair-outbox";
import { enqueuePendingDiscoveryAdmission, pendingDiscoveryAdmissions, flushPendingDiscoveryAdmissions, retryPendingDiscoveryAdmission } from "../../app/lib/discovery-admission-outbox";
import { clearResolvedPrefixTransition } from "../../app/lib/prefix-transition-command";
import { savePgnImportCommand } from "../../app/lib/pgn-import-command";
import { prefixTransitionPlanSchema, transitionSummary } from "../../app/domain/prefix-transition";
import { prefixSourceSchema } from "../../app/domain/prefix-comparison";
import transition from "../fixtures/prefix-comparison/transition.json";
import structural from "../fixtures/prefix-comparison/structural.json";

const prefixKey = "tempo-pending-prefix-transition-v1:rep";
const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const branch = { repertoire_id: "rep", starting_fen: startingFen, moves: ["e2e4"], trained_color: "white", name: "e4" };
function reserveStructuralFixture(affectedRepertoireIds?: string[]) {
  const summary = transitionSummary(prefixTransitionPlanSchema.parse(transition.ready), prefixSourceSchema.parse(structural.source), "Combined Black");
  if (affectedRepertoireIds) summary.affectedRepertoireIds = affectedRepertoireIds;
  localStorage.setItem(prefixKey, JSON.stringify({ version: 1, operationId: "prefix-operation", repertoireId: "rep",
    request: transition.request, summary, state: "pending", approval: { plan: transition.ready, source: { repertoire_id: structural.source.repertoire_id, snapshot_id: structural.source.snapshot_id, graph_generation: structural.source.graph_generation, lines: structural.source.lines } } }));
}
beforeEach(() => { installPrefixTransitionTestLocks(); reserveStructuralFixture(); });
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

const mutationCases: [string, string, () => Promise<unknown>][] = [
  ["repertoire_deletion", "tempo-pending-repertoire-delete-v1", () => deleteRepertoireCommand("rep")],
  ["canonical_prefix_save", "tempo-canonical-prefix-save-v1:rep", () => saveCanonicalPrefixCommand("rep", "preview", 1)],
  ["branch_creation", "tempo-pending-repertoire-branch-v1", () => saveBranchCommand(branch)],
  ["branch_removal", "tempo-pending-branch-removal-v1", () => removeBranchCommand(branch)],
  ["card_revision", "tempo-pending-card-revision-v1", () => reviseCardCommand({ cardId: "card", startingFen, moves: ["e2e4"], historyMode: "preserve", expectedRevision: 1 })],
  ["card_deletion", "tempo-pending-card-delete-v1", () => deleteCardCommand("card", 1)],
  ["prefix_split_acceptance", "tempo-pending-prefix-split-v1", () => acceptPrefixSplitCommand("card", 1)],
  ["analysis_paste", "tempo-pending-analysis-paste-v1", () => saveAnalysisPasteCommand({ text: "e4", starting_fen: null, preview_token: "preview", selections: [{ index: 0, repertoire_id: "rep", acknowledge_conflict: false }] })],
  ["finding_card_save", "tempo-pending-finding-card-v1:finding", () => prepareFindingCard("finding", { save: true })],
  ["opportunity_membership", "tempo-pending-opportunity-v1:opportunity", () => applyOpportunityCommand("rep", "opportunity", "train", "reviewed-evidence")],
  ["tactic_capture", "tempo-pending-tactic-capture-v1", () => saveTacticCapture({ starting_fen: startingFen, moves: ["e2e4"], source_kind: "manual", note: "" })],
  ["pgn_import", "tempo-pending-pgn-import-v1", () => savePgnImportCommand(new File(["1. e4 *"], "opening.pgn"), "white", 4)],
];
for (const [name, pendingKey, command] of mutationCases) {
  it(`issue81_${name}_final_admission_suppresses_request_and_new_identity`, async () => {
    const fetcher = vi.fn(async () => Response.json({ detail: "unexpected mutation" }, { status: 400 }));
    vi.stubGlobal("fetch", fetcher);
    await command().catch(() => undefined);
    expect(fetcher.mock.calls).toHaveLength(0);
    expect(localStorage.getItem(pendingKey)).toBeNull();
    expect(JSON.parse(localStorage.getItem(prefixKey)!).operationId).toBe("prefix-operation");
  });
}
it("issue81_missing_target_scope_never_authorizes_repertoire_deletion", async () => {
  reserveStructuralFixture([]);
  const fetcher = vi.fn(async () => Response.json({ deleted: true, id: "rep" }));
  vi.stubGlobal("fetch", fetcher);
  await deleteRepertoireCommand("rep").catch(() => undefined);
  expect(fetcher).not.toHaveBeenCalled();
});
it("issue81_malformed_prefix_storage_suppresses_mutation", async () => {
  localStorage.setItem(prefixKey, "unreadable");
  const fetcher = vi.fn(async () => Response.json({ deleted: true, id: "rep" }));
  vi.stubGlobal("fetch", fetcher);
  await deleteRepertoireCommand("rep").catch(() => undefined);
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem(prefixKey)).toBe("unreadable");
});
it("issue81_completed_structural_receipt_remains_recoverable_during_prefix_transition", async () => {
  localStorage.setItem("tempo-pending-repertoire-delete-v1", JSON.stringify({ operationId: "original-delete", repertoireId: "rep", learnedCards: "keep" }));
  const fetcher = vi.fn(async () => Response.json({ state: "complete", response: { deleted: true, id: "rep" } }));
  vi.stubGlobal("fetch", fetcher);
  await deleteRepertoireCommand("rep", "keep");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0]).toHaveLength(1);
});

it("issue81_storage_access_failure_suppresses_structural_request", async () => {
  const fetcher = vi.fn(async () => Response.json({ deleted: true, id: "rep" }));
  vi.stubGlobal("fetch", fetcher);
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Storage denied"); });
  await expect(deleteRepertoireCommand("rep")).rejects.toThrow("Storage denied");
  expect(fetcher).not.toHaveBeenCalled();
});
it("issue81_unavailable_serialization_suppresses_structural_request_and_identity", async () => {
  vi.stubGlobal("navigator", { locks: undefined });
  const fetcher = vi.fn(async () => Response.json({ deleted: true, id: "rep" }));
  vi.stubGlobal("fetch", fetcher);
  await expect(deleteRepertoireCommand("rep")).rejects.toThrow("Web Locks");
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBeNull();
});
it("issue81_independent_repertoire_final_admission_allows_real_command", async () => {
  const fetcher = vi.fn(async () => Response.json({ deleted: true, id: "independent" }));
  vi.stubGlobal("fetch", fetcher);
  await deleteRepertoireCommand("independent");
  expect(fetcher).toHaveBeenCalledOnce();
});
it("issue81_branch_removal_without_complete_rebuild_scope_cannot_guess_independence", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(removeBranchCommand({ ...branch, repertoire_id: "independent" })).rejects.toThrow("Resolve the prefix transition");
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem("tempo-pending-branch-removal-v1")).toBeNull();
});
it("issue81_verified_resolution_and_permitted_clear_restore_structural_submission", async () => {
  const operation = JSON.parse(localStorage.getItem(prefixKey)!);
  localStorage.setItem(prefixKey, JSON.stringify({ ...operation, state: "published", transitionPhase: "complete", result: {
    status: "complete", operation_id: operation.operationId, repertoire_id: "rep", plan_id: operation.request.plan_id,
    graph_generation: operation.request.graph_generation + 1, queue_date: operation.request.study_day, no_op: false,
  } }));
  await clearResolvedPrefixTransition("rep", operation.operationId);
  const fetcher = vi.fn(async () => Response.json({ deleted: true, id: "rep" }));
  vi.stubGlobal("fetch", fetcher);
  await deleteRepertoireCommand("rep");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(prefixKey)).toBeNull();
});
it("issue81_pending_structural_replay_rechecks_prefix_ownership_after_status_read", async () => {
  localStorage.removeItem(prefixKey);
  localStorage.setItem("tempo-pending-repertoire-delete-v1", JSON.stringify({ operationId: "original-delete", repertoireId: "rep", learnedCards: "keep" }));
  const fetcher = vi.fn(async () => { reserveStructuralFixture(); return Response.json({}, { status: 404 }); });
  vi.stubGlobal("fetch", fetcher);
  await expect(deleteRepertoireCommand("rep", "keep")).rejects.toThrow("Resolve the prefix transition");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(JSON.parse(localStorage.getItem("tempo-pending-repertoire-delete-v1")!).operationId).toBe("original-delete");
});
it("issue81_conflicting_repair_choice_does_not_allocate_outbox_identity", () => {
  expect(() => enqueueIntegrityRepair({ repertoireId: "rep", issueId: "issue", signature: "signature", selectedMoveUci: "e2e4" })).toThrow("Resolve the prefix transition");
  expect(pendingIntegrityRepairs()).toHaveLength(0);
});
it("issue81_conflicting_discovery_choice_does_not_allocate_outbox_identity", () => {
  expect(() => enqueuePendingDiscoveryAdmission({ opportunityId: "opportunity", selectedMoveUci: "e2e4", evidenceFingerprint: "reviewed" })).toThrow("Resolve the prefix transition");
  expect(pendingDiscoveryAdmissions()).toHaveLength(0);
});
it("issue81_previously_saved_discovery_rechecks_admission_before_delivery_and_retry", async () => {
  localStorage.removeItem(prefixKey);
  enqueuePendingDiscoveryAdmission({ opportunityId: "opportunity", selectedMoveUci: "e2e4", evidenceFingerprint: "reviewed" });
  const original = pendingDiscoveryAdmissions()[0].operationId;
  reserveStructuralFixture();
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(fetcher).not.toHaveBeenCalled();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ operationId: original, state: "pending", error: expect.stringContaining("Resolve the prefix transition") });
  expect(() => retryPendingDiscoveryAdmission("opportunity")).toThrow("Resolve the prefix transition");
  expect(pendingDiscoveryAdmissions()[0].operationId).toBe(original);
});
it("issue81_previously_saved_repair_rechecks_admission_after_read_only_evidence", async () => {
  localStorage.removeItem(prefixKey);
  const original = enqueueIntegrityRepair({ repertoireId: "rep", issueId: "issue", signature: "signature", selectedMoveUci: "e2e4" });
  reserveStructuralFixture();
  const requests: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: unknown, init?: RequestInit) => {
    if (init?.method === "POST") requests.push(init);
    if (String(input).includes("/operations/")) return Response.json({ state: "unknown" });
    return Response.json({ repertoire_id: "rep", status: "needs_repair", issue_count: 1, first_issue_id: "issue", scan_status: "idle",
      scan_generation: "scan:1", scan_progress: { completed: 1, total: 1 }, last_scan_error: null,
      issues: [{ id: "issue", kind: "multiple_responses", fen_key: startingFen.split(" ").slice(0, 4).join(" "),
        fen: startingFen, trained_color: "white", signature: "signature", moves: [], sources: [] }] });
  }));
  await flushIntegrityRepairs();
  expect(requests).toHaveLength(0);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: original.operationId, error: expect.stringContaining("Resolve the prefix transition") });
});

for (const [family, pendingKey, recover, response] of [
  ["analysis_paste", "tempo-pending-analysis-paste-v1", recoverAnalysisPasteReceipt, { saved: [], affected_repertoire_ids: ["rep"], gap_resolved: false }],
  ["branch_save", "tempo-pending-repertoire-branch-v1", recoverBranchReceipt, { id: "line", duplicate: false, moves: ["e2e4"], integrity: { status: "unchecked", issue_count: 0, first_issue_id: null } }],
  ["branch_removal", "tempo-pending-branch-removal-v1", recoverBranchRemovalReceipt, { deleted_line_count: 1, deleted_card_count: 0, retained_line_count: 0, integrity: { status: "unchecked", issue_count: 0, first_issue_id: null } }],
] as const) {
  it(`issue81_${family}_status_only_recovery_preserves_draft_and_sends_no_mutation`, async () => {
    localStorage.setItem(pendingKey, JSON.stringify({ operationId: "original-operation", body: "immutable earlier body" }));
    const fetcher = vi.fn(async () => Response.json({ state: "complete", response }));
    vi.stubGlobal("fetch", fetcher);
    expect(await recover()).toEqual(response);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0]).toEqual([expect.stringContaining("/api/operations/original-operation")]);
    expect(localStorage.getItem(pendingKey)).toBeNull();
    expect(localStorage.getItem(prefixKey)).not.toBeNull();
  });
  it(`issue81_${family}_status_only_pending_recovery_never_replays`, async () => {
    localStorage.setItem(pendingKey, JSON.stringify({ operationId: "original-operation", body: "immutable earlier body" }));
    const fetcher = vi.fn(async () => Response.json({ state: "pending" }));
    vi.stubGlobal("fetch", fetcher);
    await expect(recover()).rejects.toThrow();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0]).toHaveLength(1);
    expect(JSON.parse(localStorage.getItem(pendingKey)!).operationId).toBe("original-operation");
  });
}

it("issue81_legacy_summary_cannot_authorize_apparently_independent_deletion", async () => {
  const operation = JSON.parse(localStorage.getItem(prefixKey)!); delete operation.approval;
  localStorage.setItem(prefixKey, JSON.stringify(operation));
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(deleteRepertoireCommand("independent")).rejects.toThrow("Resolve the prefix transition");
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBeNull();
});
for (const field of ["snapshot_id", "transition_snapshot_id", "plan_id", "graph_generation", "candidate_depths"] as const) {
  it(`issue81_corrupt_reviewed_${field}_suppresses_structural_request_even_when_route_ids_match`, async () => {
    const operation = JSON.parse(localStorage.getItem(prefixKey)!);
    operation.request[field] = field === "candidate_depths" ? { a: 1, b: 2 } : field === "graph_generation" ? 2 : "different-reviewed-evidence";
    localStorage.setItem(prefixKey, JSON.stringify(operation));
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    await expect(deleteRepertoireCommand("independent")).rejects.toThrow("unreadable");
    expect(fetcher).not.toHaveBeenCalled();
    expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBeNull();
  });
}

for (const unavailableChannel of ["missing", "constructor_error"] as const) {
  it(`issue81_${unavailableChannel}_storage_publication_channel_suppresses_request_and_identity`, async () => {
    localStorage.removeItem(prefixKey);
    vi.stubGlobal("MessageChannel", unavailableChannel === "missing" ? undefined : class {
      constructor() { throw new Error("Browser messaging unavailable"); }
    });
    const fetcher = vi.fn(async () => Response.json({ deleted: true, id: "rep" }));
    vi.stubGlobal("fetch", fetcher);
    await deleteRepertoireCommand("rep").catch(() => undefined);
    expect(fetcher).not.toHaveBeenCalled();
    expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBeNull();
  });
}
