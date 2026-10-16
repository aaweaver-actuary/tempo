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
`a23b8cbff5d8ec3cf680f7bb94301646546a99d1`. Final production code tested: `423b4e0877e00c210ea5bfd0673bd5bcaaf1b9dd`;
published fixture successor: `4d132d0d984b36cd027f967cd3ecb11a1fe27ae0`.
The PR head identifies the documentation-only handoff successor; it does not
change artifact qualification.

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

The successor `fixtures/installed-synthetic-delivery/` additionally contains
`comparisons[].delivery`, using the same existing outbox/receipt inspection as
the report endpoint, read within the export's repeatable-read snapshot. It carries
original delivery IDs/status/reasons/attempts and genuine acknowledgements, with
`inspection_source=game_evidence_outbox` and observed comparison revision. The
delivery-state digest participates in immutable input identity. Its input ID is
`22de0c7c92fcecc176d4fe7acd956aae66fd77ddeba86db4a0e4ea3b738b373e`,
manifest digest `924b65aa373b5305816daf5a2ddfc2fcf26640f03a15259a34048d03033c267b`.
The learner miss has one completed batch; other games are explicitly not required.
The original immutable analysis-input exports are unchanged. Repair regeneration refreshes snapshot/receipt files in both fixture folders; each frozen export continues to pin its original observation and digest, rather than being relabelled with a newer snapshot. Neither C nor another consumer creates remediation
from these serialized delivery facts.

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
background evidence only. The final recorded commands below use the component-local `.venv/bin/python`
interpreter unless the installed CLI or Node command is stated. Evidence directory
arguments are relative to that preserved evidence root in this table. Each report
also retains actual resolved command arguments, source inputs and artifact hashes.

| Command (from checkout root) | Evidence suffix | Outcome |
| --- | --- | --- |
| `services/content/.venv/bin/python services/content/integration/run_postgres.py EVIDENCE --benchmark` | `final-content-native/` | 66/66 native; benchmark complete; container/volume removed |
| `services/content/.venv/bin/python services/content/integration/qualify_artifacts.py EVIDENCE --consumers` | `final-content-artifacts/` | 107/107 pure in each of four source-removed consumers; actual v1 consumer interoperability 1/1; zero-selection exit 5 |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py EVIDENCE` | `final-learning-serial/` | 173/173 regular native, restricted container and installed/browser acceptance; all owned resources removed |
| Same native entry, explicit `test_content_bridge.py` file through supported runner function | `final-learning-capacity-refreshed/` | 9/9; measured 2,048-definition transaction/foreground and attempt compatibility |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py EVIDENCE` | `final-learning-current-artifacts/` | 3/3 installed closure/migration cases in each Python 3.12/3.14 wheel/sdist consumer; zero-selection exit 5 |
| `services/game-evidence/.venv/bin/python services/game-evidence/integration/run_postgres.py EVIDENCE` | `final-game-evidence-current-native/` | 65/65 native, including frozen export resume and immutable-table update denial; container/volume removed |
| `services/game-evidence/.venv/bin/python services/game-evidence/integration/qualify_artifacts.py EVIDENCE` | `final-game-evidence-current-artifacts/` | 61/61 pure in each Python 3.12/3.14 wheel/sdist consumer |
| `tools/analysis-restoration/a_bridge/.venv/bin/python tools/analysis-restoration/a_bridge/qualify.py EVIDENCE` | `final-operator-current-artifacts/` | 3/3 pure in each Python 3.12/3.14 wheel/sdist consumer; zero-selection exit 5 |
| `contracts/.venv/bin/python -m pytest contracts/content-publication/tests contracts/game-evidence/tests --import-mode=importlib -q --junitxml=EVIDENCE` | `final-contracts.xml` | 198/198 unchanged shared-wire cases |
| `services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_study.py EVIDENCE` | `final-production-study-linked/` | 41/41 production Home/Study browser cases; assets and owned container/volume/image cleaned |
| `node tools/component-build/check-components.mjs --component content --component learning --component game-evidence --component analysis-a-bridge` | `final-boundaries.log` | all selected owners and real dependency boundaries pass |
| Absolute `TEMPO_COMPONENT_PYTHON`, `node tools/component-build/test-lane.mjs --component analysis-a-bridge`; same owners plus `--suite learning-full-study --list` | `final-registration.json` | normal registered operator 3/3; nonzero discovery passes |
| Five installed schema commands; changed-Python compile; conflict-marker scan; `git diff --check BASE` | `final-static.json` | generated schemas match, compile passes, no conflict markers/whitespace errors |

Content native was recorded at `a23b8cbf`; its subsequent proposal provenance
validation is covered by current source-removed pure consumers and the final
installed Learning path. Production Study was recorded at `d33896c1`; later
changes affect the alias rejection/read port and have their owning native proofs.
GE/Learning final artifacts and current native source record `423b4e08`; the
complete serial Learning repeat records `4d132d0d` with identical production code.
Operator fixture artifact qualification also records `4d132d0d`. These are bounded component proofs,
not an assertion that every repository or hosted required check passed.

Selected wheel / sdist SHA256 pairs (complete dependency hashes stay in reports):

| Artifact | Wheel | Sdist |
| --- | --- | --- |
| Content | `7949f9bd56115fdb326276de5cf57f26f9f2ac75d24a14b2bb2562d1b4fd8ae5` | `736be29ec07e2c248d184b71b12f558e6000c6f7dae8beb6f34bb4f37f8c0d64` |
| Learning runtime | `45e54d57f7fd97ae24907ff793c9beb4d4499424fffbec654e46f7c36ea3fc91` | `4b3d18f61f3117d7bf32bd665544ebc7df29fa2bff0c86b07bcd07c60e7a1baf` |
| Game Evidence | `172b33603b42f808ed13400cca20d10eeb69183d2773301e057c8466dc44e9b3` | `d628b2862749b7ecba00326cc0d42e1f6c1c0a9c70b1c90774745816f6c9b579` |
| Operator | `98f0e277d92852ce6637e94d776308a1482ad3a3d5bc2432dd9ab5ef7089fc07` | `5216e6404af5d1a3b373bc13685914b803ffc1f1ec3183bbc8d11befe7855f29` |

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
covered by the installed two-definition scenario. The final repeated bundle-v2 measurements at 129/512/2,048 definitions were
respectively 120,457 / 478,009 / 1,914,939 bytes, staging 0.065 / 0.146 / 0.641 s,
and three activation requests each at 0.078–0.098 / 0.214–0.259 / 0.793–1.024 s.
The 2,048-definition metadata successor's measured writer section (including pool
acquisition/commit) was 0.638 s; the publication lock held 0.629 s. Forty-five
concurrent foreground bootstrap reads took 0.004–0.338 s (median 0.008 s); the
predecessor-issued attempt saved with HTTP 200. Process RSS was 232,456,192 bytes
for the repeated-publication driver. These measurements retain unchanged budgets.

The separate Content benchmark covers 512 routes up to 64 plies, 2,048 explanation
definitions, 33,127 route occurrences, 15,793 opponent positions and 494,581 legal
replies. Its 4,604 inventory slices had median 0.020 s / maximum 0.659 s; recorded
writer transactions had median 0.003 s / maximum 0.619 s; publication 0.728 s;
peak driver RSS 88,178,688 bytes. This validates Content's larger legal inventory,
not opening-v2 activation of explanation definitions. Full samples stay in
`final-content-native/benchmark.json` and `final-learning-current/…/bridge/`.
Do not confuse whole validation time with activation lock duration.

Exploratory failures are retained: test-only transaction inspector omission,
stale installed packages, a concurrent test-environment dependency removal,
transient task-owned PostgreSQL startup/pool delays, and a source-free Learning
import timeout under shared-host load. Corrections preserve selected assertions
and product budgets. Native Learning runner now refreshes actual installed test
inputs, keeps producer dev tests available, and records bounded startup readiness
and sanitized owned logs. No other owner's processes or Docker resources were
modified. Final successful results supersede only their matching input scope. In
`final-learning-current/`, 172/173 passed; the existing recreated-client
remediation-cursor regression exceeded its unchanged 250 ms background budget
at commit. The complete suite was repeated serially in `final-learning-serial/`;
that repeat passed all 173 in 274.22 s, without changing the selected test or
budgets. Both reports and their cleanup receipts are retained.
The new provider JSON timestamp regression is fixed and covered by all 61 pure
artifact cases and 65 native cases. Test-only fixture corrections and missing
immutable-table grants are retained in the exploratory reports. The first Study
build lacked local workspace links; the locked offline install restored the three
declared workspaces and the repeated production acceptance passed. No unrelated
processes were stopped to obtain these results.

Real-data rehearsal remains unverified: no appropriate existing identified
repertoire/history export was available. General importer/history migration and
live conversion remain #265/#267 work. Broader authoring/findings UI, engines,
pilot assembly and live cutover remain with #193/#266 and their owning workstreams.
No deployment, merge, recurring import, Actions change or broad issue closure is
included. Cleanup removes only verified A-owned disposable resources after
preserving evidence, unique work and durable branch refs; branches are retained. Each native report records verified resource removal.
The final worktree/ignored-file/stash/process audit and checkout archival receipt
are preserved as `cleanup.json` outside the checkout; the pre-existing main stash
is unrelated and remains untouched.

## PR #294 correctness repair

The repair branch starts at `1db44cbdf310f959eb0c839e94166350531cc89b` on base `0edf566ad48f51846a43d3341809379ff0469ada`. The original validation and its recorded failures above remain historical evidence. Fresh repair evidence is maintained separately under `test-results/pr294-correctness-20261016/`; the complete [A–K repair and qualification report](a-bridge-repair.md) records exact commands, revisions, artifact hashes, retained failures and recovery limits.

### Terminal rejection and corrected approval

The operator now reconciles a persisted terminal Learning rejection into Content `failed`; its JSON reports that state and exits 2. Content independently retrieves and verifies the original receipt before the source/operation transaction. The operator-only Learning receipt includes workspace, command and exact persisted publication request. Failure records retain delivery identity, a receipt fingerprint and sanitized error fields, without raw remote messages or credentials.

```sh
tempo-content operation record-failure OPERATION_ID \
  --learning-origin https://learning.example \
  --operator-token-file /private/operator-token
tempo-content operation show OPERATION_ID
tempo-content source show SOURCE_KEY
```

After failure, plan the corrected complete source using the actual returned `requested_revision`, approve its new draft, and apply that distinct operation. A correction affecting a previously staged bundle must create a new Content publication generation: immutable Learning staging cannot be overwritten. Content never decrements a source revision. Previous Learning serving content remains until a valid successor/retirement commits. Failure replay remains idempotent even after the successor reserves the source; stale success/retry cannot change the failed operation.

Unknown/queued outcomes, malformed responses, authentication/transport errors and timeouts retain original authority and reservation. Stage-only errors have no durable Learning rejection receipt and therefore remain an explicit recovery boundary: restore/reconcile the original authority and staging inputs; do not infer cancellation or terminal failure from HTTP status.

### Snapshot observations and historical aliases

`snapshot_id` is `a-snapshot-v1:` plus SHA256 of UTF-8 compact JSON `["tempo-a-snapshot",1,workspace_id,publication_id,represented_generation]` (`ensure_ascii=False`). Revision is still the authenticated current-serving activation revision. Checkpoints use `snapshot_id:revision`, leaving old numeric-key checkpoints inspectable. Repeated requests return the persisted document. Concurrent requests recover the winning checkpoint only when all content/authority facts match; observation timestamp/local-date differences do not create another identity. Immutable snapshot conflicts remain errors.

Historical-first loading can promote a valid successor; current-first loading rejects stale promotion. Post-retirement observations retain historical activation but cannot claim current serving authority. Study resolves publication-scoped aliases while retaining physical Learning identities, history, FSRS and executable revision checks.

### Draft/proposal durability

Append-only Content migration `0004_bridge_proposal_identity` enforces proposal identity across sessions/processes. Pre-existing duplicate identities abort migration without deleting drafts. Identical replay returns the original durable draft; conflicting proposal bytes fail specifically. Complete immutable draft/provenance digests must match on semantic-ID replay. Stable Content/card identity does not authorize substituting an old raw-file provenance record.

New regressions are listed in each component's local inventory. Shared Content Publication v1 and Game Evidence v1/v2 contracts, frozen introductions, normal allowances and all original installed bridge assertions are preserved.
