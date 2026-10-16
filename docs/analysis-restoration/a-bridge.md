# A: authoritative Content → Learning → Game Evidence bridge

Status: implemented; qualification and limits are recorded below. This is an
operator workflow for separate service installations, not a live installation or
cutover. Only explicitly synthetic data has been rehearsed.

## Revisions and ownership

Branch: `codex/analysis-a-bridge`. Base:
`0edf566ad48f51846a43d3341809379ff0469ada`. Isolated checkout:
`/Users/andy/.codex/worktrees/analysis-a-bridge/tempo`. Main/live study checkout was
not used for development. Interface checkpoint:
`cae8280da7b551044f27b9d3ae74448aaad04285`; implementation/fixture checkpoint:
`a23b8cbff5d8ec3cf680f7bb94301646546a99d1`. Final tested/published refs are recorded
in the qualification handoff below; a documentation-only successor does not change
artifact qualification.

Content owns import mapping, immutable drafts, approval/source reservations,
cancellation, bounded legal validation, publication/export and delivery records.
Learning alone owns publication acceptance, activation/retirement, historical
identity aliases, admission, schedules, attempts, reviews and remediation.
Game Evidence owns comparison, immutable analysis inputs and its existing outbox.
The database-free `tools/analysis-restoration/a_bridge/` operator invokes installed
CLIs and authenticated HTTP. It imports no service implementation and owns no
foreign database. B owns engines; C owns Intelligence/findings/Workbench/pilot.
Shared Content Publication v1 and Game Evidence wire v1/v2 are unchanged.

## Operator workflow

Install only the selected components with their own locked environments. Provision
separate Content, Learning and Game Evidence databases and restricted runtime
roles using their existing owner-only migration procedures. Startup never
migrates. These commands do not provision or modify a live installation.

Keep each service's database settings in its own environment. Content uses
`TEMPO_CONTENT_DATABASE_URL`, `TEMPO_CONTENT_WORKSPACE` and
`TEMPO_CONTENT_RUNTIME_ROLE`. Learning's private
`TEMPO_LEARNING_OPERATOR_TOKEN_FILE` differs from its Study token and evidence
token. Game Evidence receives only its own database URL and the evidence token
file. The operator token stays in an owner-readable file with no group/other
permissions; it is absent from payloads, exports, receipts and logs. HTTP is
permitted for loopback; other operator origins require HTTPS. Redirects are denied.

The following examples use deliberately supplied file paths/identities. Inspect
JSON results and use their exact returned IDs and digests; do not substitute IDs
from the synthetic historical fixture into another installation.

```sh
tempo-content import plan INPUT --manifest MANIFEST --expected-revision 0
tempo-content draft show DRAFT_ID
tempo-content draft approve DRAFT_ID --digest DRAFT_SHA256
tempo-content operation show OPERATION_ID

tempo-a-bridge --content-cli /absolute/installed/bin/tempo-content \
  --learning-origin https://learning.example \
  --operator-token-file /private/operator-token apply OPERATION_ID
```

`plan` persists a draft without changing the source or Learning. Approval binds
the exact digest, expected source revision, provenance and entire complete source.
It reserves that source; competing direct edits and approvals fail before source
admission. `apply` durably authorizes application under the same Content source
lock as cancellation, then runs bounded Content work. `--max-slices N` bounds
validation work (default 64, maximum 10,000). `pending_validation` or
`pending_activation` is not completion. Repeat the original operation:

```sh
tempo-a-bridge --content-cli /absolute/installed/bin/tempo-content \
  --learning-origin https://learning.example \
  --operator-token-file /private/operator-token reconcile OPERATION_ID
tempo-content operation cancel OPERATION_ID
```

Cancellation succeeds only before durable application authorization. A delayed
worker cannot authorize a cancelled intent. Once authorization commits,
cancellation returns `application_started`; keep the original operation/authority
and reconcile it. A known pre-delivery validation or bounded-export failure is
recorded as `failed`, releases the source reservation, and permits a corrected
draft at the actual current source revision. An uncertain remote outcome remains
recoverable and retains its reservation; it is never called cancelled or failed
merely because a response was lost.

Before first delivery the operator freezes the original endpoint, operation ID,
request, expected activation revision and workspace-local due date. It reads the
original Learning receipt before exact replay. HTTP 202 stays pending. Content
independently reads that authenticated receipt before recording completion.
`operation show` retains the genuine response and transport origin. Study
credentials cannot read/retry publication operations through generic Study routes.
An empty successor invokes Learning's real retirement operation; it does not stage
an empty active publication. The prior serving generation remains until atomic
replacement/retirement commits.

```sh
tempo-a-bridge --content-cli /absolute/installed/bin/tempo-content \
  --learning-origin https://learning.example \
  --operator-token-file /private/operator-token snapshot OPERATION_ID --output SNAPSHOT

tempo-game-evidence load-snapshot SNAPSHOT
tempo-game-evidence import-pgn GAME --workspace WORKSPACE --player PLAYER --source-id SOURCE
tempo-game-evidence work --max-jobs 100 \
  --learning-endpoint https://learning.example/internal/evidence \
  --learning-token-file /private/evidence-token
tempo-game-evidence analysis-export create --workspace WORKSPACE --output EXPORT_DIRECTORY
tempo-game-evidence analysis-export page INPUT_ID --workspace WORKSPACE --kind positions
tempo-game-evidence analysis-export verify EXPORT_DIRECTORY
```

`--game-id ID` may repeat to select 1–100 exact current games. Creation freezes all
pages and the manifest atomically in Game Evidence before writing files. A hidden
`.tempo-analysis-export.json` reservation pins the directory to that input before
any page is written; repeating `create` resumes it even after games change. The
manifest is written last. A new directory requests a fresh input. Existing bytes
are never overwritten by different content. `page --after CURSOR` uses the exact
opaque cursor returned by the previous page; cursors bind workspace, export,
kind, revision and manifest digest. `verify` and schema commands need no database
or running services. Current comparisons/indexes must be complete and coherent;
stale or unavailable inputs fail explicitly rather than silently disappearing.

## Documents, mapping and examples

Generated JSON schemas for the five service-local interfaces are under
`tools/analysis-restoration/a_bridge/fixtures/schemas/`. Regenerate with
`tempo-content schema --model import-manifest`, `--model response-proposal`,
`tempo-learning publication-schema`, `tempo-game-evidence snapshot-schema`, and
`tempo-game-evidence analysis-export schema`.

| Local version | Authority and required facts |
| --- | --- |
| Authored import manifest v1 | Content: workspace/source, declared complete scope, provenance/raw-byte SHA256, explicit PGN repertoire/color/policy/route assignments, optional one-to-one legacy links and explicit re-entry |
| Learning publication bundle v2 | Learning: original Content Publication/IDs, source digest, opening execution facts, actual policy/repertoire/color routes and bindings, validated historical aliases |
| Comparison snapshot v2 | Game Evidence: pinned Content export, matching publication/routes/bindings, genuine activation, accepted source/publication/routes digests, separate authenticated serving observation |
| Analysis inputs v1 | Game Evidence: immutable manifest and serialized pages with workspace/input/revision/digests/counts, all game positions and original comparisons/reasons |
| Response proposal v1 | Content: draft-only idempotency ID, workspace/source/repertoire/policy/color, expected source revision/digest, exact route/ply/opponent move/resulting learner FEN, legal accepted lines and provenance/evidence references |

Structured Content snapshots retain their stable keys, exact six-field starting
FEN, ordered moves, alternatives, memberships and bindings. PGN has no durable
Content IDs, learner color or history: every leaf requires an explicit mapping.
Its fingerprint is SHA256 of canonical JSON `[starting_fen, ordered_uci_moves]`.
Every variation is retained; incomplete/ambiguous/unused mappings fail. Event
names and record order never create identity. Re-entry defaults false and needs
an actual binding authorization. Provenance/completeness are stored outside
executable source semantics. Source revision, executable revision, inventory
version/generation and publication generation retain their separate meanings.

V2 supports reference-graded opening move lines. Other typed families and existing
v1 consumers retain their existing paths; they are not converted to generic
exercises. Learning v2 does not author introduction allowances or frozen order.
Aliases require an existing compatible historical opening card and a unique
one-to-one Content identity; ambiguous merges/ownership/semantics fail before
activation. The accepted-alias remediation mechanism resolves observations to the
original physical Learning object. Reviews, schedules, receipts and historical
IDs are preserved. Metadata successors may reuse executable revision only with
identical FEN/specification; changed executable content retains revision fences.

Actual synthetic producer documents are in
`tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/`, with original
byte hashes in `SHA256SUMS.json`. They came from installed Content, real
operator-authenticated activation and the installed producer, not a fabricated
receiver. The historical loopback origin is no longer running. Input identity:
`ec2f522cd43f1d759e63ce6e3c00e1a71cc9b2d5e08e0585de781155ff169dcd`;
manifest digest:
`78adc89ee72705cedef73ee99be8af382553b96dab88e639d3e4bec80c316490`.

Each `positions[].opponent_decision` includes established pre-deviation route,
policy/color/history, actual opponent move, resulting learner FEN/ply, complete
legal reply scope and selected accepted responses with source/publication/digests,
and explicit re-entry provenance. Covered encounters are included for eligible
distinct-game denominators; `deviations` contains only deviations. The fixture has
two eligible encounters after `e4`: covered `e5` and deviation `c5`. Off-route learner
`c4` is not an opponent gap. Missing/ambiguous/incomplete context stays unknown.
Game Evidence reconstructs all response scopes from accepted routes/definitions;
recomputing an export checksum cannot authorize false coverage.

Historical activation and currently observed serving status are distinct. A
snapshot binds the requested generation's accepted source/route digests even if a
newer generation serves. Older snapshot promotion is rejected. Not-current or
incomplete inputs cannot withdraw evidence. Offline exports record status at a
revision/time and cannot authorize future Learning effects. Existing outbox
receipts remain authority; corrections cannot overtake uncertain predecessors.

`fixtures/response-proposal.json` is a valid draft shape with a synthetic
placeholder digest, not an applicable current proposal. The installed synthetic
fixture contains the actually consumed proposal. `invalid-proposal.json` uses
`kind: gap_context` and cannot create content. The only proposal flow is:

```sh
tempo-content proposal inspect PROPOSAL
tempo-content draft create --proposal PROPOSAL
tempo-content draft show DRAFT_ID
tempo-content draft approve DRAFT_ID --digest SHA256
```

Exact proposal-ID replay returns the original draft; conflicting bytes fail.
Approval/publication/activation/normal admission are separate operations.

## Limits and migrations

The selected capacity path is the bounded bundle-v2 increase: at most 2,048
complete definitions and 8 MiB serialized bundle, not additive active publications
or truncation. Content inputs are bounded at 8 MiB, routes at 1,000 plies and
10,000 assignments; the complete bridge export is at most 32,768 distinct opponent
positions and 64 MiB. Proposal lines have at most 20 alternatives of 40 plies.
Analysis exports select 1–100 games, 64 items per immutable page, at most 8 MiB per
page and 64 MiB in total. Limits are conjunctive: 2,048 definitions does not promise
arbitrary route/alternative combinations fit the byte bounds. Unsupported scope
fails before Learning delivery; no capacity is advertised beyond these bounds.

Learning legal validation occurs outside its activation writer transaction.
Set-based writes preserve publication → sorted repertoire → sorted card → queue
locking and atomic review effects. Publication v2 retries stay with the operator;
Study maintenance does not execute heavy optional v2 validation. Existing pool,
lock, statement and transaction budgets remain unchanged (writer lock 250 ms,
statement 1,500 ms, transaction 2,500 ms). No timeout increase is used.

Appended migrations: Content `0002_bridge.sql` (immutable drafts, durable intents,
whole-source reservations/delivery records), `0003_bridge_failure.sql` (known
pre-delivery failed state); Learning runtime `0003_content_bridge.sql`
(one-to-one historical aliases, after the existing remediation migration);
Game Evidence `003.sql` (immutable export manifests/pages). Existing migration
payloads/checksums and shared contract resources are unchanged. Reapply existing
owner-only grants for the selected installation; this PR does not run them live.

## Required A regression map

All identities live in regular owning suites, not an alternative acceptance runner.
Parameterized cases retain their separate executable identities. The 21 rows
below group installed delivery and its one-obligation replay proof together.

| # | Required invariant | Executable owner |
| --- | --- | --- |
| 1 | Identity-preserving rename/permutation | Content `test_A_identity_rename_permutation_uses_stable_policy_decisions` plus existing canonical permutation tests |
| 2 | Incomplete staging | Content native `test_A_incomplete_stage_manifest_cannot_be_admitted[count,digest]` |
| 3 | Conflicting replay | Content native `test_A_conflicting_page_replay_keeps_accepted_bytes` |
| 4 | Missing approval | Content native `test_A_approval_not_persisted_cannot_authorize_apply` |
| 5 | Restart | Content native `test_A_restart_after_approval_recovers_same_operation` |
| 6 | Both cancellation orders | Content native `test_A_cancel_and_apply_contend_at_durable_authorization[cancel,apply]` and serial schedules |
| 7 | Competing source edits | Content native `test_A_competing_approval_reserves_source_and_digest` and existing distinct-edit race |
| 8 | Proposal without approval | Content `test_A_proposal_is_not_approval_and_requires_legal_executable_content`; installed test below proves zero Learning change before approval |
| 9 | Identity conflict rollback | Learning `test_A_identity_conflict_rolls_back_activation` |
| 10 | Complete publication above 128 | Learning `test_A_complete_large_publication_measured_without_truncation[129,512,2048]` |
| 11 | Activation response loss | Learning `test_A_activation_response_loss_replays_original_operator_receipt`; installed test drops genuine HTTP response |
| 12 | Final-definition retirement | Learning `test_A_old_ack_after_replacement_and_final_retirement`; installed empty Content successor |
| 13 | Historical acknowledgement after replacement | Same Learning test and installed historical/successor snapshots |
| 14 | Retained history | Learning `test_A_identity_rename_replay_preserves_genuine_review_and_alias`; `test_A_metadata_successor_preserves_issued_attempt_under_measured_foreground_load` |
| 15 | Mixed-generation rejection | Game Evidence `test_A_snapshot_v2_rejects_mixed_generation_and_forged_response_scope` |
| 16 | Colors/policies/transpositions/alternatives/re-entry | Game Evidence regular `test_comparison.py`, especially policy/alternative/custom-position/re-entry cases; installed v2 accepted-alternative proof |
| 17 | Immutable replay/provenance | Game Evidence `test_A_analysis_export_replay_is_offline_and_inspectable`, `test_A_analysis_export_rejects_mixed_or_incomplete_generation`; native `test_A_interrupted_export_resumes_frozen_input_after_game_revision_changes` |
| 18 | Actual installed delivery and one obligation after replay | Learning `test_A_actual_evidence_delivery_export_proposal_and_optional_off_study` |
| 19 | Late evidence | Same installed test issues before evidence, then requires a fresh qualifying Study attempt |
| 20 | Foreign database denial | Same installed test proves all three restricted runtime identities denied both foreign databases |
| 21 | Real Study review/reload with optional services stopped | Learning regular `test_game_evidence_runtime.py` browser/producer-stop proofs and `learning-full-study` production acceptance |

Additional named regression: independent accepted alternatives start from the same
board (`Content test_A_accepted_alternatives_validate_from_the_same_starting_position`).
The previous validator mutated its starting board while checking the first line;
copying the board preserves legal alternative validation. Known pre-delivery
failure/source release, queued v2 operator retry and credential-family separation
also have regular native owners. Component discovery registers the operator as a
tool, not a fifth domain service, and includes Content/operator changes in the
actual installed Learning/Study acceptance inputs.

## Qualification handoff

Fresh commands, source/artifact hashes, complete collected/executed/JUnit
identities, benchmark measurements, failures and cleanup receipts are retained
outside the disposable checkout under
`/Users/andy/tempo/test-results/analysis-a-bridge/`. Initial validation records are
background evidence only. The final command/outcome record is added after the
ongoing qualification completes.

The installed four-case run under `bridge-coherent/` passed in 183.14 s. Complete
synthetic custom-position sources had one route/membership/accepted line per
definition, each route one ply. End-to-end plan/approval and bounded
validation/publication/delivery measurements were:

| Definitions | Source bytes | Publication bytes | Plan/approval s | Validation + delivery s | Max child RSS bytes |
| --- | --- | --- | --- | --- | --- |
| 129 | 77,387 | 66,885 | 0.430 | 6.669 | 84,606,976 |
| 512 | 308,166 | 264,811 | 0.447 | 21.177 | 99,123,200 |
| 2,048 | 1,239,824 | 1,058,504 | 0.634 | 130.561 | 225,787,904 |

These are complete synthetic sources, not production latency guarantees or a
real-data rehearsal. Actual alternatives and three-ply routes are additionally
covered by the installed two-definition scenario. Repeated activation,
transaction/lock duration and concurrent foreground measurements are retained
separately; do not confuse whole validation time with activation lock duration.

Exploratory failures are retained: test-only transaction inspector omission,
stale installed packages, a concurrent test-environment dependency removal,
transient task-owned PostgreSQL startup/pool delays, and a source-free Learning
import timeout under shared-host load. Corrections preserve selected assertions
and product budgets. Native Learning runner now refreshes actual installed test
inputs, keeps producer dev tests available, and records bounded startup readiness
and sanitized owned logs. No other owner's processes or Docker resources were
modified. Final successful results supersede only their matching input scope.

Real-data rehearsal remains unverified: no appropriate existing identified
repertoire/history export was available. General importer/history migration and
live conversion remain #265/#267 work. Broader authoring/findings UI, engines,
pilot assembly and live cutover remain with #193/#266 and their owning workstreams.
No deployment, merge, recurring import, Actions change or broad issue closure is
included. Cleanup removes only verified A-owned disposable resources after
preserving evidence, unique work and durable branch refs; branches are retained.
