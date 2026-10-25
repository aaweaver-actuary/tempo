# Retained Study history map (#389): source preparation only

**BLOCKED for real-history mapping, converter assignment and cutover.** This is a
field ledger and synthetic comparison example, not a migration, installed-data
inventory, approved identity map or golden fixture. It does not close #389.

## Evidence boundary

- Reviewed source: `cb0ed5910e6bd05894919fd8d49f5dc7bc236975` (2026-10-25).
  Legacy source declares schema 50; Learning's ordered manifest contains
  `learning.runtime/0001`, `/0002`, `learning.remediation/0001`, then
  `learning.bridge/0001`. A schema number alone does not identify installed bytes.
- Current contracts read: [#388](https://github.com/aaweaver-actuary/tempo/issues/388),
  [#389](https://github.com/aaweaver-actuary/tempo/issues/389),
  [#358](https://github.com/aaweaver-actuary/tempo/issues/358), and
  [#359](https://github.com/aaweaver-actuary/tempo/issues/359).
  [#371's cutover specification][cutover] was read at its actual head
  `bb073b6482fa97c5780118ff4fd52a395a8d2c21`, **not** the older SHA in its PR body.
  Its one-writer/activation/recovery rules are referenced, not reimplemented here.
- An earlier operator report identified deployed `86d11afe` / schema 50 on
  October 23. This task did not repeat that observation. Source comparison against
  full commit `86d11afe8579ac56b2a39d8d63984180c7f3c194` found legacy migrations
  001–049 and all three Learning SQL files byte-identical at their old paths
  (`backend/migrations/`, `services/learning/runtime/src/tempo_learning_runtime/migrations/`).
  Legacy 050 differs only in its included module resource path; its declared
  module checksum is unchanged. Browser-intents' source is byte-identical after
  relocation; study-intent-storage differs only in two relative imports. These
  are **source comparisons, not runtime/schema/device equivalence**.
- Actual latest rows, installed migration checksums, original browser authorities,
  pending operations, completeness and the reviewed source-to-target identities
  remain unverified. No live host, database, device, API or browser was accessed.

### Source register

All relative source pointers below refer to the reviewed source commit above.
DDL table names and adapter symbols specify the relevant portion; these are not
instructions to execute DDL or use live read endpoints.

| Label | Exact source |
| --- | --- |
| L1 | [Legacy 001][l1]: named `CREATE TABLE` statements; additions are named explicitly below. |
| L16/17/18 | [Review receipts][l16], [operation recovery][l17], [attempt ownership][l18]. |
| L30/32/42 | [Opening decision evidence][l30], [retained queue origins][l32], [issuance bit][l42]. |
| L33/35/38/48/50 | [Canonical prefix][l33], [route provenance][l35], [transition state][l38], [frozen order][l48], [legacy identity/module inclusion][l50]. |
| N1/N2/N3 | [Learning core][n1], [evidence revisions][n2], [Content aliases][n3]; [ordered manifest][manifest]. |
| R1 | [Shared remediation DDL][r1], included by both owners, not a new migration system. |
| A | [Existing translator][translator] `translate`, [observation helper][observation] `comparison_snapshot` / `observe_capture`, [operator][operator] `receipt` / `acknowledgement`. |
| B | [Intent store][intents] types/`identity`/`put`; [legacy recovery][legacy-browser] `legacyEnvelope` / `migrate`; [authority][authority] `studyWorkspace` / `assertStudyAuthority`; [read inspector][browser-read] `readRetainedStudyData`. |

## Projection and identity contract

#388 requires exactly `schema_version: 1`, `workspace_id`, `unresolved`, and
`entities`, with exactly eight groups. Each record has only `key` and `data`.
The examples below use `data` containers named for source tables/store records;
these **are proposed comparison fields, not new database columns or tables**.

- **M (mechanical correspondence):** listed fields have the same named Learning
  destination unless an arrow says otherwise. Copy their exact values only after
  source identity and referential closure are approved. M does not approve a row,
  alias, replay, schedule recomputation or activation.
- **C (cold preservation):** retain the listed fields/complete rows in the
  immutable private source archive, and include them under `data.cold` in the
  owning normalized entity on both sides. State the reason and archive locator
  in the private mapping receipt. This is a required preservation disposition,
  not a claim that an archive exists or a proposed new storage subsystem. The
  target-side comparison must actually read the retained archive; copying BEFORE
  into AFTER is not verification.
- **B (blocking):** source/destination semantics, identity, accepted authority or
  required values are not established. Retain source data but populate
  `unresolved`; #388 must exit 2. C cannot be used to hide a still-needed serving,
  replay, introduction, quota, grading or settlement fact.
- Source TEXT dates and `*_json` remain exact strings, including whitespace;
  SQL NULL is JSON null; BIGINT flags remain integers, BOOLEAN remains boolean.
  For typed TIMESTAMPTZ/DATE and JSONB, freeze one lossless serialization in the
  reviewed fixture. Never strip timezone, round numbers, parse/re-emit TEXT JSON,
  normalize FEN or drop unrecognized data fields to obtain equality.
- Preserve every captured column. The ledger partitions the named source facts;
  additional installed columns/tables or a missing required table are B until
  classified. A row is not absent because an adapter's SELECT omits it. Explicit
  zero rows need capture evidence; an omitted group is invalid. Cold-only rows
  get their own record within the appropriate group when no live owner remains.
- Keys are reviewer-approved **source authority + table/store + complete primary
  key tuples**, represented by stable opaque strings. Composite keys require
  unambiguous encoding, not delimiter guessing. The illustrative `synthetic:*`
  keys below are hand-assigned, not a real key-generating algorithm. Distinct
  authorities with identical IDs must not collide. Preserve source numeric IDs
  and every embedded reference; renumbering requires a separately approved map
  covering receipts, queue origins, manifests and browser payloads.
- Preserve a physical card's history once across all memberships. Do not clone
  reviews/schedules per route or distribute a prefix review across new decisions.
  A decision projection is not a new historical card. FEN, move text, hashes of
  only executable content, or matching row counts cannot establish identity.

## Field ledger

`PK` below is the authoritative key **within the captured authority**, never a
replacement for that authority. References to complete rows mean every column
enumerated in the linked definition, not an adapter's shortened projection.

### 1. cards

| Source / PK | Fields and proposed destination | Invariants / disposition |
| --- | --- | --- |
| L1 `cards`; `id` | M → `learning.cards`: `id,repertoire_id,kind,start_fen,moves_json,unlock_after_card_id,content_type,archived,superseded_by,source_ref,source_fen,revision,trained_color,pending_validation,prefix_split_rejected_after_review_id,study_exercise_id`. Scheduling fields and `introduced_at` are partitioned below, not omitted. | Preserve kind, executable revision, archive/deletion/supersession and nullable ownership. Check original repertoire-or-study FK constraint. Missing owner, ambiguous successor or changed executable is B. |
| L35 `cards`; `id` | `canonical_route_source`: C in `cards.data.cold`, no N1 column. | Retain exact provenance; any proposed route derivation/adoption depending on it is B pending explicit mapping. |
| L1 + L35 `repertoire_cards`; `(repertoire_id,card_id)` | M → same pair; `canonical_route_source`: C as above. | Keep all links plus `cards.repertoire_id`; do not replace either with an arbitrarily chosen owner. |
| L1 `repertoire_integrity_card_blocks`; `(repertoire_id,card_id,issue_id)` | M → blocking pair in N1; `issue_id,scan_generation,published_at`: C (N1 stores only the pair). | Preserve every original row in cold data before any reviewed many-to-one pair projection; never lose blocked status. |
| Legacy [037][l37] `deleted_cards`; `card_id` | M → `card_id,deleted_at`; TEXT→TIMESTAMPTZ is B until the exact timestamp parser/round-trip is approved. | Preserve tombstones even without a current card; do not resurrect content. |
| N1-only `cards` fields | `serving_active,publication_id,publication_generation`: B for legacy rows. | Defaults `1,NULL,1` are not accepted publication evidence. Existing target values must be captured, not overwritten. |

### 2. reviews

| Source / PK | Fields and proposed destination | Invariants / disposition |
| --- | --- | --- |
| L1 `reviews`; `id` | M → same table: `id,card_id,rating,reviewed_at,previous_interval,next_interval,internal_rating,guided,source_kind,source_ref,invalidated_at,invalidation_reason`. | Keep invalidated rows, ordering by original timestamp/ID and uniqueness of non-null `(source_kind,source_ref)`. Never replay reviews to recreate schedules or reinterpret `correct` as a new grading policy. |
| L30 `opening_evidence_observations`; `(attempt_id,decision_index)` | M → `attempt_id,decision_index,decision_id,observed_at,failure_at,observation_json`. | Decision observations remain observations, not extra reviews. Resolve their presentation/attempt without inventing a physical-card alias. |
| L30 `opening_evidence_summaries`; `decision_id` | M → `decision_id,first_responses,clean_successes,incorrect_responses,assisted_responses,manual_failures,corrections,distinct_clean_days`. | Do not regenerate aggregates from a potentially incomplete event archive. |
| L30 `opening_evidence_clean_days`; `(decision_id,study_day)` | M → same fields. | Preserve timezone-qualified day semantics from the owning attempts; do not derive UTC days instead. |
| R1 `remediation_settlements`; `(workspace_id,review_id)` | M → all fields: `workspace_id,review_id,attempt_id,card_id,reviewed_at,invalidated,invalidation_reason`. | Preserve issue frontier and invalidation; review IDs here are TEXT, not necessarily `reviews.id`. Cross-authority remapping is B. |

### 3. schedules

| Source / PK | Fields and proposed destination | Invariants / disposition |
| --- | --- | --- |
| L1 `cards`; `id` | M → same card's `state,due_date,interval_days,ease,repetitions,lapses,fsrs_card_json,first_correct_at,reinforcement_pending,stability,guided_review,maximum_interval,scheduling_mode,hard_correct_streak,recent_attempts_json`. Include `id` as the join identity. | Copy state, do not run the scheduler. [Reconciliation's `SCHEDULE_COLUMNS`][reconcile] is a smaller subset and is not a complete historical export. Preserve FSRS payload and ordered recent attempts exactly. |
| L1 `opening_card_schedule_seeds`; `card_id` | M → `card_id,source_card_id,baseline_successful_days,baseline_recent_clean,verification_due,created_at`. | Keep seed provenance separate from observed reviews; unresolved source card is B. |
| L16 `review_schedule_snapshots`; `review_id` | M → `review_id,state_json`. | Retain each saved snapshot, including fields beyond today's scheduler subset. |
| L1 `study_sibling_burials`; `(exercise_id,study_day)` | M → `exercise_id,study_day,reason`. | Preserve suppression and local-day meaning, not just due dates. |
| L1 + [047][l47] `settings`; `id` | M → `id,timezone,light_first_interval_days,new_cards_per_day,study_new_per_day,include_defensive_cards_in_daily_stack,study_only_mode`; `tactics_new_per_day` → N1 **`tactic_new_per_day`**. | Explicit singular/plural rename; no default reset. Legacy `timezone='local'` is B without the installed effective IANA zone. |
| Remaining L1 settings columns | C: `initial_depth,lichess_username,chesscom_username,auto_sync_minutes,engine_line_window_cp,major_mistake_cp,draw_hold_user_moves,coverage_reply_denominator,coverage_cumulative_target,coverage_horizon_fullmoves,coverage_path_floor,coverage_maia_elo,defense_new_cards_per_day,discovery_window_days`. Also C: [031][l31] `defensive_analysis_enabled`. | No same-field Learning columns; keep producer/settings history private. If an accepted endgame/defense path still needs these values, its serving compatibility is B, not silently defaulted. |

### 4. introductions (includes teaching)

| Source / PK | Fields and proposed destination | Invariants / disposition |
| --- | --- | --- |
| L1 `cards`; `id` | M → `id,introduced_at`. | Preserve date/null as recorded. [Admission][admission] can set this at admission, so it does not prove first issuance or first answer. |
| L1 `teaching_states`; `(card_id,revision,ply)` | M → `card_id,revision,ply,taught_at`. | No transfer to another revision/ply merely because moves match. Keep historical revisions. |
| L1 `tactic_progress`; `puzzle_id` | M → `puzzle_id,deck_id,card_id,clean_pass_at,admitted_at,admission_mode`. | Preserve clean-pass versus admission distinction; null card is not zero progress. |
| L1 `tactic_introductions`; `puzzle_id` | C → `puzzle_id,pack_id,introduction_date`; no N1 table. | Historical pack introduction cannot be reconstructed from `tactic_progress.admitted_at`; active quota/admission reliance is B. |
| L1 `tactic_pack_activation`; `pack_id`; `tactic_rotation`; `id` | C → `(pack_id,active)` and `(id,last_pack_id)`. | Legacy source-selection state has no direct Learning destination. Do not restart an optional producer to infer it. |
| Browser `teaching` store | M → retained same-authority browser facts `workspace,key,cardId,revision,ply?,kind`; separate from server teaching. | `seen` and `first-clean` are different facts. Legacy unversioned first-clean markers are C with their original value; existing UI recovery's assumed revision 1 is not historical proof. |

### 5. accepted_definitions (includes route and frozen-order facts)

| Source / PK | Fields and proposed destination | Invariants / disposition |
| --- | --- | --- |
| L1 + [029][l29] `repertoires`; `id` | M → `id,name,source_name,created_at,is_main,new_cards_per_day`. L33 `canonical_prefix_moves_json,canonical_prefix_revision,canonical_prefix_preview_id,scope_source_revision`: C, with B for executable scope adoption. | Nonempty canonical prefix is explicitly unsupported by A's `comparison_snapshot`; do not treat a route containing the same moves as approval. |
| L1 `repertoire_lines`; `id` | M → `id,repertoire_id,trained_color,created_at`; C → `name,start_fen,moves_json` (N1's line table lacks them). | Ordered authored route is still required identity evidence. Preserve it in the accepted definition/archive, not solely as a recomputed graph. |
| L1 `opening_graph_publications`; `repertoire_id` | M → `repertoire_id,generation,state`; C → `last_error,published_at`. | Bind steps to the captured generation, not whichever generation is current later. |
| L1 `opening_graph_steps`; `(repertoire_id,generation,line_id,decision_index)` | M → those PK fields plus `card_id,parent_card_id,starting_fen,moves_json,trained_color`; C → `decision_fen_key,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json`. | Preserve prefix/segment coverage and complete route evidence. The N1 projection alone cannot explain old multi-decision segments. |
| L1 `opening_graph_legacy_mappings`; `(repertoire_id,generation,legacy_card_id,decision_card_id)` | C → all four fields in `accepted_definitions.data.cold`; no N1 table. | These are existing graph-projection mappings, not reviewer-approved historical aliases. Preserve every generation and pair; using them to transfer reviews/schedules or adopt a decision identity is B until explicitly approved. |
| L1 `card_revisions`; PK `id`, unique `(card_id,revision)` | C → all fields `id,card_id,revision,start_fen,moves_json,history_mode,created_at` (no N1 table). | Historical executable evidence must survive; do not substitute current card or opening presentation when absent. |
| L1 `prefix_splits`; `source_card_id`; `repertoire_line_training_depths`; `line_id` | C → `source_card_id,source_revision,shortened_card_id,continuation_card_id,created_at` and `line_id,learner_decision_count`. | Preserve successor/depth evidence; any decision-history or admission mapping depending on it is B. No one-to-many review duplication. |
| L1 `studies`; `id` | M → `id,title,archived`; C → `description,source_json,created_at,updated_at`. | Authoring provenance is not recreated by the reduced Learning projection. |
| L1 `study_positions`; `id` | M → `id,fen`; C → `source_id,parent_id,child_index,move_uci,history_json,comment,starting_comment,nags_json,arrows_json,squares_json,node_path,valid`. | Preserve source tree and path, including transpositions; equal FEN is not equal position identity. |
| L1 `study_exercises`; `id` | M → `id,study_id,position_id,current_revision,status,sibling_group`; C → `source_json,point_value,created_at,updated_at`. | Capture all relevant revisions, not only current/published exercises. |
| L1 `study_exercise_revisions`; `(exercise_id,revision)` | M → `exercise_id,revision,specification_json,specification_hash,created_at`. | Saved answers must use the original accepted grading definition, not today's revision. |
| L1 `study_chapters,study_sources,study_links` | C → complete rows, keyed respectively by `id`; exact fields in L1 named statements. | Retain authored tree/raw PGN, import diagnostics, source hashes and links; these have no N1 tables. If needed to grade a retained attempt, B until its accepted revision supplies that closure. |
| L48 `frozen_opening_ranks`; `(run_id,card_id)` | M → `run_id,card_id,ordinal,fingerprint,memberships_json,definition_json`; also M → `frozen_opening_activation(id,run_id,epoch)` and `frozen_opening_runs(run_id,reader_version)`. | Keep every ordinal and membership/definition fingerprint; do not rerank from current schedules. [Frozen-order reader][frozen] protects exposed/legacy-origin cards. |
| Remaining L48 facts | C → runs `plan_checksum,plan_json,previous_run_id,created_at,invalidated`; pending `(run_id)`; queue changes `(run_id,queue_id,queue_date,card_id,before_position,after_position,definition_json)`. | No N1 columns/tables. **Any pending apply is B** until its existing owner reconciles it, not permission to copy half a run. |
| L1 `endgame_templates`; `id` | M → `id,card_id,name,white_material,black_material,trained_color,goal_mix,enabled,created_at`. | Keep accepted definition and links, not a freshly generated position. |
| L1 `puzzles,puzzle_decks,threat_training_candidates`; [025][l25] `tactic_captures` | C → complete named-table rows/PKs; B for required accepted-definition extraction. | No exact table-to-table Learning equivalent. A retained tactic's card executable does not establish its source metadata. Candidate `id,card_id,exercise_revision,evidence_json,validation_json,policy_json,approved_at` do not by themselves define N1 `accepted_defense_exercises(candidate_id,card_id,exercise_revision,definition_json)`. Do not accept unapproved candidates. |
| N1/N2/N3 accepted state | Preserve existing target `accepted_publications(publication_id,generation,digest,payload_json,state)`, `serving_publications(publication_id,generation,activation_revision)`, `accepted_defense_exercises`, `content_card_aliases(content_card_id,learning_card_id,publication_id,binding_digest)`, `accepted_evidence_revisions(workspace_id,evidence_id,evidence_revision,semantic_digest,updated_at)`. | B for manufacturing these from legacy defaults. The [publication adapter][publication] stages/activates content, not history migration. N2 null `semantic_digest` means unverified old semantic payload, not an empty digest. |
| L50 `legacy_learning_identity`; `id` | C → exact `id,target_id,workspace_id` in retained source-authority metadata; B → independent `learning.instance(id,workspace_id)`. | `workspace_id='legacy'` and an actual target ID cannot be guessed or relabelled. The normalized logical workspace requires reviewer-approved linkage, not an invented live identity. |
| N1 `tablebase_cache`; `fen_key` | C → exact `fen_key,response_json,fetched_at` in existing target `accepted_definitions.data.cold`. | Derived lookup cache, not an accepted exercise definition or historical answer. Retention does not authorize a new lookup or establish grading compatibility; any required serving dependency is B until its owner confirms it. |

Supporting authoring/projection closure is also explicit: C preserves complete
rows from L1 `repertoire_card_introduction_priorities`,
`repertoire_card_priority_generations`, [024][l24] `opening_segmentation_*` and
L33 `canonical_prefix_previews/results/positions` in accepted_definitions cold
data, keyed by each DDL PK. They have no same-table N1 destination. These are
historical/advisory facts, not permission to activate a proposal or recompute
frozen order. Any installed route/transition/publication dependency beyond this
ledger is B until the snapshot inventory names its exact rows and disposition.

### 6. queue_attempts (includes saved answers and issuance)

| Source / PK | Fields and proposed destination | Invariants / disposition |
| --- | --- | --- |
| L1 `daily_queue`; `id` | M → `id,queue_date,card_id,cycle,position,status,attempt_state,review_result_json,attempt_failed,card_bucket,admission_kind,gameplay_priority_reason,admission_repertoire_id,admission_source`. | Retain complete/failed/buried/superseded as well as queued rows; preserve `(queue_date,card_id,cycle)` uniqueness and position/ID tie-breaks. |
| L1 `daily_queue_days`; `queue_date` | M → `queue_date,seed,membership_hash,generated_at`. | Preserve frozen daily order evidence. No regeneration from today's memberships. |
| L32 + L42 `queue_attempt_origins`; `(queue_entry_id,card_id,revision)` | M → all fields: PK plus `queue_date,cycle,admission_kind,admission_repertoire_id,attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type,issued_as_head`. | Keep origins whose queue/card projection has disappeared. `legacy=1` is backfilled admission evidence; `issued_as_head=0` is not proof of never issued. Neither table stores a first-issued timestamp. |
| L1 + L38 `study_attempts`; `id` | M → `id,exercise_id,revision,card_id,queue_entry_id,cycle,context,answer_json,answer_hash,assessment_json,assessment_method,grader_version,hint_seen,solution_seen_before_answer,started_at,committed_at,finalized_at,result_json,retired_operation_id`. | Preserve unfinalized/practice/review attempts, original answer and retirement. No regrading/re-asking the answer. Conflicting queue uniqueness is B. |
| L30 + L38 `opening_evidence_attempts`; `attempt_id` | M → `attempt_id,manifest_id,presentation_snapshot_id,repertoire_id,card_id,card_revision,trained_color,context_json,manifest_json,started_at,study_timezone,queue_entry_id,contiguous_sequence,state,terminal_json,completed_at,review_id,review_result_json,retired_operation_id`. | Preserve active/partial attempts and exact manifest scope. `started_at` is not first-issued proof. |
| L30 supporting rows | M → presentations `(id,content_fingerprint,card_id,revision,start_fen,moves_json,trained_color,captured_at)`; queue contexts `(queue_entry_id,presentation_snapshot_id,repertoire_id,effective_trained_color)`; events `(attempt_id,sequence,event_json)`. | Keep captured color/scope, contiguous sequence and event order. Do not rerun triggers to infer historical context from edited repertoires. |
| L1 `tactic_discovery_attempts`; `id` | M → `id,deck_id,puzzle_id,clean,result_json`. | Retain original result even without a current card. |
| L1 `defense_attempts`; `attempt_id` | M → `attempt_id,candidate_id,card_id,queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at`. | Legacy nullable `queue_entry_id` versus N1 NOT NULL: null is B, never invent an entry. Candidate→accepted definition is separately B above. |
| L1 `defense_recognition_submissions`; `attempt_id` | M → `attempt_id,candidate_id,queue_entry_id,exercise_revision,answer_json,recognition_correct,created_at`. | Keep recognition before completion; no new answer/attempt identity on retry. |
| L1 `endgame_attempts`; `id` | M → `id,template_id,start_fen,target,status,user_moves,created_at,completed_at`. | Active attempts are durable work, not disposable session state. |
| L1 queue projection state | M → `queue_projections(queue_date,state,generation,updated_at,refresh_pending,last_error,blocked_count)` and `queue_projection_diagnostics(queue_date,card_id,message)`. | A refreshing/failed projection is not ready merely because rows copied. Preserve diagnostics; existing owner must reconcile before serving. |
| L1 `gameplay_card_priorities`; `card_id` | C → `card_id,source_game_id,finding_id,priority_date,reason,created_at`. | No same-table N1 destination. A currently effective priority is B until its queue/obligation semantics are accounted for; do not make optional Games a serving prerequisite. |
| R1 `remediation_attempts`; `(workspace_id,attempt_id)` | M → `workspace_id,attempt_id,card_id,card_revision,publication_id,publication_generation,issued_at,frontier_sequence`. | Preserve **actual recorded** issuance/frontier if present. Missing pre-remediation issuance remains unknown: never use operation receipt time, admission date, review time or migration time to fill it. |
| R1 remaining queue-driving facts | M → complete `remediation_cards,remediation_incident_versions,remediation_contributions,remediation_obligation_heads,remediation_evidence,remediation_source_heads,remediation_game_heads,remediation_workspaces` rows, with their full compound PKs from R1. N1 `learning_obligations`: retain all fields if present. | Shared DDL is not proof that legacy authority inside `payload_json`, publication IDs or workspace `legacy` can be relabelled as independent Learning. That authority transfer is B. Keep sequence/frontier, revisions, hashes, inactive/withdrawn contributions and accepted timestamps. |

### 7. operation_receipts

| Source / PK | Fields and proposed destination | Invariants / disposition |
| --- | --- | --- |
| L1 + L17 `operation_receipts`; `operation_id` | M → `operation_id,command_name,request_hash,payload_json,state,response_json,error_json,next_retry_at,created_at,updated_at` where compatible. | Legacy nullable `payload_json` vs N1 NOT NULL is B, even for terminal receipts; do not invent `{}`. Command names, payload shape/hash algorithm and state meanings require owner review before replay. |
| L17/L18 extra receipt fields | C → `background,attempt_count,lease_expires_at,last_error_json,retry_cycle,cycle_attempt_count,attempt_token`. | N1 lacks these recovery-owner fields. Nonterminal legacy state (`pending,queued,executing,retrying,blocked`) is B until resolved by its original owner or explicitly transferred; never activate an old lease/token in the new executor. |
| L16 + L32 `review_attempt_receipts`; `attempt_id` | M → `attempt_id,card_id,queue_entry_id,outcome,guided,completed_at,review_id,scheduling_status,warning,result_json,request_json`. | Keep request identity, warnings and scheduling result, including unknown/null request. [Receipt persistence][reconcile] is not permission to replay a new review. |
| R1 `remediation_deliveries`; `(workspace_id,delivery_id)`; `remediation_review_receipts`; `(workspace_id,review_id)` | M → `(workspace_id,delivery_id,payload_hash,response_json)` and `(workspace_id,review_id,payload_json,settled)`. | Preserve boolean `settled`, typed JSONB response and TEXT payload distinctions. Legacy authority transfer remains B. |
| N1 receipt/binding tables, when present | M → `activation_receipts(operation_id,request_hash,result_json)`, `evidence_deliveries(delivery_id,digest,result_json)`, `logical_request_bindings(family,attempt_id,request_hash)`. | No synthetic legacy binding or successful activation receipt. [Gateway][gateway] checks command + hash and commits effects/receipt atomically; browser completion requires the matching receipt. |
| N1 `analysis_obligations`; `request_id` | Retain complete existing target rows `request_id,candidate_id,request_json,state`. B for unfinished work; C in `operation_receipts.data.cold` only after owner-verified terminal disposition. | These durable defense-analysis requests are not absent because their optional producer is unavailable. Preserve original request identity/payload/state; do not replay, mark complete or transfer ownership to make snapshots equal. |
| N1 `advisory_notifications`; `(kind,subject)` | C → exact `kind,subject,generation` in existing target `operation_receipts.data.cold`. | Advisory refresh generation is not a review/settlement receipt. Any unfinished required publication/queue effect is B until the existing owner reconciles it; capture does not start an optional producer. |
| L38 transition rows/fences | B for any nonterminal application or outstanding fence; C complete rows after owner-verified terminal state. Exact `prefix_transition_applications`, `prefix_transition_card_fences`, `prefix_transition_repertoire_fences` fields/PKs are in L38. | Existing transition owner, not a second source fence or new recovery implementation. Keep retired-operation references. |
| L1 `discovery_admission_intents`, related `background_tasks/background_task_events` | B for unfinished Study-affecting work; C complete original rows after owner-verified terminal resolution, with PKs and every column from L1 plus applicable ordered migrations. | N1 background-task shapes differ. Do not silently drop durable work or transfer a producer lease; lack of a typed command mapping is a blocker. |

Keep source schema receipts (`tempo_schema_migrations(version,applied_at)`,
`internal_migrations(name,applied_at)`) and actual target
`migration_history(id,checksum,applied_at)` as C source/target capture metadata in
the private mapping receipt, not as interchangeable runtime receipts. They are
not business rows to copy from BEFORE to AFTER. Unrelated producer tables are
outside this bounded map, not approved for deletion; the final snapshot inventory
must establish their cold disposition and any retained-history dependencies.

### 8. browser_intents

The browser's storage `workspace` is an **origin-bound authority identity**, not
automatically the normalized snapshot's logical `workspace_id`. Preserve both.
Legacy uses `${API-origin}:study`; independent Study uses
`${Learning-origin}:study:${encodeURIComponent(workspace)}`. Do not rewrite one
to the other or assume phone/desktop means a particular implementation.

| Source / authoritative key | Fields and proposed destination | Invariants / disposition |
| --- | --- | --- |
| IndexedDB `tempo-study-intents` v1 / `intents`; `(workspace,key)` | M → retained same-authority `workspace,key,kind,attemptId,requestId,endpoint,revision?,createdAt,payload,sequence,state,version,data?,receipt?,claim?`; claim contains `owner,token,expiresAt`. | Immutable envelope includes endpoint/payload/time/revision; retain mutable delivery state separately. Repeated key + changed envelope is conflict, not overwrite. A claim coordinates delivery, not settlement; preserve it as evidence, not a newly authorized lease. |
| Same DB / `metadata`; `(workspace,key)` | M → `workspace,key,value`, including sequence, practice boundaries and legacy-import checkpoints; complete teaching facts are owned by introductions above. | Keep committed/discarded as well as pending/conflicted rows. Checkpoints never authorize skipping retained legacy sources. |
| `tempo-learning-authorities-v1` localStorage | M → original registry identity and `origin,workspace,offlineDatabase`; browser-build/origin capture is an external gate. | Keep mapping for every source authority; do not grant target credentials. `tempo-learning-connection-v1` is **sessionStorage**, containing `origin,workspace,credential,bootstrap?`: **exclude secrets from fixtures/Git**, retain only authorized non-secret authority/bootstrap evidence privately. |
| Legacy localStorage keys listed by B | C exact raw values for pending reviews/failures/teaching, `tempo-seen-moves`, `tempo-first-clean-passes`, and inspector-discovered endgame/burial/tactic-capture/card-delete keys. | B until validated with the actual installed reader and reconciled. `legacy-time-unavailable` and `teaching-fact` are adapter sentinels, not observed timestamps. Preserve malformed/unrecognized data rather than silently clearing it. |
| `tempo-study-intents-cutover-v1` localStorage | C → exact raw acknowledgement value (or observed absence) in `browser_intents.data.cold`, separately from IndexedDB migration checkpoints. | The current retained-data inspector does not capture this key; the authorized browser inventory must include it explicitly. A stored acknowledgement is not proof older tabs are closed or authority has transferred. Never manufacture or replay it as cutover approval. |
| `tempo-offline-training` or registered authority-specific v2 DB / `training`, key `prepared-daily-queue` | M → [PreparedTraining][offline-types] `localDate,preparedAt,cards,attempts,nextTemporaryId` with complete original card transport objects. Each OfflineAttempt retains every field in that type (including original answer/assessment, local/parent/server IDs, cycle, expected revision/review, acknowledgment/conflict, evidence and continuation state). | No regeneration of a prepared card/answer, no integer-ID coercion, no deletion based on a locally shown success. Any schema/authority/required payload gap is B. |
| Same DB / `opening_attempts`, key `attempt_id`; `opening_events`, key `(attempt_id,sequence)` | M → [SavedAttempt/SavedEvent][journal-types] complete checkpoint/manifest/context/terminal, `owner_session_id,final_sequence,delivery_state,rejection?,retention_reason?,local_capture_gap?,delivery?`; frozen delivery includes `checkpoint,operationKey`. Event fields exactly as [OpeningDecisionEvent][evidence-types]. | Preserve `retained`/quota-fallback data, gaps and event order. [Journal adapter][journal-storage] confirms acknowledged sequences; its separate completion-removal operation deletes the whole journal only after the caller confirms completion. A receipt must match the attempt. No storage mutation performed here. |

## Existing identity helpers: reuse limits

A's translator already distinguishes ordered authored routes, policy/color,
physical executable and interior learner-decision projections. With
`preserve_physical_execution=True` it retains opponent preludes and can bind an
interior `exercise_ply` to the same physical card. The default normalized prelude
path creates a new executable and explicitly marks historical adoption unknown.
Neither path exports complete reviews, schedules, queue/receipt state or browser
work, so neither output is a #388 history snapshot.

Do not remove its diagnostics to force adoption: unavailable/archived/blocked
cards, unknown color, revision other than 1 requiring a successor, changed
executable, ambiguous route or interior binding, empty/over-40-move executable,
and a shared physical card needing explicit policy mapping remain B. Shared
memberships must be preserved even when the current helper cannot map them.
`comparison_snapshot` carries before/after authority tokens for downstream
validation; it checks source/graph/catalogue digests, graph generation,
complete/scoped catalogue and canonical-prefix support. It does not itself
compare the two authority tokens. Its introduced boolean is not first-issued evidence.
The operator's acceptance/queue methods are actions and were **not** run.

## Interruption, repeated import and original-authority replay

The following are **required future disposable proofs, not executed migration
tests**. Reuse the existing owner mechanisms; do not build a new import runner,
source-fence system or launcher in this documentation task.

| Boundary / scenario | Required evidence and expected result |
| --- | --- |
| Final source capture | Apply [#371 §3–5][cutover]: one writer, fence all write-capable paths including issuance/maintenance, then a final consistent snapshot and retained pending-browser inventory. Source preparation here did none of this. |
| Interrupted copy before activation | Keep source authoritative/fenced as specified, preserve partial target and mapping receipt; rerun the same approved import plan into the disposable target. No double reviews, introductions, teaching, attempts or settlements; #388 equal only after independent complete extraction. |
| Repeated identical import | Same approved historical IDs and complete values produce no additional logical effects. Duplicate input keys fail validation rather than being silently deduplicated. Changed immutable source bytes/alias/receipt under the same identity must stop as conflict. |
| Pending original answer | Existing [review delivery][review-outbox] checks the original operation receipt first after ambiguous delivery; [operation status][operation-status] checks operation identity, preserves pending/blocked and does not equate HTTP 202 with completion. Replay only the original immutable answer to its original verified authority. Changed answer/revision/authority is B. |
| Authority switch | [assertStudyAuthority][authority] rejects foreign/unconfirmed pending intents, legacy keys and unacknowledged offline attempts. Do not relabel browser storage, discard a conflicted answer, or switch endpoints to make the import pass. Resolve through original authority under the cutover plan before its final fence. |
| Activation begun / acceptance uncertain | Persist activation boundary before target traffic per #371. Keep legacy non-writing; forward-repair existing Learning authority. No automatic legacy fallback or restore over the authoritative target. Separate recovery destination needs proof of all post-snapshot accepted effects before promotion. |

## Synthetic before/after contract examples

The two complete JSON blocks below deliberately describe the **same supplied
facts**, with reordered object members in AFTER. They demonstrate #388, not an
extractor, importer, business-valid database, shared-policy alias approval or
live fixture. All identities/dates/payloads are invented; `.invalid` is a reserved
example origin. The small examples select representative fields; production
extraction must include the entire ledger and capture inventory. `unresolved: []`
means only that this hand-authored example has no unresolved example key. It is
not a claim that the real #389 gate has been resolved. Synthetic hash/fingerprint
strings are opaque placeholders, not runtime-valid digests.

`first_issued_provenance` is comparison annotation, not a stored timestamp:
`unknown` must remain unknown when only a backfilled origin/receipt exists.
The browser record deliberately remains pending at its original authority on
both sides. Equality does not settle that pending work.

<!-- synthetic-before -->
```json
{
  "schema_version": 1,
  "workspace_id": "synthetic-workspace",
  "unresolved": [],
  "entities": {
    "cards": [
      {
        "key": "synthetic:card:1",
        "data": {
          "cards": {"id": "synthetic-card","repertoire_id": "synthetic-repertoire-a","kind": "prefix","start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","moves_json": "[\"e2e4\",\"e7e5\",\"g1f3\"]","unlock_after_card_id": null,"content_type": "opening","archived": 0,"superseded_by": null,"source_ref": null,"source_fen": null,"revision": 1,"trained_color": "white","pending_validation": 0,"prefix_split_rejected_after_review_id": -1,"study_exercise_id": null},
          "repertoire_cards": [{"repertoire_id": "synthetic-repertoire-a","card_id": "synthetic-card"},{"repertoire_id": "synthetic-repertoire-b","card_id": "synthetic-card"}],
          "frozen_opening_ranks": [{"run_id": "synthetic-run","card_id": "synthetic-card","ordinal": 3,"fingerprint": "synthetic-fingerprint","memberships_json": "[\"synthetic-repertoire-a\",\"synthetic-repertoire-b\"]","definition_json": "{\"synthetic\":\"retained-executable\"}"}],
          "cold": {"canonical_route_source": 1}
        }
      }
    ],
    "reviews": [
      {
        "key": "synthetic:review:101",
        "data": {
          "reviews": {"id": 101,"card_id": "synthetic-card","rating": "correct","reviewed_at": "2030-01-02T10:00:00Z","previous_interval": 0,"next_interval": 7,"internal_rating": "good","guided": 0,"source_kind": "study","source_ref": "synthetic-old-attempt","invalidated_at": null,"invalidation_reason": null}
        }
      }
    ],
    "schedules": [
      {
        "key": "synthetic:schedule:1",
        "data": {
          "cards": {"id": "synthetic-card","state": "learning","due_date": "2030-01-09","interval_days": 7,"ease": 2.5,"repetitions": 1,"lapses": 0,"fsrs_card_json": null,"first_correct_at": "2030-01-02","reinforcement_pending": 0,"stability": 7.0,"guided_review": 0,"maximum_interval": 365,"scheduling_mode": "light","hard_correct_streak": 1,"recent_attempts_json": "[true]"},
          "review_schedule_snapshots": [{"review_id": 101,"state_json": "{\"due_date\":\"2030-01-09\",\"interval_days\":7}"}]
        }
      }
    ],
    "introductions": [
      {
        "key": "synthetic:introduction:1",
        "data": {
          "cards": {"id": "synthetic-card","introduced_at": "2030-01-02"},
          "teaching_states": [{"card_id": "synthetic-card","revision": 1,"ply": 0,"taught_at": "2030-01-02T09:59:00Z"}]
        }
      }
    ],
    "accepted_definitions": [
      {
        "key": "synthetic:definition:1",
        "data": {
          "study_exercise_revisions": [{"exercise_id": "synthetic-exercise","revision": 2,"specification_json": "{\"type\":\"explanation\",\"prompt\":\"Explain the plan.\",\"hint\":\"\",\"explanation\":\"Synthetic explanation\",\"rubric\":\"Name the plan.\"}","specification_hash": "synthetic-specification-hash","created_at": "2030-01-01T00:00:00Z"}],
          "cold": {"card_revisions": [{"id": 301,"card_id": "synthetic-card","revision": 1,"start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","moves_json": "[\"e2e4\",\"e7e5\",\"g1f3\"]","history_mode": "synthetic-preserved-history","created_at": "2030-01-01T00:00:00Z"}],"repertoire_lines": [{"id": "synthetic-line","repertoire_id": "synthetic-repertoire-a","name": "Synthetic route","trained_color": "white","start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","moves_json": "[\"e2e4\",\"e7e5\",\"g1f3\"]","created_at": "2030-01-01T00:00:00Z"}]},
          "frozen_opening_activation": {"id": 1,"run_id": "synthetic-run","epoch": 2}
        }
      }
    ],
    "queue_attempts": [
      {
        "key": "synthetic:queue:202",
        "data": {
          "daily_queue": {"id": 202,"queue_date": "2030-01-09","card_id": "synthetic-card","cycle": 0,"position": 3,"status": "queued","attempt_state": "clean","review_result_json": null,"attempt_failed": 0,"card_bucket": "opening","admission_kind": "review","gameplay_priority_reason": null,"admission_repertoire_id": "synthetic-repertoire-a","admission_source": "synthetic-source"},
          "queue_attempt_origins": [{"queue_entry_id": 202,"card_id": "synthetic-card","revision": 1,"queue_date": "2030-01-09","cycle": 0,"admission_kind": "review","admission_repertoire_id": "synthetic-repertoire-a","attempt_failed": 0,"last_status": "queued","review_result_json": null,"legacy": 1,"start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","moves_json": "[\"e2e4\",\"e7e5\",\"g1f3\"]","trained_color": "white","content_type": "opening","issued_as_head": 0}],
          "first_issued_provenance": "unknown"
        }
      },
      {
        "key": "synthetic:saved-attempt:1",
        "data": {
          "study_attempts": {"id": "synthetic-saved-attempt","exercise_id": "synthetic-exercise","revision": 2,"card_id": null,"queue_entry_id": null,"cycle": null,"context": "practice","answer_json": "{\"type\":\"explanation\",\"text\":\"Synthetic original answer\",\"ready\":true}","answer_hash": "synthetic-answer-hash","assessment_json": "{\"outcome\":\"needs_self_assessment\",\"feedback\":\"Compare with the rubric\"}","assessment_method": "self_assessed","grader_version": 1,"hint_seen": 0,"solution_seen_before_answer": 0,"started_at": "2030-01-09T09:55:00Z","committed_at": "2030-01-09T09:56:00Z","finalized_at": null,"result_json": null,"retired_operation_id": null}
        }
      }
    ],
    "operation_receipts": [
      {
        "key": "synthetic:receipt:old",
        "data": {
          "operation_receipts": {"operation_id": "review-attempt:synthetic-old-attempt","command_name": "cards.review","request_hash": "synthetic-request-hash","payload_json": "{\"card_id\":\"synthetic-card\",\"review\":{\"outcome\":\"correct\",\"guided\":false,\"queue_entry_id\":201,\"attempt_id\":\"synthetic-old-attempt\",\"recorded_at\":\"2030-01-02T10:00:00Z\",\"expected_revision\":1}}","state": "complete","response_json": "{\"persisted\":true,\"review_id\":101}","error_json": null,"next_retry_at": null,"created_at": "2030-01-02T10:00:00Z","updated_at": "2030-01-02T10:00:01Z"},
          "cold": {"background": false,"attempt_count": 1,"lease_expires_at": null,"last_error_json": null,"retry_cycle": 0,"cycle_attempt_count": 1,"attempt_token": null}
        }
      }
    ],
    "browser_intents": [
      {
        "key": "synthetic:browser:pending",
        "data": {
          "intent": {"workspace": "https://study.example.invalid:study:synthetic-workspace","key": "review:synthetic-pending-attempt","kind": "review","attemptId": "synthetic-pending-attempt","requestId": "review-attempt:synthetic-pending-attempt","endpoint": "/api/cards/synthetic-card/review","revision": 1,"createdAt": "2030-01-09T10:00:00Z","payload": {"backendId": "synthetic-card","queueEntryId": 202,"outcome": "again","guided": false,"attemptId": "synthetic-pending-attempt","completedAt": "2030-01-09T10:00:00Z","expectedRevision": 1},"sequence": 4,"state": "pending","version": 2,"data": {"backendId": "synthetic-card","queueEntryId": 202,"outcome": "again","guided": false,"attemptId": "synthetic-pending-attempt","completedAt": "2030-01-09T10:00:00Z","expectedRevision": 1,"deliveryStarted": true}}
        }
      }
    ]
  }
}
```

<!-- synthetic-after -->
```json
{
  "entities": {
    "browser_intents": [
      {
        "data": {
          "intent": {"data": {"deliveryStarted": true,"expectedRevision": 1,"completedAt": "2030-01-09T10:00:00Z","attemptId": "synthetic-pending-attempt","guided": false,"outcome": "again","queueEntryId": 202,"backendId": "synthetic-card"},"version": 2,"state": "pending","sequence": 4,"payload": {"expectedRevision": 1,"completedAt": "2030-01-09T10:00:00Z","attemptId": "synthetic-pending-attempt","guided": false,"outcome": "again","queueEntryId": 202,"backendId": "synthetic-card"},"createdAt": "2030-01-09T10:00:00Z","revision": 1,"endpoint": "/api/cards/synthetic-card/review","requestId": "review-attempt:synthetic-pending-attempt","attemptId": "synthetic-pending-attempt","kind": "review","key": "review:synthetic-pending-attempt","workspace": "https://study.example.invalid:study:synthetic-workspace"}
        },
        "key": "synthetic:browser:pending"
      }
    ],
    "operation_receipts": [
      {
        "data": {
          "cold": {"attempt_token": null,"cycle_attempt_count": 1,"retry_cycle": 0,"last_error_json": null,"lease_expires_at": null,"attempt_count": 1,"background": false},
          "operation_receipts": {"updated_at": "2030-01-02T10:00:01Z","created_at": "2030-01-02T10:00:00Z","next_retry_at": null,"error_json": null,"response_json": "{\"persisted\":true,\"review_id\":101}","state": "complete","payload_json": "{\"card_id\":\"synthetic-card\",\"review\":{\"outcome\":\"correct\",\"guided\":false,\"queue_entry_id\":201,\"attempt_id\":\"synthetic-old-attempt\",\"recorded_at\":\"2030-01-02T10:00:00Z\",\"expected_revision\":1}}","request_hash": "synthetic-request-hash","command_name": "cards.review","operation_id": "review-attempt:synthetic-old-attempt"}
        },
        "key": "synthetic:receipt:old"
      }
    ],
    "queue_attempts": [
      {
        "data": {
          "first_issued_provenance": "unknown",
          "queue_attempt_origins": [{"issued_as_head": 0,"content_type": "opening","trained_color": "white","moves_json": "[\"e2e4\",\"e7e5\",\"g1f3\"]","start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","legacy": 1,"review_result_json": null,"last_status": "queued","attempt_failed": 0,"admission_repertoire_id": "synthetic-repertoire-a","admission_kind": "review","cycle": 0,"queue_date": "2030-01-09","revision": 1,"card_id": "synthetic-card","queue_entry_id": 202}],
          "daily_queue": {"admission_source": "synthetic-source","admission_repertoire_id": "synthetic-repertoire-a","gameplay_priority_reason": null,"admission_kind": "review","card_bucket": "opening","attempt_failed": 0,"review_result_json": null,"attempt_state": "clean","status": "queued","position": 3,"cycle": 0,"card_id": "synthetic-card","queue_date": "2030-01-09","id": 202}
        },
        "key": "synthetic:queue:202"
      },
      {
        "data": {
          "study_attempts": {"retired_operation_id": null,"result_json": null,"finalized_at": null,"committed_at": "2030-01-09T09:56:00Z","started_at": "2030-01-09T09:55:00Z","solution_seen_before_answer": 0,"hint_seen": 0,"grader_version": 1,"assessment_method": "self_assessed","assessment_json": "{\"outcome\":\"needs_self_assessment\",\"feedback\":\"Compare with the rubric\"}","answer_hash": "synthetic-answer-hash","answer_json": "{\"type\":\"explanation\",\"text\":\"Synthetic original answer\",\"ready\":true}","context": "practice","cycle": null,"queue_entry_id": null,"card_id": null,"revision": 2,"exercise_id": "synthetic-exercise","id": "synthetic-saved-attempt"}
        },
        "key": "synthetic:saved-attempt:1"
      }
    ],
    "accepted_definitions": [
      {
        "data": {
          "frozen_opening_activation": {"epoch": 2,"run_id": "synthetic-run","id": 1},
          "cold": {"repertoire_lines": [{"created_at": "2030-01-01T00:00:00Z","moves_json": "[\"e2e4\",\"e7e5\",\"g1f3\"]","start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","trained_color": "white","name": "Synthetic route","repertoire_id": "synthetic-repertoire-a","id": "synthetic-line"}],"card_revisions": [{"created_at": "2030-01-01T00:00:00Z","history_mode": "synthetic-preserved-history","moves_json": "[\"e2e4\",\"e7e5\",\"g1f3\"]","start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","revision": 1,"card_id": "synthetic-card","id": 301}]},
          "study_exercise_revisions": [{"created_at": "2030-01-01T00:00:00Z","specification_hash": "synthetic-specification-hash","specification_json": "{\"type\":\"explanation\",\"prompt\":\"Explain the plan.\",\"hint\":\"\",\"explanation\":\"Synthetic explanation\",\"rubric\":\"Name the plan.\"}","revision": 2,"exercise_id": "synthetic-exercise"}]
        },
        "key": "synthetic:definition:1"
      }
    ],
    "introductions": [
      {
        "data": {
          "teaching_states": [{"taught_at": "2030-01-02T09:59:00Z","ply": 0,"revision": 1,"card_id": "synthetic-card"}],
          "cards": {"introduced_at": "2030-01-02","id": "synthetic-card"}
        },
        "key": "synthetic:introduction:1"
      }
    ],
    "schedules": [
      {
        "data": {
          "review_schedule_snapshots": [{"state_json": "{\"due_date\":\"2030-01-09\",\"interval_days\":7}","review_id": 101}],
          "cards": {"recent_attempts_json": "[true]","hard_correct_streak": 1,"scheduling_mode": "light","maximum_interval": 365,"guided_review": 0,"stability": 7.0,"reinforcement_pending": 0,"first_correct_at": "2030-01-02","fsrs_card_json": null,"lapses": 0,"repetitions": 1,"ease": 2.5,"interval_days": 7,"due_date": "2030-01-09","state": "learning","id": "synthetic-card"}
        },
        "key": "synthetic:schedule:1"
      }
    ],
    "reviews": [
      {
        "data": {
          "reviews": {"invalidation_reason": null,"invalidated_at": null,"source_ref": "synthetic-old-attempt","source_kind": "study","guided": 0,"internal_rating": "good","next_interval": 7,"previous_interval": 0,"reviewed_at": "2030-01-02T10:00:00Z","rating": "correct","card_id": "synthetic-card","id": 101}
        },
        "key": "synthetic:review:101"
      }
    ],
    "cards": [
      {
        "data": {
          "cold": {"canonical_route_source": 1},
          "frozen_opening_ranks": [{"definition_json": "{\"synthetic\":\"retained-executable\"}","memberships_json": "[\"synthetic-repertoire-a\",\"synthetic-repertoire-b\"]","fingerprint": "synthetic-fingerprint","ordinal": 3,"card_id": "synthetic-card","run_id": "synthetic-run"}],
          "repertoire_cards": [{"card_id": "synthetic-card","repertoire_id": "synthetic-repertoire-a"},{"card_id": "synthetic-card","repertoire_id": "synthetic-repertoire-b"}],
          "cards": {"study_exercise_id": null,"prefix_split_rejected_after_review_id": -1,"pending_validation": 0,"trained_color": "white","revision": 1,"source_fen": null,"source_ref": null,"superseded_by": null,"archived": 0,"content_type": "opening","unlock_after_card_id": null,"moves_json": "[\"e2e4\",\"e7e5\",\"g1f3\"]","start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","kind": "prefix","repertoire_id": "synthetic-repertoire-a","id": "synthetic-card"}
        },
        "key": "synthetic:card:1"
      }
    ]
  },
  "unresolved": [],
  "workspace_id": "synthetic-workspace",
  "schema_version": 1
}
```

### Reproduce the bounded checks

Re-executed 2026-10-25 in an isolated Mac checkout against base `cb0ed591`:
**23/23 existing comparator tests passed**, plus **12 CLI scenarios and 2
equal/nonmutation API checks** below. All **41 local source links** resolve;
`npm run check:conflicts` and `git diff --check` passed. Independent source review
covered the ledger's DDL, identity/adaptation limits and browser storage fields;
all **55 historical source-file comparisons** were verified (53 byte-identical,
the two relocation differences described above). This is source correspondence,
not installed-data completeness or an approved historical identity map.
No database/browser/infrastructure tests were run.

From the repository root, this reads only this document and the existing
standalone comparator, and writes temporary JSON files outside the checkout.
It tests equal reordered objects, repeated comparison without input mutation,
missing review, changed due date, changed receipt payload, changed browser
authority, lost frozen membership, fabricated issuance, reordered memberships,
changed saved answer, and duplicate/unresolved/missing-group invalid inputs. It does not exercise a
database, import idempotency, browser replay or interruption recovery.

```sh
python3 -m unittest discover -s database/tests -p test_study_snapshot_compare.py -v
python3 - <<'PY'
import copy, importlib.util, json, pathlib, re, subprocess, sys, tempfile
root = pathlib.Path.cwd()
document = (root / 'docs/architecture/study-first-reduction-2026-10-21/MIGRATION-MAP.md').read_text()
def example(label):
    return json.loads(re.search(r'<!-- synthetic-' + label + r' -->\s*```json\s*(.*?)\s*```', document, re.S)[1])
before, after = example('before'), example('after')
tool = root / 'database/tooling/study_snapshot_compare.py'
spec = importlib.util.spec_from_file_location('compare', tool)
compare = importlib.util.module_from_spec(spec); spec.loader.exec_module(compare)
baseline = copy.deepcopy(before)
assert compare.compare_snapshots(before, after)['equal']
assert compare.compare_snapshots(before, after)['equal'] and before == baseline
with tempfile.TemporaryDirectory(prefix='tempo-history-map-') as directory:
    left, right = pathlib.Path(directory) / 'before.json', pathlib.Path(directory) / 'after.json'
    left.write_text(json.dumps(before))
    def check(value, expected, group=None, path=None, missing=None):
        right.write_text(json.dumps(value))
        result = subprocess.run([sys.executable, str(tool), str(left), str(right)], capture_output=True, text=True)
        assert result.returncode == expected, result.stderr or result.stdout
        if group:
            difference = json.loads(result.stdout)['entities'][group]
            if path: assert path in difference['changed'][0]['paths'], difference
            if missing: assert difference['missing'] == [missing], difference
    check(after, 0)
    cases = [
        ('schedules', ('cards', 'due_date'), '2030-01-10', '/cards/due_date'),
        ('operation_receipts', ('operation_receipts', 'payload_json'), '{"synthetic":"changed"}', '/operation_receipts/payload_json'),
        ('browser_intents', ('intent', 'workspace'), 'https://other.invalid:study:synthetic-workspace', '/intent/workspace'),
        ('cards', ('frozen_opening_ranks', 0, 'memberships_json'), '["synthetic-repertoire-a"]', '/frozen_opening_ranks/0/memberships_json'),
        ('queue_attempts', ('first_issued_provenance',), 'invented-from-receipt', '/first_issued_provenance'),
    ]
    for group, route, replacement, path in cases:
        changed = copy.deepcopy(after); value = changed['entities'][group][0]['data']
        for member in route[:-1]: value = value[member]
        value[route[-1]] = replacement; check(changed, 1, group, path)
    changed = copy.deepcopy(after); changed['entities']['queue_attempts'][1]['data']['study_attempts']['answer_json'] = '{}'
    check(changed, 1, 'queue_attempts', '/study_attempts/answer_json')
    changed = copy.deepcopy(after); changed['entities']['reviews'] = []
    check(changed, 1, 'reviews', missing='synthetic:review:101')
    changed = copy.deepcopy(after); changed['entities']['cards'][0]['data']['repertoire_cards'].reverse()
    check(changed, 1, 'cards', '/repertoire_cards/0/repertoire_id')
    changed = copy.deepcopy(after); changed['entities']['cards'] *= 2; check(changed, 2)
    changed = copy.deepcopy(after); changed['unresolved'] = ['unapproved-shared-policy-alias']; check(changed, 2)
    changed = copy.deepcopy(after); del changed['entities']['introductions']; check(changed, 2)
print('12 CLI scenarios and 2 equal/nonmutation API checks passed; synthetic only.')
PY
npm run check:conflicts
git diff --check
```

## Inputs still needed before an expert can sign #389

1. Updated #358 host/database fingerprint and both devices' actual origin,
   frontend/service-worker build, Learning workspace and authority registry.
   October 23 evidence is not a final fenced snapshot or a browser inventory.
2. Authorized disposable snapshot with schema/manifest checksums, complete
   retained-history row/column inventory, sequence state, referenced definition
   closure, frozen-order/transition state and private browser/server pending
   exports. Keep actual records, credentials and private identities out of Git.
3. Reviewer-approved authority/card/revision/route/decision/attempt identity map,
   explicit C destinations and all B decisions, including shared memberships,
   old prefix granularity, unknown issuance and nullable/legacy receipt shapes.
4. Independently extracted golden BEFORE/AFTER, reviewed exact serializers,
   interruption/repeated-import and original-authority replay receipts on that
   disposable snapshot. Comparator equality alone is insufficient.
5. Only then release separate bounded converter leaves for content/identity,
   reviews/schedules/introductions, server receipts/queue/teaching and browser
   envelopes. Each needs exact files, approved expected output and an owner test.
   Launcher implementation and any authorized live cutover remain separate gates.

Architecture impact: none. This file documents existing source fields and
unverified preservation requirements; it changes no schema, runtime interface,
dependency, state owner, transaction, launcher or deployment.

[cutover]: https://github.com/aaweaver-actuary/tempo/blob/bb073b6482fa97c5780118ff4fd52a395a8d2c21/docs/architecture/study-first-reduction-2026-10-21/LAUNCHER-CUTOVER.md
[l1]: ../../../database/tempo_legacy_database/ddl/migrations/001_initial.sql
[l16]: ../../../database/tempo_legacy_database/ddl/migrations/016_phone_review_receipts.sql
[l17]: ../../../database/tempo_legacy_database/ddl/migrations/017_operation_recovery.sql
[l18]: ../../../database/tempo_legacy_database/ddl/migrations/018_operation_attempt_ownership.sql
[l24]: ../../../database/tempo_legacy_database/ddl/migrations/024_opening_segmentation.sql
[l25]: ../../../database/tempo_legacy_database/ddl/migrations/025_tactic_capture.sql
[l29]: ../../../database/tempo_legacy_database/ddl/migrations/029_repertoire_daily_limits.sql
[l30]: ../../../database/tempo_legacy_database/ddl/migrations/030_opening_decision_evidence.sql
[l31]: ../../../database/tempo_legacy_database/ddl/migrations/031_defensive_analysis_pause.sql
[l32]: ../../../database/tempo_legacy_database/ddl/migrations/032_queue_attempt_origins.sql
[l33]: ../../../database/tempo_legacy_database/ddl/migrations/033_canonical_prefix.sql
[l35]: ../../../database/tempo_legacy_database/ddl/migrations/035_canonical_publication_freshness.sql
[l37]: ../../../database/tempo_legacy_database/ddl/migrations/037_card_deletion.sql
[l38]: ../../../database/tempo_legacy_database/ddl/migrations/038_prefix_transition_application.sql
[l42]: ../../../database/tempo_legacy_database/ddl/migrations/042_queue_attempt_issuance.sql
[l47]: ../../../database/tempo_legacy_database/ddl/migrations/047_study_only_mode.sql
[l48]: ../../../database/tempo_legacy_database/ddl/migrations/048_frozen_opening_order.sql
[l50]: ../../../database/tempo_legacy_database/ddl/migrations/050_learning_remediation.sql
[n1]: ../../../database/tempo_learning_database/ddl/migrations/0001_core.sql
[n2]: ../../../database/tempo_learning_database/ddl/migrations/0002_evidence_revisions.sql
[n3]: ../../../database/tempo_learning_database/ddl/migrations/0003_content_bridge.sql
[manifest]: ../../../database/tempo_learning_database/ddl/migrations/manifest.json
[r1]: ../../../database/tempo_remediation_database/ddl/migrations/001_remediation.sql
[translator]: ../../../tools/analysis-restoration/a_bridge/src/tempo_a_bridge/legacy.py
[observation]: ../../../tools/analysis-restoration/a_bridge/src/tempo_a_bridge/legacy_observation.py
[operator]: ../../../tools/analysis-restoration/a_bridge/src/tempo_a_bridge/legacy_study.py
[intents]: ../../../database/browser-intents/src/index.ts
[legacy-browser]: ../../../database/browser/study-intent-storage.ts
[authority]: ../../../app/lib/study-connection.ts
[browser-read]: ../../../database/browser/study-connection-storage.ts
[reconcile]: ../../../database/tempo_learning_database/adapters/review_reconciliation.py
[admission]: ../../../database/tempo_learning_database/adapters/admission.py
[frozen]: ../../../database/tempo_learning_database/adapters/frozen_order.py
[publication]: ../../../database/tempo_learning_database/adapters/publication_bridge.py
[gateway]: ../../../services/learning/runtime/src/tempo_learning_runtime/gateway.py
[offline-types]: ../../../app/domain/offline-training.ts
[journal-types]: ../../../app/domain/opening-evidence-journal.ts
[evidence-types]: ../../../app/domain/opening-evidence.ts
[journal-storage]: ../../../database/browser/opening-evidence-journal-storage.ts
[review-outbox]: ../../../app/lib/review-outbox.ts
[operation-status]: ../../../app/lib/operation-status.ts
