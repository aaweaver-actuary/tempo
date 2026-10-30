# Learning runtime regression ownership

`tests/test_decision_projection.py::test_known_projected_binding_accepts_counter_only_fen_variants_without_rewriting_raw_facts`
requires the same explicitly identified exercise/binding to accept normalized
legal-position equality despite different FEN clocks. Raw full route/definition
FEN strings and immutable digests remain distinct; input is not rewritten.
The adjacent position/path/color/unknown-card and legal-en-passant controls keep
piece placement, turn, castling, current legal-EP normalization, legal moves and
explicit binding identity strict. Equal FEN alone never establishes identity.


`test_complete_9612_definition_publication_fits_public_ceiling_without_truncation`
and `test_complete_10001_definition_publication_rejected_without_truncation` in
`test_content_bridge.py` align the existing V2 facts limit to the public
Publication definition ceiling of 10,000. The complete constructed within-limit
input retains every definition/fact; oversized input fails. The existing
`test_C3_publication_bundle_capacity_is_bounded_after_default_expansion` retains
the independent 12 MiB expanded-payload bound. These pure model checks do not
claim target activation throughput or real graph acceptance.

## Accepted repertoire display titles

`tests/test_decision_projection.py::test_optional_repertoire_title_absent_preserves_prior_route_bytes`
retains the pre-extension complete normalized-bundle digest. The adjacent bounded
nonblank/consistent-title cases and
`test_titled_unbound_foreign_route_cannot_escape_membership_ownership_locks`
require every explicit name write to belong to accepted fact memberships while
retaining old untitled extra-route behavior.
`tests/test_publication_storage_separation.py::test_title_projection_uses_existing_locked_activation_writer`
retains the existing lock/transaction writer; the adjacent public graph-digest
case keeps display titles out of routing identity while full acceptance binds them.

The owning real PostgreSQL `tests/test_content_bridge.py` cases are
`test_fresh_opening_activation_preserves_repertoire_title_and_receipt_replay`,
`test_title_absent_activation_preserves_existing_repertoire_metadata`,
`test_conflicting_repertoire_titles_fail_before_staging_or_writes`,
`test_repertoire_title_update_rolls_back_with_activation_failure`, and
`test_repertoire_title_storage_cannot_update_outside_declared_memberships`.
They cover exact new names, immutable staging conflicts, old receipt no-ops after
a newer title, legacy metadata preservation, rejected inconsistent titles,
same-transaction rollback and defensive writer scope. Pure tests alone do not
qualify these storage effects or live activation.

## Balanced daily presentation

`tests/test_study_ui_harness.py::test_native_study_harness_initializes_fixture_key_before_starting_servers`
executes the production Study harness setup through fixture-key creation twice,
before any server or database starts, and requires distinct UUIDv4 keys. It
guards the harness's missing `uuid` import; actual UI and durable review effects
remain owned by `integration/study_tests/test_study_ui.py`.

`test_queue_ordering.py` owns pure policy-v3 proportions across skewed, empty,
single and multiple categories; identical membership under different seeds;
stable replay; frozen opening relative rank across category slots; protected
positions; stable urgent priority; versioned fingerprints and preparation fences.
The named fresh-urgent-cycle and pre-mutation provenance cases distinguish new
obligations from positioned retries and keep replacement ranks/unmixed additions
dirty until actual presentation. Verified controlled edits retain the sequence.
`test_queue_presentation.py::test_runtime_balances_admitted_work_and_replays_without_moving_issued_head`
owns actual admission/refresh/reload through the independent runtime and restricted
PostgreSQL. Existing `test_eligibility.py`, `test_families.py` and
`test_remediation.py` remain the owners of competing issuance, quota/prerequisite
gates, typed reviews, retry/reinforcement, remediation authority and exactly-once
effects. This slice changes presentation only; it does not adopt soft cooldowns.

`tests/test_decision_projection.py` owns explicit bound decision offsets, exact
position/color and identity validation, unchanged full executables/aliases, and
old binding byte compatibility. The installed three-service regression
`test_C3_original_opponent_prelude_card_receives_interior_miss_and_settles_once`
owns original physical identity/history preservation, an interior game miss,
post-issuance evidence fencing, subsequent Study settlement and exact replay.
It is a labeled constructed compatibility regression, not real-game acceptance.

`test_C3_complete_historical_alias_activation_fits_documented_lock_capacity`
uses 6,524 constructed existing physical cards and 6,524 distinct Content aliases
through real v2 activation, preserving a genuine saved review and every schedule.
It retains both card-edit lock namespaces and verifies receipt replay. The owned
native PostgreSQL startup declares and records shared lock capacity; it does not
change production settings or increase the transaction timeout.

`test_C3_copied_review_identity_advances_without_reusing_or_reordering_history`
reproduces an actual sequence collision after explicit copied review IDs, repairs
it through the Learning-owned rehearsal helper, preserves the original review,
and saves the next ordinary Study review with a new ID. Its second case requires
an already advanced sequence to remain ahead. Empty-rehearsal restoration now
finishes review identity allocation; explicit repair refuses changed copied
history and remains restricted to verified disposable C3 storage.

## A authoritative bridge

`test_content_bridge.py` owns measured complete 129/512/2,048-definition activation
and replacement, original receipt replay and credential separation, genuine
review/schedule retention through explicit aliases and metadata successors,
identity conflict rollback, historical status versus replacement, and retirement.
Accepted historical aliases cannot be silently dropped by a successor. The
2,048-definition metadata successor test measures actual writer/lock durations,
concurrent foreground reads and a predecessor-issued attempt saving after commit.
`test_authoritative_bridge.py` owns actual installed Content import/approval,
authenticated operator activation with real response loss/restart, installed
producer delivery to the existing receiver, late-evidence and qualifying Study
settlement, immutable offline export/replay, proposal draft versus approval and
application, three-domain role denial, and complete authored-source capacity.
These tests are discovered by the regular native runner; its dependency closure
now records Content and the database-free operator tool as test inputs only.

The complete native inventory is discovered by `../../../database/tempo_learning_database/tooling/run_postgres.py` from `tests/`; collection, every setup/call/teardown phase and JUnit must agree. No selection flags or skips are accepted. `components.json` registers the owning native suite and its real client/runner inputs. Installation checks also run in source-removed Python consumers through `qualify.py`.

| Regular file | Proof |
| --- | --- |
| `test_host_activity.py` | Independent session expiry and remaining quiet time; pending essential request holds; own-producer cleanup; authenticated/origin-scoped activity without PostgreSQL; optional filesystem failure retains evidence and no broker dependency |
| `test_runtime.py` | Load/issue/review, immutable logical and transport replay, scope/origin/role denial, authoritative concurrent first enqueue |
| `test_transactions.py` | Opening evidence atomicity; handler and handled-reconciliation rollback; competing payloads; bounded current-card contention; nonblocking optional successor; stale/deleted/retired definitions; retained quota; finite-contention maintenance; local-day boundary |
| `test_queue_persistence.py` | Complete owner card/count/projection reads, legacy wire whitelist, review identities, defense/Study source facts, bounded windows, semantic HTTP adoption/head issuance conflicts and daily-all/remediation-all/projection-last review refresh ordering. These infrastructure-free checks do not establish native query/locking/rollback behavior; `test_queue_presentation.py` retains actual PostgreSQL ownership. |
| `test_review_policy.py` | `test_review_policy_reads_borrowed_settings_without_nested_connection` forbids nested connection acquisition and proves settings come from the borrowed command transaction; `test_review_policy_preserves_injected_provider_without_database_read` retains injected policy semantics. These infrastructure-free adapter checks do not establish native locking/rollback behavior. |
| `test_families.py` | All five typed specifications, practice/review/self-assessment/feedback; tactical discovery and binding; defense recognition, covered grading and uncovered durable intent |
| `test_eligibility.py` | Parent exposure, integrity fences, competing issuance, middlegame review, guided typed attempts/sibling burial, local evidence promotion and required settlement |
| `test_endgames.py` | Native full-retirement migration, rollback and ordinary-review preservation |
| `test_endgame_calculation_removed.py` | ASGI removal before database/provider, saved immutable receipt recovery, legacy trigger and template binding, absence of optional worker/pool/provider composition |
| `test_publications.py` | Immutable staging, completeness and latest-generation recheck; accepted frozen ranks; migration/workspace/privileged-role readiness; unique route positions and memberships; every-line learner-turn validation for both colors; exact multi-membership graph completeness and rollback; competing owners, retirement/replacement locking (including removed memberships), independent repertoires and retryable owner contention |
| `test_evidence_revisions.py` | Monotonic revisions, original transport replay, semantic retries without re-promoting settled misses, conflicting content, empty corrections, transaction-wide rollback, both concurrent revision orders, installed HTTP process restart, historical receipt migration and restricted-role access, custom receiver authority; actual PostgreSQL cross-evidence obligation/delivery identity races and loser rollback |
| `test_enqueue.py` | Barrier-controlled creator rollback with waiting insertion; existing leased task replacement; authoritative generations/event identities |
| `test_restart.py` | Installed HTTP process restart, response loss, durable intent resumption and task-owned database restart |
| `test_browser.py` | Real Chromium with existing strict schemas/outbox; progression and lost-response reload recovery against the real Learning API |
| `test_container.py` | Final restricted-user image, actual PostgreSQL load/issue/review/replay and absence of backend/broker/model source |
| `test_installation.py` | Installed closure, packaged append-only migration dependency/resources, forbidden optional imports, transaction owner and denied-auth startup without infrastructure |

Legacy compatibility adapters retain the regular scheduling application/package/boundary and defensive grading/validation/detection suites. Scheduling ownership metadata records the packaged owner. The retained enqueue primitive changes its returned row/event identity to PostgreSQL's `RETURNING` result; `backend/tests/test_durable_task_enqueue_identity.py` exercises the retained consumer's event/result binding, while native concurrency evidence lives in Learning's regular suite rather than a parallel harness. Shared runner normalization has a named regression in the existing component-build boundary suite.

## F1 adoption

`test_remediation.py` owns the integrated v2 intake/borrowed-row-factory proof;
issuance-only frontier binding and reload reuse; qualifying settlement and logical
review replay; late/unknown-time evidence requiring a fresh attempt; immutable
corrections, historical semantic replay and exact withdrawals; validity/lifecycle
fences; guided/failed nonsettlement; required-settlement rollback (including handled
reconciliation); superseded/expired lease rollback; zero-cap locked-card
introduction; protected positions and unchanged frozen inputs; concurrent delivery;
both actual review/evidence commit orders; acceptance/receipt/work-request rollback;
trusted alternatives/position/alias rejection; ordinary-work fairness; and stable
library miss order across single-card maintenance slices.
Prepared-continuation coverage asserts that prefetch has no frontier, the actual
receipt-authorized issuance binds it once, reload/replay retains it, and neither
review submission nor an old already-issued origin can invent a missing binding.

`test_game_evidence_runtime.py` owns actual installed producer CLI -> authenticated
Learning HTTP -> separate PostgreSQL database integration. A real forwarding proxy
drops the acknowledgement after Learning commits; a new producer process replays
after Learning restarts. Role-denial checks prove the runtime identities cannot
connect to the other domain database. Its Chromium proof stops the producer and
Learning, restarts only Learning, settles remediation with real response-loss/reload
recovery, then follows existing reinforcement and saves ordinary newly introduced
work. No optional provider/model/broker is started, and browser traffic to any
other origin is rejected. These are focused client/API proofs, not the complete
Study UI switch owned by F2.

The existing `test_evidence_revisions.py` cases now exercise the same remediation
authority using coherent accepted v1 observations. Their original monotonicity,
global identity-race, rollback, migration, restricted-role and transport replay
invariants remain; old raw-obligation/frontier writes are replaced by equivalent
repository assertions. Historical v1 receipts remain a read-only replay boundary.
No selected check is skipped or weakened. Existing `test_eligibility.py` promotion
and settlement now observes the durable maintenance boundary, rather than assuming
intake performs synchronous admission. The native browser harness awaits #285's
durable enqueue and compiles the two unchanged Study package public entries using
its local TypeScript dependency; no root frontend install is required.

## PR #287 recovery repairs

The regular native `test_remediation.py` inventory adds:

- `test_remediation_refresh_preserves_active_cursor_and_lease`: queued, leased and
  retrying states through the real queue-refresh caller; cursor, lease, generation,
  ready ordering, attempts, other payload fields and database-returned identities.
- `test_repeated_refreshes_do_not_starve_later_remediation_card`: four retained
  non-actionable identities and one later valid miss; continuing requests between
  pairs of claims, at most five successful remediation slices, and ordinary work.
- `test_remediation_arrival_behind_cursor_is_found_by_followup_pass`: continuing
  and recreated clients. `recreated-client` owns restart/cursor/rescan preservation.
- `test_remediation_rescan_racing_with_final_slice_is_not_lost`: both serialization
  orders, actual PostgreSQL blocking and committed-state verification.
- `test_remediation_day_change_fences_old_slice`,
  `test_remediation_coalesced_request_rolls_back_with_parent`, and
  `test_remediation_failed_slice_release_preserves_concurrent_rescan`: day fencing,
  atomic request rollback and failed-owner release.

`test_evidence_revisions.py` adds `test_stale_v1_delivery_receipt_survives_reconnect`,
`test_stale_v1_delivery_id_cannot_be_reused_with_newer_payload`,
`test_historical_frontier_stale_delivery_binds_transport_identity` (both wires),
`test_concurrent_stale_delivery_identity_reuse_is_serialized` (both commit orders,
independent source identities without shared obligation locks),
`test_stale_receipt_failure_rolls_back_without_acceptance_effects`, and
`test_v2_historical_semantic_replay_is_unchanged_by_v1_stale_policy`.

`test_installed_producer_auth_recovery_accepts_once_in_independent_learning` in
`test_game_evidence_runtime.py` uses actual installed CLI processes, authenticated
Learning HTTP and separate disposable databases. Correcting the private token and
restarting retries the original delivery and produces one logical acceptance.

`test_installed_receipt_policy_replays_stale_identity_without_content_or_writes`
in `test_installation.py` checks optional installed receipt arguments, immutable
replay and digest conflict in all four wheel/sdist consumers using a receipt-read
port double. Native suites separately own actual persistence and concurrency.

`test_review_task_locks_follow_prepared_continuation_issuance` observes actual
PostgreSQL task-table lock ownership at continuation binding.
`test_stale_content_receipt_binds_identity_without_acceptance` additionally covers
durable rejected-content identity and acceptance only under a fresh transport.

## F2 actual Study adoption

Post-merge P3 adds `bootstrap-body-interruption-browser-online` to the existing
`test_production_study_learning_native` inventory. The forwarder sends partial
bytes from a real authenticated HTTP-200 bootstrap with its full Content-Length.
Browser observers prove native fetch resolved and body consumption began before
the connection is cut. With browser online, real cached issued-head practice stays
local until the original command receives Learning confirmation, its offline
record is acknowledged, and PostgreSQL contains exactly one review. The existing
request allowlist and disposable-resource cleanup remain authoritative.

`test_study_bootstrap_teaching.py` owns strict settings/bootstrap/progress,
unsupported patches before acceptance, original teaching time and transport
identity, concurrent incremental facts, lifecycle/revision/deletion/learner-turn
fences, historical reads/replay, every accepted opening alternative, malformed
chess input and retired-route absence. `test_families.py` additionally
asserts the accepted defense candidate identity in the issued queue.

`integration/study_tests/test_study_ui.py` is registered as `learning-full-study`.
It owns production Home/Study native board/control interaction against installed
Learning and real restricted PostgreSQL: progression, teaching/reload, guided
failure, original-command loss recovery, saved-review/next-load separation, two
tabs, denied/quota/aborted/incompatible storage, old authority, actual cached-shell
offline answer/reconnect, all five typed specifications, tactics/middlegame,
defense covered/uncovered, settings and auth/CORS denial.
Real installed producer delivery stops before Study settlement; late delivery
cannot settle from the already issued attempt. Task-owned Learning and database
interruptions recover unchanged commands. Forwarding faults never invent essential
responses. These tests supplement rather than replace F1 and durable-session proofs.

Regular frontend ownership is in `tests/REGRESSIONS.md` and
`tools/component-build/test-inventory.json`; connection and delivery, connected
snapshot grading, matching teaching/operation receipts and existing queue/claim/
older-writer/cancellation tests remain in their normal suites.

## PR #291 Study review repair acceptance

The existing `learning-full-study` suite adds `service-unavailable-browser-online`,
`multi-authority-resume`, `registered-authority-recovery`, and
`study-authoring-boundary`. Transport loss preserves an actual issued head and its
original offline answer until a real Learning receipt and one PostgreSQL review
confirm it. Two pages share one browser context/frontend storage and use separate
session bindings and forwarding origins. Real settings commands commit with lost
replies, then reconcile only through their original origins; an unrelated C is
blocked. The forwarding origins share one installed Learning database, so this
proves origin/storage isolation and actual receipts, not separate-database isolation.
Existing F1, restart, storage, exercise-family and offline-shell cases remain selected.

The authenticated activity endpoint regression verifies its stable workspace source identity; managed admission separately requires configured expected identities even before their first successful publication.
## PR #294 repair regressions

- `tests/test_content_bridge.py::test_A_historical_alias_move_membership_accepts_new_alternative`: genuine prior review, retained physical ID/schedule/history, accepted original and new alternative, legal unaccepted move and obsolete executable revision. Existing activation compatibility/retirement regressions remain selected.
- `tests/test_authoritative_bridge.py::test_A_corrected_source_activates_after_original_terminal_failure`: installed Content/operator, actual failed Learning receipt, restart, source release, corrected revision and immutable failed-operation retry.
- `test_A_actual_evidence_delivery_export_proposal_and_optional_off_study` additionally loads historical then current snapshots into a separate fresh installed producer; the original producer still proves current-then-historical rejection without current-pointer mutation. Post-retirement observations are genuine HTTP-produced fixtures.

The complete regular native and production Study suites retain credential separation, issuance, review, retirement, optional-services-off and resource-budget owners.

The installed bridge proof also loads the real post-retirement generation-2 observation, recomputes evidence, and requires unknown readiness with unchanged delivery receipts; response loss explicitly retains Content applying authority.

`test_A_cli_timeout_terminates_owned_descendants` prevents a timed-out installed operator from leaving a Content child connected to the disposable database. Large installed-source cases retain the original 10,000 total slices and 300-second child limit, using 64-slice resumable invocations; total duration and invocation counts are reported without changing production budgets.

The installed bridge uses a two-party barrier after genuine Learning status reads to race two installed operators against actual Content snapshot checkpoints; both must return exactly the same persisted observation. The alias regression saves its exact before/after review and FSRS tuples in `bridge/alias-membership.json` for audit.

## PR #294 publication fencing

Regular `tests/test_content_bridge.py` owns:

- `test_A_abort_before_activation_fences_original_identity`: absent/queued
  activation and retirement, terminal replay, retry, selected gateway work and
  maintenance with unchanged serving effects.
- `test_A_activation_before_abort_preserves_real_completion`: genuine completion
  and original failed activation remain immutable.
- `test_A_publication_abort_rejects_conflicting_request`,
  `test_A_publication_abort_requires_operator_credential`, and
  `test_A_publication_transport_key_cannot_bypass_fenced_identity`: exact family,
  workspace/request/ID binding and Study/evidence credential denial.
- `test_A_publication_abort_is_idempotent_after_restart` and
  `test_A_publication_abort_serializes_with_activation`: durable receipt equality,
  both lock winners and queued retry authority using separate sessions.

Regular installed `tests/test_authoritative_bridge.py` owns
`test_A_installed_pre_activation_recovery` (unsupported source, impossible initial
retirement, real stage conflict, lost stage/abort response),
`test_A_installed_preflight_preserves_previous_serving_authority` (active predecessor,
already retired source, retirement changing after preflight), and
`test_A_installed_concurrent_operators_preserve_one_authority` (two installed apply
or abandon commands). All use actual restricted databases, installed CLIs and
Learning HTTP authority, including corrected approval and stale-operation denial.

`test_A_selected_maintenance_cannot_resurrect_terminally_fenced_retirement` pauses
actual maintenance after selecting a queued retirement and before execution;
fencing wins before it resumes, preserving active cards and the original revision.
Abort/activation race parameters use the actual activation and retry HTTP routes.
`test_A_publication_fence_measured_under_existing_budgets` retains API and owner
transaction timings without changing production budgets or asserting a host SLO.
`test_A_installed_completion_cannot_substitute_checkpointed_request` verifies a real
wrong-publication completion cannot release the original reservation.

The installed conflicting-authority regression also creates a genuine queued
wrong-publication retirement by timing out execution behind its publication lock.
Neither reconcile nor abandon may retry it or change either publication. The
completed variant independently exercises Content's authenticated record CLI.

Concurrent installed operator parameters cover both activation and retirement:
two actual apply processes share one retirement outcome; two abandon processes
fence a genuinely queued retirement without changing the active predecessor.

`test_host_activity_reporting_thread_failure_does_not_prevent_service_startup` proves an unavailable optional renewal thread leaves authenticated service startup and essential in-memory request accounting usable while no optional snapshot is claimed. `test_host_activity_shutdown_reporting_failure_is_diagnostic_and_retains_owned_snapshot` proves cleanup reporting cannot escape shutdown or erase uncertain ownership. Both use injected standard-library failures and remain in the regular native Learning suite.

`test_host_activity_stale_short_pulse_cannot_shorten_a_fresh_held_session` keeps a renewed same-session deadline monotonic when an older short pulse arrives late. The legacy native priority proof's `test_postgres_priority_monotonic_activity_renewal` executes the actual Redis Lua and checks both per-member deadlines and the shared key lifetime against a longer pending foreground lease.


`test_host_activity_clock_retains_reader_domain_after_host_sleep` protects the shared-host clock contract. macOS Python uptime excludes sleep while the native Node reader includes it; the producer uses `CLOCK_MONOTONIC_RAW` on macOS and the ordinary monotonic clock elsewhere. The regression simulates a suspend offset without requiring Node, a broker, or a database for Learning progress. Real Python 3.12/3.14 and Node clock brackets are retained in the task's qualification evidence.


The full Endgames retirement supersedes the earlier calculation-only removal.
`test_endgames.py` rejects new retired-family publications, proves missing routes
leave ordinary review usable, and exercises both owner migrations against
PostgreSQL, including idempotency, exact unrelated-state preservation, recreation
fences and mixed-ownership rollback. `test_endgame_calculation_removed.py` proves
missing routes without a database/provider and absent handlers/configuration.
`test_pre_retirement_v1_stage_replay_preserves_digest` and
`test_pre_retirement_v1_staged_publication_activates_and_replays` cover ordinary
opening/Tactics historical explicit-null facts, exact V1 byte identity, pending
activation and idempotent replay. `test_pre_retirement_v2_stage_replay_preserves_digest`
and `test_pre_retirement_v2_staged_publication_activates_and_replays` cover the
same contract for V2 opening canonical identity. Historical fixtures are built
independently of the current execution-fact serializer. Both activation cases
start with directly stored historical bytes and prove one materialized card,
one receipt, the completed original operation, and unchanged bytes/digest.
`test_retired_endgame_v2_publications_cannot_create_new_cards` and
`test_retired_endgame_nonnull_metadata_rejected_for_ordinary_publications`
preserve rejection of actual Endgames and even empty non-null metadata in V1
opening/Tactics and V2 openings, alongside the existing V1 retired-content test.
The registered filenames remain stable so regular-suite discovery retains the
replacement retirement proofs. Historical saved-practice receipts below describe
the earlier implementation, not current behavior.


## Database file separation: frozen Study facts and guards

`test_frozen_order_policy.py` owns the extracted policy's source import boundary, inactive/publication annotation, changed-definition/legacy/route distinctions, stale-token and mandatory-miss capabilities, protected relative slots, generator input, and borrowed-transaction deletion/definition visibility and rollback. Tests use supplied facts or in-memory SQLite only; they do not qualify native PostgreSQL. Existing queue-ordering and installed deferred-import/authentication checks remain the owners of presentation and independently installed closure. The SQL literals across the three original/moved implementations were compared once for relocation evidence, not permanently pinned by policy tests.


## Database file separation: Study recovery, bootstrap and teaching

`test_study_command_storage_separation.py` owns missing/ambiguous recovery identity rejection, retained-completion transaction rollback, exact failure-marker definition fencing, settings alias/boolean encoding and essential-enqueue order, supplied accepted-route chess verification, and learner-turn teaching rejection before a write. These ten cases use semantic fake operations or in-memory SQLite; existing native `test_study_bootstrap_teaching.py` and `test_transactions.py` retain PostgreSQL ownership and were not run for this mechanical split. SQL literals were checked once for relocation evidence, not frozen as policy-test expectations.

## Database file separation: essential work and evidence receipt facts

`test_essential_work_storage_separation.py` owns the extracted composition's essential-kind admission, same-day queued/leased/retrying rescan without cursor/lease reset, stale-day no-op, completed-sweep reset, late rescan merge, both completion lease fences, application rollback before lease release, retained-operation deferral, task completion before projection publication, workspace denial, historical replay/conflict before current authority and accepted-frontier/custom-receiver context. These eighteen semantic fake-operation cases open no database connection. Existing `test_enqueue.py`, `test_remediation.py`, `test_evidence_revisions.py` and `test_transactions.py` retain native PostgreSQL concurrency/transaction proof and were not run for this mechanical split. Original SQL literals and the intact claim function were checked once for relocation evidence, not pinned in policy tests. Installed deferred-import/denied-authentication still guards independent package closure.

## Database file separation: Study exercise and tactic attempts

`test_attempt_storage_separation.py` owns practice completion without scheduling, the three guided-rating sources and retained requested rating, requeue/completion/sibling-effect order, changed-definition rejection before scheduling, Study identity replay/conflict before current-definition reads, pending/automatic answer persistence and refresh order, card/position/queue locks before answer/latest-review fences, self-assessment retirement/revision/replay fences, and tactic receipt replay/light due-date/normal-mode/guided-queue decisions. These twenty-one semantic fake-operation cases use no database. Existing `test_families.py`, `test_eligibility.py` and `test_transactions.py` retain actual PostgreSQL family, idempotency, concurrency, sibling and transaction proof and were not run for this split. A one-time comparison of original/new paths checked unchanged results, SQL order and bound values; policy tests do not parse or pin SQL. Installed deferred-import/denied-authentication remains the independent artifact guard.

## Database file separation: foreground queue operations

`test_queue_storage_separation.py` owns supplied-fact presentation labels/eligibility, tail/positioned/fallback requeue provenance, unavailable and retained-issued-attempt fences, wrong-day denial, first-head binding before issuance marker, failed-marker conflict, burial lock/provenance order, invalid refresh dates, ready projection no-op, active same-day coalescing and completed/other-day refresh. These twenty semantic cases use fake named storage operations and no connection. Existing eleven `test_queue_persistence.py` cases retain bounded typed/legacy wire projection, original latest-review/source facts, borrowed refresh ordering and HTTP head-issuance behavior. One-time original/new comparisons retain exact query/parameter/capability order, including synthetic driver selection; they do not establish PostgreSQL races. Native `test_enqueue.py`, `test_eligibility.py`, `test_queue_presentation.py` and `test_transactions.py` remain the actual concurrency/transaction owners and were not run for this mechanical split.

## Database file separation: review service and reconciliation

`test_review_storage_separation.py` owns empty/replayed defense scheduling, prior-state/maturity inputs, idempotent early return, required settlement/follow-up identity and unlock order, immutable receipt replay/conflict before current-state reads, changed-revision history-only storage, per-event snapshot/scheduling/interval/card/receipt order, missing-time conservative fallback, and time/identity cutoff plus baseline-seed history. These twelve semantic cases open no database. A one-time original/new comparison additionally checks protected queue slots/priority projection, ordinary requeue/provenance, exact SQL/parameter/capability order and clock-read placement. Existing native family, eligibility and transaction suites retain actual PostgreSQL race/lock/rollback ownership and were not run for this mechanical split. Installed deferred-import/denied-authentication remains the package closure guard.

## Database file separation: foreground admission and review adapter

`test_admission_review_storage_separation.py` owns inactive/deleted admission rejection, family quota/write order, miss proposal and protected-attempt fences, frozen opening rejection, sorted current/optional lock selection after optional denial, archived-card serving-read short circuit, completion evidence revision authority, identified/legacy completion provenance and recovered-origin-only persistence. These eighteen fake-operation cases complement existing review-policy borrowed/injected settings and queue-persistence/deferred-effect proofs; no database is opened. A one-time original/current method comparison additionally covers exact SQL/parameter/capability order, driver-selected optional lock behavior, DTOs, deferred state and clocks. Native eligibility/transaction suites retain actual PostgreSQL concurrency/lock/rollback ownership and were not run for this mechanical split.

## Database separation: command receipts and remediation integration

`tests/test_gateway_remediation_storage_separation.py` owns seventeen infrastructure-free boundary cases: credential rejection before handler/savepoint, terminal receipt idempotency, immutable intent/logical request identity, handled-conflict savepoint rollback versus driver-error outer rollback, original borrowed tuple-row cursor/info behavior, accepted-definition workspace/unique-projection/alias authority, immutable issuance time and binding, required issued origin, and settlement → priority clearing → current miss → provided/default follow-up order. Complete SQL/driver operations are in `database/tempo_learning_database/adapters/{gateway,remediation_adapter}.py`; handler/authority/settlement/clock/lifecycle decisions remain in runtime. The existing remediation repository writer and existing direct integration reads are retained; this does not establish new private-table isolation. Native runtime/transaction/publication/remediation suites retain actual PostgreSQL savepoint, rollback, contention and settlement ownership; those suites were not run for this relocation. Four baseline mixed files and the initially moved adapter policy audit remain unfinished.

## Database separation: accepted publication execution

`tests/test_publication_storage_separation.py` owns twenty infrastructure-free decision/composition cases: workspace/immutable/stale staging before writes; activation receipt replay or conflict before serving locks; prepared-digest rejection before repertoire/card locks; historical alias/adoption/revision rejection before writes; accepted physical identity through alias/card/graph writes and receipt-before-follow-up order; retirement revision gates and exact effect order; sorted removed/new repertoire owner locks; executable graph comparison beyond counts; and read-close → model validation → dispatch with prepared-context reset on failure. Five existing `test_decision_projection.py` cases retain model/route/projection/alias and prior serialized binding behavior. Root adapters `publications.py` / `publication_bridge.py` own complete fixed queries, existing lock operations and scalar/set-based writes, with no new writer or generic SQL port. Native publication/content-bridge/transaction suites retain actual PostgreSQL rollback, ownership contention, history preservation, restart and lease-fence ownership; these were not run for this relocation. API/defense baseline extraction and initially moved adapter policy classification remain unfinished.

## Database separation: HTTP projections and defense practice

`tests/test_api_defense_storage_separation.py` owns twenty infrastructure-free cases: handled reconciliation savepoint order; absence of retired Endgames handlers; actual/expected migration readiness mapping; queue entry day/status/head mapping without SQL-fragment consumption; missing evidence rejection before the second event-read scope; safe exercise presentation and committed feedback scope; prepared v2 publication retry; defense card → queue lock → current/replay ordering; missing queue/replay gates before clocks; original scheduling/repeat/order/receipt sequence; rubric rejection before database access; and recognition evidence recheck before answer persistence. Eleven existing `test_queue_persistence.py` checks retain queue/HTTP/review refresh composition proof. Native runtime/transaction/family suites retain actual PostgreSQL rollback, locking, graded recognition and atomic queue/review/attempt ownership; they were not run for this relocation. All original Learning mixed-file paths are separated; initial-adapter application policy classification and any necessary extraction remain unfinished.

## Database separation: opening-evidence application policy

`tests/test_opening_evidence_storage_separation.py` owns nineteen infrastructure-free cases: actual read-close before manifest validation; immutable color/fallback; no-opening fast path; original resource cleanup on missing queue identity; ambiguous/missing snapshot diagnosis without blocking ordinary review; immutable context/terminal/event identity; advisory identity → late deletion gate → current scope ordering; completed aggregate replay authority; aggregate-persistence gate; identical source-staleness driver failure before writes; borrowed standalone publication order; observation delta/idempotency/distinct clean-day behavior; bounded response pagination; and validation-to-HTTP conflict translation before persistence. Existing queue projection/composition and review policy cases retain API/review consumer coverage. Six pure helpers/DTO move unchanged into `src/tempo_learning_runtime/opening_evidence/`; the root adapter owns fixed queries, driver identities and original managed read sections. Native transaction/opening-evidence suites retain actual PostgreSQL atomic aggregate review/history/evidence rollback, locks/races and restart ownership; those were not run. Standalone functions remain despite unresolved caller usage. The full initial seven-adapter source audit identifies no remaining application-policy block, with source closure distinct from native/system qualification.
