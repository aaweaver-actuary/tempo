# Legacy game to normal Study: first contract checkpoint

Started 2026-10-18 18:13:07 UTC on `codex/legacy-game-study-bridge`, based on main
`67a11752097147816fc8090caa10bb821265eb4e`. The isolated clone is in task-3/tempo;
exact checkpoint head/time, commands, outputs and hashes are retained outside it
in task-3/evidence. This is a contract checkpoint, not a completed Study bridge.

Scope: one current repertoire, 1–3 explicitly selected completed games, and an
unambiguous miss on an introduced physical card. Competing-route catalogue reads
reject policy ambiguity; they do not expand the training or game sample.

## Implemented

`tempo_game_evidence.LegacyComparisonSnapshot` is a strictly typed observed-graph
input containing no Content publication or activation. Its before/after token
retains target/workspace/repertoire, actual legacy source revisions, source,
catalogue and graph digests, and ready graph generation. The snapshot retains
capture time/digest, complete competing catalogue, exact original physical
card ID/revision/FEN/moves/color/memberships/introduction marker, and ordered
route/decision offsets. Validation checks legal execution, complete selected
route inventory, exact physical executable at the original route offset, learner
side, policy anchors and digest coherence. Shared cards across repertoires and
transposition re-entry are explicitly unsupported. Incomplete catalogue,
unready graph, changed capture tokens or unintroduced cards remain unknown.

`tempo_a_bridge.legacy_observation.comparison_snapshot` reuses existing
`translate(..., preserve_physical_execution=True)` and verifies owner-supplied
capture/graph/catalogue tokens. It emits an observation directly. The existing
translator's revision-1 restriction remains: other revisions stay unbound/unknown
pending an explicit compatible extension. Both authority variants use the same
route compiler, legal projection validator, `projected_definition` and
`compare_game`. Content snapshot v1/v2 rules and serialized fixtures remain
supported. The existing publication loader deliberately rejects the new input;
its store/outbox and v1/v2 delivery wire cannot honestly deliver it yet.

Remediation exports `CurrentMissProjection`, typed owner column names for
`current_miss_sql`. It permits a coherent legacy projection without nonexistent
publication columns on legacy cards. Identifier validation, workspace/current
game fences, frontier-first query boundary and qualifying settlement remain
shared. Backend adoption is pending. A valid offline digest grants no acceptance.

## Synthetic physical-card proof

The four `legacy-authority-*` fixtures retain the capture, observation, mapping
report and completed PGN. Black-side `physical-black`, revision 1, still executes
`e2e4 e7e5 g1f3 b8c6 f1b5 a7a6` from its original FEN, including the opponent
prelude. Learner offsets are 1, 3 and 5. The competing Italian route keeps the
shared prefix unknown until ply 5. The game plays `g8f6`; the expected move is
`a7a6`. There is one exact original card, no new card or activation. Matching and
opponent-departure controls have no miss. This is comparison evidence, not a
received obligation, issued attempt or qualifying review.

## Next authority and transport contract

1. Add an explicitly versioned shared delivery variant with a discriminated
   `legacy_graph` reference, capture/executable digests, physical revision and
   exact encounter/route/decision offset. Preserve activated-publication v1/v2.
   Do not put invented publication fields into their wire forms. The backend
   owner independently rechecks the reference before acceptance.
2. Add a typed legacy accepted-decision adapter to shared remediation. Normalize
   verified facts into existing contribution/frontier operations. Proposed
   storage compatibility: use a namespaced internal key (`legacy-graph:` plus
   the verified authority-token digest) and graph generation in the existing
   publication-named storage slots. These are internal authority keys, never
   Content records or activation receipts. Review that explicit mapping with
   the owner projection before any write. If rejected, re-estimate a narrow
   module schema evolution; do not adopt runtime core or migrate schedules.
3. Backend captures pages of at most 64 rows and coherent before/after source,
   catalogue and graph tokens. Close reads before mapping, legal replay and
   hashing. Reject failed graph/integrity state. At acceptance, acquire card
   locks before day/remediation locks, fence structural source changes, and
   recheck revision, full executable, membership, graph and catalogue authority.
4. Retain `HTTP -> dispatch_command -> Celery -> execute_command -> durable
   operation_receipts`. Register `game_evidence.receive_legacy` with `prepare=`
   for computation outside the write transaction. Identity binds the original
   immutable source envelope, never newly prepared values. Borrow the command
   transaction using per-cursor tuple rows. Module acknowledgement and operation
   result commit together. Pending HTTP stays pending; lost responses replay
   original operation/delivery IDs. Do not copy the independent direct-write HTTP
   receiver or remove query-only/write-allowlist guards.
5. Legacy HTTP has no evidence-token receiver; its CORS allowlist is not evidence
   authentication. Keep the command unexposed until authentication ownership and
   separately authorized provisioning are resolved. No credential or security
   configuration change is part of this checkpoint.

## Owning seams

| Seam | Existing owner and required work |
| --- | --- |
| Migration registration | `scripts/apply_postgres_migrations.py`, `backend/app/schema_version.py`, `backend/migrations/`, currently 49. Register the installed module's unchanged `001_remediation.sql` (12 tables) with pinned source/checksum and contiguous backend version. No runtime core/instance/settings or schedule conversion. Pending. |
| Legacy capture | Backend `repertoire_comparison.py`, `opening_graph.py`, `postgres_opening_graph.py`, `opening_graph_recovery.py`, canonical-scope/integrity owners. Reuse source/graph fencing; do not copy the FEN-only comparer. Database capture pending. |
| Receiver | `command_dispatch.py`, `command_gateway.register_command(..., prepare=...)`, `tasks.py` registration and `main.py` guards. Typed receiver and authority normalization pending. |
| Current miss | Shared `current_miss_sql`; backend `real_game_feedback.py`, queue presentation/admission and frozen readers. Column adapter implemented; adoption/old-path exclusion pending. |
| First issuance | `queue_commands.issue_queue_head`, after head/revision validation and before `issued_as_head=1`. Bind frontier atomically once. Inspect the old origin flag: an already-issued origin without a binding is permanently fenced. Prefetch/retry/recovery/offline work cannot acquire retrospective bindings. There is no `queue_issuance.py` at this base. |
| Admission | Prelocked `AdmissionPort` around `review_service.ensure_card_queued_after` and existing ordering/preservation for introduced cards. Do not call `promote_real_game_card`, which reads the weaker legacy predicate. Preserve head, frozen relative order, buried state and quotas. |
| Completion | Backend `learning_review_adapter.schedule/complete_attempt`, `review_service.apply_scheduling_review`: use exact persisted review ID, bound issued origin, failure marker, revision and actual outcome for shared `settle` in the review transaction. Never bind here. |
| Reconciliation | Backend `LearningReviewAdapter.reconcile_completed` delegates to shared `tempo_learning_runtime.review_reconciliation`, but lacks the runtime adapter's remediation callback. Add settlement after a real reconciled review is saved. History-only and unbound attempts cannot settle. Do not adopt runtime DB/configuration. |
| Invalidation | Shared `invalidate_settlement` in the invalidation transaction, followed by current shared predicate evaluation. Card/source invalidation fences future acceptance/admission and retains receipts. Existing threat-review invalidation is not opening-obligation proof. |
| UI reads | Workbench `contracts.ts`, `client.ts`, `workbench.ts`, `public-navigation.ts`; Study `home_view.tsx`. GET-only target/card inspection shows current/stale and accepted, queued/deferred, settled separately. Preview/link never accepts, issues or reviews. Normal Study owns the later user review; explain a protected head ahead of the target. Pending; no UI changes. |

## Coexistence decision for independent review

Resolve selected GE identity to an exact existing legacy imported-game identity
using provider/source identity and retained raw/source digest. This binding is
part of the prepared/accepted envelope and is rechecked. Unresolved mapping is
unsupported/read-only; do not infer it from names, time or FEN.

For selected `(workspace, canonical game, repertoire)`, first durable bridge
acceptance transfers miss authority for that scope to remediation. Legacy miss
admission/settlement excludes that accepted scope, including after withdrawal;
unknown siblings must not fall back to the FEN comparer. Use retained module
game/source heads and accepted contribution provenance, with an owning native
query bound. This ownership check is not another frontier store. Other games
and repertoires keep their old path. Preserve already-issued work/history; only
a later actually issued, bound qualifying review can settle a new contribution.
Priority clearing consults the combined disjoint predicates so the old weaker
rule cannot clear a bridge obligation. This policy/query needs implementation
and native proof before delivery is exposed.

## Evidence, limits and next estimate

Task evidence records exact source-mode commands and outputs. GE checks include
unchanged v1/v2 fixture equality, route/legal-FEN controls, physical projection
and legacy cases. Remediation policy/validation and legacy translator checks
pass. Tests used an existing Python 3.12 environment read-only, task sources on
`PYTHONPATH`, no installation. This is not installed artifact qualification.
An initial combined pytest invocation had a test-package collection collision;
component owning configurations resolved it. One fixture canonical-hash check
was corrected while retaining the rejecting policy-anchor assertion.

The native owner-projection case collects but is unexecuted. It covers read
columns, receipt replay, prior-issued frontier-0 review, later qualifying
settlement and invalidation using the same repository. Even a pass would not
prove legacy HTTP, backend migration or actual Study adoption. No native/build/
browser window was granted. Automatic approval review rejected resource-task
messaging pending explicit authorization. No Docker resource or new environment
was created; no live service, viewer, data, B code or primary checkout changed.

Proceed to the narrow adapter after independent contract review. No wholesale
Content/history migration, second scheduler/frontier store, runtime core adoption
or weaker provenance has proved necessary. The initial 8–12 working-hour estimate
remains low confidence, plus resource/reviewer waits; most work remains in shared
wire, backend transaction integration, coexistence and focused native/UI proof.
Re-estimate if storage, authentication ownership or source-edit fencing changes
that design. This is not a deployment estimate.
