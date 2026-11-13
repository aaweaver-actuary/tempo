# Quota-efficient execution

## Evidence for changing the workflow

Thirty of the 38 mainline merges since the packet baseline came from T02 ownership/census/tooling branches. That is useful metadata progress, but it is not equivalent to delivery of the pending review/browser/install work. The existing project history is approximately 479,164 characters, and the full task reconstruction is about 1.2 MB. Loading these repeatedly can exhaust context without changing the product. We have no model-billing breakdown and do not attribute a percentage of the spent quota to any particular activity.

## Operating rules for the next run

- **One owner, bounded workers.** A launcher hands off and exits. Use one integration owner, two implementation workers and one reviewer/operator slot; do not keep creator, manager and coordinator agents repeating the same state. Reuse a worker for review when code is ready. One agent owns each actual deliverable; one person owns heavy resources.
- **Small startup.** Read the short owner prompt, current state and active issue. A worker receives its assigned IDs, relevant predecessor heads, permitted files, selected task prompt, acceptance and stop condition. Retrieve another file only to resolve a named question. Do not load the full project history or 129-card packet into every context.
- **One status authority.** The real execution ledger records task ID, state, source SHA, focused proof, native/browser receipt, review finding, merge, installation and next blocker. GitHub issues/PRs link this detail; the personal project summary records only decisions, status and next action. Never copy every log into every tracker.
- **Reuse existing work.** All 13 active PR heads reduce to eight candidate aggregates. Each aggregate inherits the union of its own and every contained child's unresolved acceptance. Do not separately rebuild or re-review the same unchanged content, but carry every unproved child check into the aggregate's proof matrix. Before reimplementing an unevidenced row, inspect the existing owner/receipt once and record an accepted no-change disposition when justified.
- **Batch coherent small edits.** Finish remaining mapping, annotation and fixture updates per owner boundary rather than one new branch/review/manager cycle per file or handful of diagnostics. Keep consequential transaction, identity and public-interface changes reviewable. A batch is a cohesive diff, not an unchecked repository sweep.
- **One review can cover correctness and architecture.** Include a brief Guardian-like look in the existing independent review. Escalate depth for authority, persistence, concurrency, composition and schema changes. Do not create ceremonial duplicate reviews or review receipts for documentation-only changes.
- **Focused development proof, shared integration proof.** Use the smallest meaningful component checks while editing. Run required expensive native/browser/lifecycle acceptance on a coherent final candidate where the original criteria permit shared evidence. Reuse a receipt only when its relevant source/dependency/schema/fixture/runtime inputs are unchanged. A new headline SHA alone is not a reason for every historical suite to rerun.
- **Fix launch prerequisites once.** Record supported Python, pytest/imports, required assets, current disk budget and owned resource readiness before a stack starts. Preflight/build errors are not test failures or passes. Do not repeat a blocked launch without a changed prerequisite.
- **Concise handoffs.** Default worker report: result, exact head, changed boundary, tests actually run, remaining blocker and next action, ideally 200–300 words plus paths. Keep full logs on disk. Shared issues get material deltas, not a narration of every command.
- **Checkpoint before exhaustion.** Persist after each accepted deliverable and approximately every 60–90 minutes of long work. These are reporting intervals, not deadlines that abort valid tests. If remaining quota is visible, reserve its final portion for push/checkpoint/resource cleanup and stop dispatching new work. Never claim to measure quota when tooling does not expose it.
- **Stop at the assigned boundary.** Source work awaiting heavy proof may hand off. A blocked install does not justify more optional architecture work. Preserve incomplete work and the exact next command; do not start a fresh census merely to keep agents busy.

## Tradeoff

Larger coherent batches reduce coordination overhead but can hide an unrelated behavioral change. Keep batches inside one owner/contract, retain a readable diff and one proportionate independent review, and split when dependencies or acceptance genuinely differ. This preserves assurance while removing repeated context and orchestration.

## Optional run telemetry

Record active-agent count, elapsed time by authoring/review/build/proof, new and reused receipts, and completed deliverables. Add token usage only if available from the host. Compare useful accepted outcomes per run; PR counts and mapped-file counts alone are weak completion measures.
