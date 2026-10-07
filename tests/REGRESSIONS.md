Issue #79 — read-only shorter-prefix transition planning (October 7, 2026):

- `backend/tests/test_prefix_transition.py`: `test_issue79_equal_depth_and_empty_selection_are_explicit_no_ops`; `test_issue79_depth_only_shortening_is_distinct_from_no_op`; `test_issue79_selected_caro_routes_preserve_unselected_qgd_and_alias_decisions`; `test_issue79_shared_card_retains_other_membership_history_queue_and_owner`; `test_issue79_implicit_authored_owner_retains_card_without_explicit_other_link`.
- Identity/history: `test_issue79_compatible_targets_reuse_real_history_and_keep_seed_distinct`; `test_issue79_compatible_authored_checkpoint_target_preserves_its_authoritative_kind` (failed before the schema repair exposed by the real deployment); `test_issue79_replacements_start_with_postgres_defaults_without_fabricated_evidence`; `test_issue79_pending_active_delayed_and_offline_attempts_have_explicit_dispositions`.
- Fail-closed conflicts: `test_issue79_authored_edits_targets_and_provenance_fail_closed` (authored removal, incompatible color, owner fallback, archival, and scheduling); `test_issue79_integrity_conflicts_fail_closed_with_specific_repair_identity`; `test_issue79_saved_split_bypass_blocks_without_repair_or_history_transfer`; `test_issue79_shared_target_global_role_change_blocks_even_with_matching_identity`; `test_issue79_lengthening_and_invalid_selection_never_produce_an_applicable_plan`.
- Determinism/freshness: `test_issue79_same_snapshot_and_input_produce_deeply_immutable_identical_plans`; `test_issue79_reordered_selection_tables_and_rows_produce_identical_plan`; `test_issue79_shared_split_blockers_remain_deterministic_when_override_order_changes` (failed before canonical blocker ordering); `test_issue79_source_graph_revision_and_transition_state_invalidate_old_plan` (source, generation, revision, membership, reviews, seeds, attempts, absent targets, day); `test_issue79_tampered_plan_fails_integrity_fence`.
- `backend/tests/test_prefix_transition_api.py`: `test_issue79_http_plan_is_typed_deterministic_and_has_no_application_authority`; `test_issue79_http_invalid_or_lengthening_requests_fail_without_partial_plans`; `test_issue79_http_rejects_stale_sources_and_state_changes_during_planning`; `test_issue79_http_conflicting_authored_membership_returns_complete_blocked_plan`; `test_issue79_http_outage_and_foreground_preemption_are_retryable_without_false_success`; `test_issue79_capture_bounds_raw_transfer_and_hashes_only_after_transaction_close`; `test_issue79_runtime_guard_is_background_query_only_without_command_dispatch`.
- Regular disposable PostgreSQL durability, `scripts/check_postgres_opening_segmentation.py`: `test_issue79_reader_only_deployed_api_plans_without_product_writes` runs the real POST through the reader-only API and compares product state; `test_issue79_readonly_planner_foreground_concurrency_and_stale_replay` proves explicitly read-only repeatable snapshots, idle readers during computation, a real NOWAIT foreground review, rejection of the now-stale plan, and fresh retry with no planner writes; `test_issue79_pending_command_bindings_are_accounted_before_delivery` covers direct reviews and nested checkpoint/study payloads in retained operation receipts. Existing #77 proofs remain required. These are feature regressions, not a repair of an existing planner.

PR #72 integration with main #69/#83 (October 5, 2026):

- `test_queue_origin_migration_follows_current_main_without_renumbering_published_versions` checks contiguous numbering/readiness and preserved published 030/031. `test_postgres_current_main_schema31_upgrade_adds_queue_origins_without_changing_evidence` in the regular durability upgrade rehearsal proves real 31->32 backfill and unchanged evidence contexts.
- `test_foreground_review_http_preparation_keeps_foreground_request_lease` now covers ordinary and reconciliation endpoints; `test_review_requeue_context_correction_only_runs_for_fresh_review` covers both handlers. Reconciliation cases first failed for absent preparation/completion/context handling.
- `test_reconciliation_conflict_rolls_back_new_evidence_and_preserves_saved_checkpoint` first failed when reconciliation bypassed evidence; it fences completion writes before returning an explicit conflict. `test_reconciliation_digest_ignores_only_derived_preparation` first failed for derived-manifest transport identity.
- `test_postgres_retired_opening_evidence_reconciliation_is_atomic_and_replay_safe` in `scripts/check_postgres_queue_attempt_recovery.py`, called by regular durability, proves retired queue recovery after failed transport, one aggregate review/observation/completion, unchanged logical ID/time/result, immutable reinforcement color, replay with same/new transport IDs, and changed/unprovable conflicts preserving earlier capture with no partial writes. Evidence remains available for the existing recreation/backup digest checks.
- `test_pre_evidence_aggregate_receipt_replays_without_inventing_a_new_result` first failed because an absent evidence field became JSON null; legacy aggregate receipts retain their original payload shape.
- `reconciliation preserves valid opening evidence and original result across retry and reload` first failed for omitted completion envelopes. Both branches' outbox/storage/fallback regressions remain; `unresolved guided review remains inspectable when reconciliation rejects it` adapts main's retained-result assertion to the explicit conflict state. Flush-result and guided-marker-key expectations use #72's existing contracts.
- The existing pinned `review-conflicts-390` baseline now incorporates main #86's phone heading, board placement and compact page height. Expected/actual/diff from CI 37277824545 were manually reviewed: dialog geometry, text, saved result and controls are unchanged; only the surrounding already-approved main phone layout differs. The desktop baseline, screenshot thresholds and interaction assertions remain unchanged.
- `operation receipt polling honors abort with %s options` proves foreground/background/custom-fetch signal forwarding. `classified operation failure preserves structured detail message and status notification` preserves options/notifications plus status/code/retryability. Existing delivery deadlines, marker locks, offline conflict exclusion and evidence completion remain required.

PR #72 repair: displayed guidance and phone fallback (October 3, 2026):

- `test_postgres_guided_marker_locks_displayed_revision_until_commit` in `scripts/check_postgres_queue_attempt_recovery.py` pauses the production marker immediately after validation, observes a separate editor's PostgreSQL lock wait, retains R1 guidance while R2 remains unassisted, and proves fresh review idempotency. Its edit-first ordering rejects the stale R1 marker without marking R2.
- `test_card_revision_retains_original_guided_failure_without_guiding_new_content` in `backend/tests/test_queue_attempt_recovery.py` covers sequential queued/blocked/completed projections and replacement of an already-installed SQLite revision trigger on repeated initialization. `test_sqlite_revision_trigger_replacement_rolls_back_on_install_failure` proves failed installation leaves the original trigger installed.
- `iphone fallback applies conflict card identity pending pair identity and unacknowledged phone exclusions` in `desktop-queue-regressions.test.ts` covers the actual fallback hydration, exact pending identity, cross-revision conflicts, unacknowledged versus acknowledged phone results, connected grading and eligible counts. `offline eligibility preserves only linked unchanged reinforcement while retaining conflicts` retains the existing local repeat contract without admitting revised/unrelated/conflicted continuations. The same module-local rule runs after offline completion as well as every refresh.
- `iphone offline fallback excludes a conflicted card across revision and queue changes` in `phone-offline-training.spec.ts` reloads the real iPhone home-screen fallback during an API outage, retains A unchanged, trains only independent B, and proves loading/completion/reload create no new A result.

# Reported issues and regression coverage

Stale queue attempts and poisoned training-review replay (October 3, 2026):

- Original failures reproduced on remote main `2b677af`: `terminal stale A remains inspectable while independent B and C persist`, `replay rejection identifies old A rather than the current B`, and `refresh removing an active opening preserves its board and attempt for completion`; all three failed before implementation. Backend `test_limit_reconciliation_deleted_attempt_remains_saveable_and_idempotent` failed with HTTP 409 after the actual production limit-reconciliation deletion.
- Outbox (`tests/unit/review-outbox-regressions.test.ts`): the original A/B/C and attribution cases; `legacy reload persists deterministic identity without inventing a completion time`; `explicit conflict retry changes transport identity while preserving completed evidence`; `same-card dependent result waits while an unrelated card persists`; `failed atomic conflict write preserves A and pauses B without data loss`; `deferred failed receipt retains conflict classification and reconciles the same attempt`; `unprovable guided result becomes a durable nonblocking conflict`. Existing 503 stable-identity, guided-marker, timeout, deferred-operation, and concurrent-append cases remain required; concurrent flushes now also prove single-flight replay. `legacy identity storage failure attributes the retained result before any request` and `review timeout also bounds deferred operation receipt polling` protect preflight storage and deferred-request boundaries.
- Active attempts (`tests/unit/attempt-lifecycle-regressions.test.ts`): `reload retains an unprovable result as a conflict and opens independent cards`; `repeated refreshes retain an active attempt until explicit advancement`; the original active-opening and existing superseded-response/pending-reply cases remain required. `refresh preserves active %s state and identity when the queue projection disappears` covers player turn, pending reply, guided play and feedback pause.
- Debug export: `review debug export retains failed A identity and machine classification independently of the visible board` in `tests/unit/debug-reporting-regressions.test.tsx`.
- Dialog/notifications (`tests/unit/review-conflict-ui-regressions.test.tsx`): `online conflict dialog exports original evidence and discards only the selected record`; `online conflict dialog explicitly retries the immutable logical attempt`; `old A failure is attributed to A while current B remains displayed and conflicts never report saved`.
- Backend (`backend/tests/test_queue_attempt_recovery.py`): `test_saved_attempt_receipt_precedes_archived_card_and_deleted_projection`; `test_logical_attempt_id_reuse_rejects_changed_payload`; `test_missing_attempt_requires_retained_matching_provenance`; `test_stale_attempt_never_credits_changed_archived_or_blocked_content`; `test_live_projection_cannot_authorize_changed_content_without_revision_bump`; `test_retired_unchanged_projection_reconciles_after_integrity_repair`; `test_retained_guided_failure_requeues_once_after_projection_deletion`; `test_recovered_review_admission_does_not_invent_a_new_introduction`; `test_buried_projection_is_an_explicit_conflict`; `test_conflict_http_contract_keeps_code_retryability_and_readable_detail`; `test_sqlite_origin_backfill_runs_once_and_survives_reinitialization`. `test_recovered_projection_retains_completion_for_competing_review` and `test_recovered_result_never_writes_another_cards_replacement_projection` first failed during the recovery audit, then passed after retaining canonical completion and fencing projection writes by card. Existing phone competing-review/chronological tests remain required.
- SQLite study upgrade: `test_study_migration_preserves_queue_attempt_provenance_and_triggers` first reproduces the card-table rebuild failure, then verifies unchanged origin evidence and surviving revision/update/delete triggers. Existing `test_study_migration_preserves_legacy_cards_reviews_and_foreign_keys` and `test_study_migration_failure_rolls_back_and_backup_restores` remain required; full CI first caught this compatibility boundary.
- API inventory: `test_postgres_route_contract_matches_registered_endpoints` first caught the omitted reconciliation route; `test_staged_postgres_mutations_pass_the_runtime_write_guard` verifies its foreground command classification.
- Real PostgreSQL: `test_postgres_queue_attempt_maintenance_contention_restart_and_receipt_recovery` in `scripts/check_postgres_queue_attempt_recovery.py`, called by regular durability, proves actual limit deletion interleaved with foreground review, independent reads/reviews under contention, canonical and transport replay after reconnect, durable classified errors, unchanged-card recovery from a historical failed receipt, and noncrediting changed-content conflicts. The populated schema-upgrade rehearsal verifies retained backfill after deletion and idempotent migration; `test_postgres_sqlite_queue_import_preserves_origins_without_trigger_duplicates` proves legacy and retained-ledger import. Service recreation compares origin and attempt-receipt contents; the backup drill covers every public table.
- Legacy marker API: `test_legacy_empty_guided_marker_body_preserves_unchanged_context` first failed for the existing `{}` body, then proves absent, empty and identified bodies preserve valid unchanged guidance. The full PostgreSQL study-durability workflow also requires that legacy body.
- Replacement guidance: `test_stale_guided_marker_never_marks_reassigned_queue_content` first failed for both legacy and identified markers; `test_identified_guided_marker_can_mark_only_current_replacement_context` proves valid replacement guidance. Real PostgreSQL `test_postgres_stale_guided_marker_reassignment_and_idempotent_current_context` exercises the production foreground receipt handler. `pending old-card review cannot hide replacement content on the same queue ID` and `old guided marker cannot label replacement content sharing its queue ID` in `desktop-queue-regressions.test.ts` both first failed. `training-failure-outbox-regressions.test.ts` covers displayed identity on stable transient replay, explicit ambiguous conflict without operation rotation, and clearing only the completed card's marker.
- Refresh identity: `same queue ID on replacement content cannot consume the active card's future slot` and `unchanged active content refreshes its priority reason without resetting its board or logical attempt` in `attempt-lifecycle-regressions.test.ts` first failed before adding card identity to the attempt context and refreshing only unchanged content's priority reason.
- Phone refresh: `phone reconnect retains its active card before advancing into the refreshed 241-card queue` and `complete queue reconciliation keeps a removed phone attempt playable and retains its completed conflict` in `phone-offline-training.spec.ts` replace the old replacement/pause expectations with the approved playable-attempt contract. `Games prioritizes a canonical miss and explains the targeted study card` retains unchanged card content while checking refreshed priority metadata.
- Browser: `poisoned online A becomes inspectable while B and C save and conflict retry survives reload` in `training-prefetch.spec.ts`; `queue retirement during a held opening drag preserves active board and accepts one drop` in `cross-browser.spec.ts` (Chromium, Firefox, WebKit). Pinned `review-conflicts-390` and `review-conflicts-1280` cover the combined dialog.

Stranded PGN discard (October 4, 2026):

- `discarding an unknown PGN import permits a different file after reload` in the dialog unit file and `tests/browser/recovery.spec.ts` covers recovery without the original file and a subsequent different upload. The component regression failed before the control existed.
- `discarded PGN confirmation permits a different file with a fresh operation identity`, `lost PGN discard response resolves the original terminal receipt without another POST`, the three `unconfirmed %s PGN discard retains identity` cases, `timed out PGN discard retains identity and ignores a late terminal response`, `stale PGN discard completion cannot erase a newer pending import`, `stale PGN completion cannot replace a newer pending import when a different file is selected`, and `PGN discard preserves an already completed repertoire and validates its result` in `tests/unit/pgn-import-pending-regressions.test.ts` protect terminal clearing, ambiguous delivery and unrelated/newer state. `confirmed HTTP %s PGN failure permits a subsequent different file with a fresh identity` covers validation and durable failures. Existing same-file replay cases remain in that file.
- `test_discarded_pgn_delivery_cannot_restore_its_payload_or_create_repertoire_data` (seven states), `test_pgn_discard_preserves_completed_import_and_rejects_other_command_types`, `test_pgn_discard_route_uses_stable_foreground_command_identity`, and `test_pgn_discard_invalid_route_requests_do_not_dispatch` in `backend/tests/test_postgres_pgn_discard.py` cover the production handlers and API boundary. Route inventory/write-guard tests include the new endpoint.
- `verify_pgn_discard_fencing` in the regular PostgreSQL operation-recovery stage proves real row/advisory locking and the admission/discard completion race. The study-durability stage checks actual HTTP/Celery admission rejection and no repertoire creation before and after service recreation, then successfully imports a different PGN. These are disposable proofs; no live study fixture is used.
- `pending-import-dialog-phone` in the pinned visual suite covers the new recovery control.

Quiet notifications (October 2, 2026):

- Terminal settings transfer failures were falsely resolved and hidden from Needs attention: `settings transfer unavailable warning remains actionable after progress ends`; `settings transfer error remains actionable after progress ends`; `successful settings transfer resolves the same progress record quietly` in `tests/unit/settings-notification-regressions.test.tsx` exercise the actual settings component and tray, retaining one record per operation.
- Workspace refresh progress falsely reported success after an error or an unrelated read succeeded: `failed workspace refresh remains actionable until its own data recovers`; `workspace refresh failure survives remount until matching recovery` in `tests/unit/notification-regressions.test.tsx` preserve the warning through retries/reloads until each failed URL returns ready.
- Routine saves interrupt study: `routine review saves never show popups`; `active work stays in history without a popup and history keeps the latest 500`; browser `review saves stay quiet without moving the board or card`.
- Grouped Clear controls acknowledge all represented notices without resolving independent saves or hiding new arrivals: `clearing grouped notifications preserves independent operations and new arrivals`; the retained notification-clear browser cases inspect cleared history under All after reopening Needs attention.
- Retry warnings flood the window or keep popups alive: `identical retries share one entry without reopening or extending the popup`; `keyed diagnostic repeats keep their popup deadline when details change`; `warning popups stay bounded and expire even while work remains active`.
- Grouping loses independent saves or diagnostic details: `grouped discovery warnings resolve independently`; `confirming one grouped save does not restart the remaining warning popup`; `grouping keeps different sources severities details and resolution states separate`; `stored duplicate warnings group after reload without losing operation identities`.
- Routine and recovered notices obscure actionable failures: `notification history opens to unresolved warnings and errors`; `failed saves remain actionable after saving progress ends`; `guided attempt warnings clear quietly only after pending saves are confirmed`; `guided attempt recovery clears its warning while phone conflicts remain`; browser `phone notification history groups retries and opens to needs attention`.
- The existing settings and offline-phone browser regressions now verify persisted changes and retained history instead of requiring success popups. Existing JSON export, storage-failure and independent discovery-confirmation regressions remain required.
- The Black capture browser case waits for the loaded puzzle before recording its unchanged-board invariant: `Black-first capture keeps its orientation while typed SAN and real-board moves save one solution`. A diagnostic trace captured the empty startup board before the puzzle arrived; the original restoration assertion remains unchanged.

| Issue | Required regression |
| --- | --- |
| Tactic capture restoration test reads the placeholder board before the underlying puzzle loads | `Black-first capture keeps its orientation while typed SAN and real-board moves save one solution` waits for the loaded puzzle before recording the restored FEN |
| A delayed discovery admission handles newer evidence, or a resurfaced revision cannot reuse the same move | `test_stale_discovery_admission_preserves_card_without_handling_new_evidence`; `test_resurfaced_discovery_accepts_same_move_with_revisioned_intent_and_replay`; `test_sqlite_resurfaced_admission_identity_keeps_old_intent_and_same_revision_retries`; `test_postgres_resurfaced_acceptance_keeps_revision_identity_and_same_choice_retries`; `test_sqlite_revisioned_admission_upgrade_preserves_legacy_intents_and_task_references`; `test_postgres_stale_admission_and_revisioned_same_move_replay` in `scripts/check_postgres_upgrade.py` (real PostgreSQL leases, unique constraints, reconnect, queue preservation, and replay) |
| A delayed Train this decision command or old receipt handles an unreviewed evidence revision | `test_direct_discovery_training_rejects_obsolete_evidence_without_handling_current_revision`; `a completed training receipt for older evidence cannot confirm the current revision`; existing obsolete-eligibility and older-confirmation regressions |
| Handled discoveries remain in the inbox or return without new evidence | `test_handled_discovery_clears_feed_persists_and_resurfaces_only_with_material_evidence`; `test_sqlite_handled_upgrade_requires_matching_queued_revision_and_card`; `test_handled_discovery_feed_counts_and_pagination_exclude_completed_items`; `test_discovery_accepted_engine_branch_survives_publication_restart`; `test_handled_discovery_postgres_upgrade_and_material_evidence_replay` in `scripts/check_postgres_upgrade.py`; `confirmed Add and train removes the handled discovery from the review session`; `confirmed Train this decision clears the last item while keeping the viewer open`; `confirmation from older evidence does not clear a newly surfaced discovery`; `dismissal advances through undecided discoveries and clears the final item`; browser `confirmed Add and train clears the completed item after advancing before the save responds`; existing `confirmed failed discovery save remains visible with a retry action` |
| Legacy queued state for revision A hides current revision B during the handled-state upgrade (027 after the current-main rebase) | `test_sqlite_handled_upgrade_requires_matching_queued_revision_and_card`; `test_postgres_handled_upgrade_requires_matching_queued_revision_and_card` in `scripts/check_postgres_upgrade.py`: matching queued intent and card backfills; mismatched, absent, wrong-card, unfinished, preparing, and failed cases remain unhandled; initialization/migrations repeat safely and preserve history |
| Repertoire Opportunities trains unreviewed current evidence after the displayed row becomes stale | `Repertoire train submits the displayed revision and leaves newer evidence unhandled on conflict`; browser `Repertoire Opportunities stale Train submits displayed evidence and preserves the current revision`: the displayed fingerprint is submitted and a conflict leaves newer evidence actionable |
| Missing Train revisions bypass the write guard, including persisted legacy commands | `test_discovery_training_requires_reviewed_revision_before_any_write`; `test_postgres_discovery_train_requires_revision_before_dispatch`: missing/null/empty request revisions reject without writes/dispatch; `test_direct_training_requires_revision_before_opening_write_transaction`; `test_postgres_legacy_train_payload_cannot_write_current_evidence`: legacy service/command payloads reject before database work; `test_postgres_direct_training_requires_current_reviewed_revision` in `scripts/check_postgres_upgrade.py`: real PostgreSQL missing/stale revisions reject, matching training confirms, reconnect/replay preserves one queue row and reviews |
| Legacy fingerprintless browser Train state confirms or resubmits current evidence | `legacy fingerprintless Train pending cannot confirm or resubmit current evidence`; `legacy fingerprintless Train complete cannot confirm or resubmit current evidence`: obsolete actions are cleared before any fetch and require review/click again; `revisionless Train calls are rejected before a request or pending action is created`: runtime guard protects untyped callers; existing `a completed training receipt for older evidence cannot confirm the current revision` retains A/B receipt separation |
| #32: Equivalent last moves and training parent updates disturb board ownership or input | `equivalent last-move values do not cancel a held piece or reapply FEN`; `training parent status updates keep the lease and forward the latest handler exactly once`; `equivalent snapshots suppress store publications while callbacks use current card and step`; `a fixed twenty-update status workload produces no new publication, lease release or reset` |
| #32: Released, remounted, or obsolete publishers overwrite the active workspace | `released and remounted same-owner sessions fence both late updates and late releases`; `an obsolete mounted publisher cannot steal a replacement owner's lease on rerender`; `Strict Mode reacquires once per setup and reapplies the board after construction cleanup` |
| #32: Genuine invalidation retains stale input or queued events grade a replacement card | `same-FEN card changes and same-owner remounts invalidate held input`; `read-only or unavailable input cancels the held move and ignores queued move events`; `a wrong attempt returning to the same FEN explicitly resets through position revision`; `deferred Chessground events from a previous card cannot grade or annotate its replacement` |
| #32: Annotations or editing changes break board interaction contracts | `annotation, highlight, handler and appearance updates preserve drag without reapplying FEN`; `select-only and free editing retain current handlers and disable input when locked`; `Chessground drawing cannot mutate published annotations or shared defaults` |
| #32: A real held piece stops following the pointer during unrelated updates, or a stale drop survives read-only mode | `held training drag survives sync, service, notification and parent updates and drops once`; `a read-only transition interrupts a real held drag and prevents a stale drop` in `held-drag-preservation.spec.ts` |
| #32 / PR #46: Locking or flipping leaves an already-applied speculative move displayed at an unchanged authoritative FEN | `same-FEN locking restores an applied move and fences its deferred callback`; `orientation invalidation restores an applied move before its deferred callback`; `an accepted move remains authoritative after its handler updates FEN` in `board-authoritative-restoration-regressions.test.tsx`, using the real Chessground API and state |
| #32 / PR #46: Guided review feedback locks the board but retains the candidate instead of the finding position | `guided review restores the authoritative finding position after a same-FEN reveal` (correct and incorrect candidates) in `guided-review-board-restoration.spec.ts`, checking rendered piece placement |
| API cannot start when a populated PostgreSQL ledger ends at 16 and the image requires a newer schema | `scripts/check_postgres_upgrade.py` rehearses populated 16→current and repeat migration; `test_postgres_upgrade_rejects_newer_and_gapped_history`; `test_postgres_schema_upgrade_does_not_require_sqlite_snapshot`; `test_failed_postgres_migration_prevents_dependent_rollout` |
| A retry starts a duplicate execution, loses its original identity, or allows a stale attempt to publish | `scripts/check_postgres_operation_recovery.py` exercises the real task entry point against disposable PostgreSQL, including replay, conflict, explicit retry, and stale ownership |
| Preparing discovery confirmations starve later eligible confirmations after reload | `stalled discovery confirmations cannot starve a later queued admission across reloads` |
| A later accepted discovery C waits behind indefinitely preparing A and B | `confirms later discovery C within three eligible flushes while A and B keep preparing` |
| A recovered game job is overwritten by a defensive journal result | `handles a recovered game job before clearing a defensive claim journal`; `a defensive null result cannot overwrite a recovered game job` |
| Diagnostic incident keys or serialized bundles persist a credential | `hydrates legacy secret-bearing incident keys without losing counts or identity`; `removes canary secrets from every persisted and exported incident field` |
| A 250 ms background transaction times out while claiming threats or reading recurring evidence | `test_postgres_threat_claim_checks_sparse_priorities_before_ordered_queue`; `test_postgres_recurring_evidence_uses_per_event_analysis_lookups`; `scripts/check_postgres_upgrade.py` verifies the claim indexes; `scripts/measure_postgres_incident_workloads.py` measures all four reported workloads on an isolated restore |
| Docker Compose warns that the existing Tempo data volume belongs to another project | `test_tempo_data_volume_is_external` in `scripts/test-docker.mjs` verifies Compose resolves `tempo-data` as external and retains its name |
| An in-progress game sync exposes partial worker counters as a completed frontend result, or malformed completion appears successful | `test_incomplete_game_sync_never_exposes_internal_counters_as_completed_result`; `test_completed_game_sync_rejects_partial_result_in_public_model`; `backend sync serialization matches strict frontend status contract across progress and completion` |
| Two indefinitely preparing discovery admissions starve a later unsent save, or an old oversized key cannot recover safely | `two indefinitely preparing admissions cannot starve a later unsent discovery`; `a large saved discovery backlog receives bounded submission service`; `legacy oversized stored operation key is repaired only after its confirmed rejection`; `uncertain invalid stored operation key remains intact with an actionable error` |
| Validation failures are double-reported, attributed to a display label, or merged with unrelated errors after reload | `one validation exception reports once with its HTTP endpoint and resolves after valid status`; `does not reuse notification identity after debug module reload`; `repeated incident observations keep first seen history and count occurrences` |
| PostgreSQL background timeout leaks through a pool, masks rollback, or aborts a bounded populated workload | `scripts/check_postgres_background_budget.py` checks transaction-local timeout, rollback, and reuse; `scripts/check_postgres_background_workloads.py` commits a threat claim, recurring evidence read, 864-key position read, and bounded priority retention on populated fixtures |
| Personal-priority evidence reads every position in one PostgreSQL transaction | `test_personal_evidence_reads_864_positions_in_bounded_complete_sections` |
| A lost command response or worker restart loses its receipt, a duplicate runs twice, or retry exhaustion stays an unexplained spinner | `scripts/check_postgres_operation_recovery.py` checks retry, restart, conflict, and blocked replay; `test_blocked_operation_retry_reuses_durable_identity_and_payload`; `returns blocked operation details without discarding an uncertain engine command` |
| An unresolved defensive claim prevents ordinary analysis or loses its stable operation ID during journal migration | `moves a legacy unresolved defensive claim to its dedicated durable journal`; `reports a blocked browser operation without losing its original identity` |
| A similar London card recommends `Bxc4` while the pictured `...Nf6` card recommends `Nc3`; comparison must preserve that difference, exact move-order routes, and the active training attempt | `London comparison keeps Nc3 distinct from the nearby Bxc4 decision and retains transposed card routes`; `test_comparison_cards_returns_active_routes_and_all_repertoire_links`; `four comparison boards retain distinct routes and independent ply navigation`; `training comparison stays concealed until a mistake and returning preserves the attempt`; `comparison card service failure gives a retry without sample matches` |
| PostgreSQL defensive threat scan exhausts retries because its candidate upsert refers ambiguously to the existing source fingerprint | `PostgreSQL defensive candidate upsert replays and resets changed evidence` in `scripts/check_postgres_threat_candidate_upsert.py`, run by `scripts/test-postgres-docker.mjs` |
| The PostgreSQL maintenance image cannot start the schema migration or SQLite import because it omits the shared schema-version module | `PostgreSQL maintenance image starts migration and import commands` in `scripts/test-postgres-docker.mjs` |
| Cutover verification hashes PostgreSQL-only publication columns and falsely reports a mismatch with the SQLite source | `test_postgres_cutover_digest_ignores_new_target_columns` |
| Backup restore verification fails after source-column projection is introduced or omits PostgreSQL-only receipt columns | `test_postgres_backup_comparison_projects_postgres_only_columns` |
| Compose splits the recurring PostgreSQL backup shell loop into two arguments and leaves backups restarting | `recurring PostgreSQL backup loop has valid shell syntax` in `scripts/test-postgres-docker.mjs` |
| PostgreSQL game exclusion is rejected by the route guard or writes without a foreground receipt and durable refresh intents | `test_postgres_game_exclusion_uses_foreground_receipt_and_atomic_followup` |
| Game exclusion reports a pending Celery receipt as a completed save or replays it with a new command ID | `game exclusion keeps its operation ID and never treats a pending receipt as saved`; `a different game exclusion decision waits for the prior receipt` |
| A manual defensive scan writes an intent in the API or its browser treats a pending command as queued | `test_postgres_manual_threat_refresh_admits_scan_through_foreground_receipt`; `manual defensive scan retains its operation ID until admission is confirmed` |
| Game position derivation holds a database transaction across chess replay, publishes after a stale lease, or loses the final legal position | `test_postgres_game_position_index_yields_to_foreground_and_replays_safely`; `test_postgres_game_position_index_retains_final_legal_position_on_bad_move` |
| A derivation deadlock or serialization conflict consumes its retry budget and fails background work during foreground activity | `test_postgres_derivation_lock_conflicts_yield_without_failing_task` |
| Game analysis commits a derivation row without a restartable index task after a worker interruption | `test_postgres_analysis_followup_checkpoints_derivation_before_index_admission` |
| A partially indexed game becomes visible to repertoire or insight readers before its full position generation is ready | `test_postgres_game_position_import_keeps_legacy_view_until_generation_switch`; `test_postgres_game_position_index_rejects_incomplete_stage_before_visibility_switch` |
| Repertoire comparison exposes a partial match/event set, replays after its lease changes, or publishes mixed repertoire source versions | `test_postgres_game_repertoire_import_keeps_three_legacy_views_until_publication`; `test_postgres_game_repertoire_stages_one_item_and_switches_all_views_together`; `test_postgres_game_repertoire_restarts_on_source_change_without_publishing` |
| Repertoire chess computation holds a database section and delays a foreground request | `test_postgres_game_repertoire_foreground_can_run_during_chess_computation` |
| Loading all repertoire cards in one PostgreSQL background read exceeds its 50 ms transaction limit | `test_postgres_repertoire_index_reads_bounded_pages_and_reuses_source_digest` |
| PostgreSQL game follow-up enqueues a defensive scan that no Celery worker claims, or a stale scan writes after its lease changes | `test_postgres_threat_scan_prepares_outside_transaction_and_rejects_stale_lease` |
| A defensive scan writes every detected seed in one PostgreSQL transaction and blocks foreground work | `test_postgres_threat_scan_publishes_one_seed_per_restartable_slice` |
| PostgreSQL API startup leaves today's queue refreshing forever because it never requests the durable queue task | `test_postgres_api_startup_requests_todays_queue_through_foreground_command` |
| Browser activity is blocked by the PostgreSQL write guard, allowing background slices to begin during active study | `test_postgres_browser_activity_extends_cross_process_foreground_admission` |
| An API left running across midnight never creates the next day's PostgreSQL queue | `test_postgres_daily_queue_rollover_requests_refresh_until_ready` |
| A queued defense card cannot save recognition or grading after PostgreSQL cutover, or the two submissions reuse one receipt ID | `test_postgres_defense_answers_dispatch_atomic_foreground_commands` |
| A stale defensive answer becomes an HTTP 500 after moving through a Celery receipt instead of retaining its actionable conflict | `test_postgres_defense_stale_answer_preserves_conflict_status` |
| A dedicated tactic attempt finishes in the browser without a PostgreSQL command receipt, or accepts an ambiguous save without a stable attempt key | `test_postgres_tactic_attempt_dispatches_validated_foreground_command` |
| Tactical pack activation bypasses Celery, returns a pre-commit catalog, or retries a pending save with a new key | `test_postgres_tactic_activation_dispatches_and_reads_committed_catalog`; `tactic activation pending command reuses its key across retry` |
| PostgreSQL teaching-state saves bypass the command queue or replay changes their timestamp | `test_postgres_cutover_teaching_state_dispatches_and_replays_saved_timestamp` |
| A teaching save is lost when the command response is pending or the service fails | `teaching save survives an ambiguous response and replays with the same command ID`; `failed teaching save remains queued for a later retry` |
| The service-outage visual check becomes ambiguous when the notification tray also exposes an alert | `service-unavailable` in `tests/browser/visual.spec.ts` targets the review-history alert explicitly |
| Concurrent main-repertoire changes bypass the command queue and leave an ambiguous selection | `test_postgres_cutover_main_repertoire_selection_uses_one_locked_command` |
| PostgreSQL queue refresh can hold a long background transaction, start during foreground work, or repeat a stale eligibility slice after restart | `test_postgres_queue_refresh_eligibility_slices_yield_and_restart_without_replay`; `test_postgres_queue_refresh_foreground_request_blocks_new_database_slice` |
| Tactical queue preparation holds a database transaction during packaged puzzle file work, starts during foreground study, loses a timed-out read, or replays a stale reservation | `test_postgres_tactical_queue_prepares_outside_database_and_retries_timed_out_read`; `test_postgres_tactical_queue_foreground_contention_and_stale_replay` |
| Sliced opening reset changes reviewed or currently queued cards, or replays with different results | `test_postgres_queue_opening_reset_phases_are_idempotent_and_preserve_active_cards` |
| Queue reconciliation drops or skips unseen cards when split into restartable PostgreSQL slices | `test_postgres_queue_unseen_reconciliation_matches_sqlite_and_survives_reordering`; `test_postgres_queue_reconcile_checkpoint_discards_stale_replay` |
| Due-card queue slices admit blocked, unpublished, archived, future, or already queued cards | `test_postgres_due_queue_slices_admit_only_eligible_cards_in_order` |
| Cutover inventory omits PostgreSQL-native SQL calls and leaves migration sites unclassified | `test_postgres_cutover_inventory_includes_native_postgres_queries` |
| Full validation or a focused browser scope runs tests and builds before discovering that Docker or localhost binding is sandbox denied | `full verification checks Docker and loopback access before any test family`; `focused browser and Docker Make targets preflight before launching tests` |
| Docker daemon access succeeds but the pinned visual container cannot see the checkout, causing a late failure after other test families | `pinned browser preflight detects an inaccessible checkout mount before tests` |
| Standalone Stockfish smoke preempts itself when a slower search reaches the foreground API poll without an API server | `standalone defense engine smoke does not poll an unavailable API` |
| Lint walks Git-ignored local checkout copies and fails on their bundled third-party engine files after unit and backend stages pass | `lint scope excludes ignored local checkout copies` |
| A new notification toast gives a service failure two alerts, making the pinned `service-unavailable` visual test ambiguous | `service-unavailable` checks the actionable inline database error specifically |
| Extra header controls crowd or overlap laptop navigation at 1280px, or a compact-menu fix hides existing desktop destinations | `laptop header navigation and actions remain separate`; `analysis activity count fits header controls at 1280`; `defensive daily stack setting persists across navigation and reload`; `Edit card opens Builder line-removal context and deletes the selected branch` |
| TypeScript position matching rejects a noncapturable en passant square that Rust canonicalizes away | `TypeScript position distance matches the shared Rust parity fixture`; `rust_position_distance_matches_typescript_shared_fixture` |
| Rust card IDs canonicalize noncapturable en passant while persisted Python card IDs retain the supplied FEN field | `card_ids_match_python_shared_fixture`; `test_python_card_ids_match_shared_rust_parity_fixture` |
| Repertoire statistics issues two forecast-input reads for every distinct parent card | `test_repertoire_statistics_batches_forecast_parent_reads` |
| PGN import retraverses each line's decision segments just to count merged duplicates in its response | `test_import_derives_decision_segments_once_per_parsed_line` |
| PGN import reports only request total time and cannot separate parsing, decision derivation, storage, and scheduling | `test_import_reports_parse_derive_and_storage_phase_timings` |
| PGN import reports success when persisted lines cannot be scheduled for their background rebuild | `test_import_scheduler_failure_reports_saved_data_and_retry_action` |
| PostgreSQL PGN import bypasses the worker or loses its idempotency key | `test_postgres_pgn_import_dispatches_parsed_payload_with_idempotency` |
| An unknown PGN receipt strands the selected import or recovery replaces its identity | `unknown PGN receipt replays the exact file and settings with its original operation ID`; `lost PGN POST and unavailable status preserve identity for later same-ID replay`; browser `unknown PGN receipt recovers the same operation after reload and matching file reselection` |
| Durably active PGN imports are resent or reported as failed | `durably %s PGN import polls the same operation without a POST or new UUID` covers pending without the legacy diagnostic, queued/executing/retrying; `active PGN polling expires after thirty seconds and a later check uses the same operation`; `hung PGN status transport respects the budget and late completion cannot erase identity`; browser `durably executing PGN import waits informationally and Check again never resends` and its retrying case |
| Legacy pending PGN receipts hide their no-payload diagnostic behind polling or lose identity on Check again (PR #68) | `legacy pending PGN receipt returns its diagnostic without polling or replay`; `rechecking legacy pending PGN import only reads its original operation`; `PGN polling stops when a legacy no-payload pending receipt appears`; component `legacy pending PGN dialog retains its diagnostic and selection across Check again`; browser `legacy pending PGN import promptly shows its diagnostic and Check again only inspects the original operation` |
| Receipt errors erase recovery identity or permit replacement by a different PGN | `different PGN fingerprint cannot replace an unresolved %s import` covers all six unresolved states; `%s status failure preserves PGN identity without guessing unknown or resending` covers transport/HTTP/malformed/unsupported receipts; `unreadable PGN admission acknowledgement (%s) resolves the original receipt without another POST` covers invalid JSON and missing/invalid identity; `invalid complete PGN result retains recovery identity instead of claiming success`; completed/failed stored receipt cases; `failed previous PGN import reports its failure before a different deliberate import can start`; `terminal previous PGN import permits a different file after resolving its receipt`; pre-admission HTTP 400/422 and HTTP 500 durable-failure cases in `tests/unit/pgn-import-pending-regressions.test.ts` |
| A blocked PGN automatically retries, loses its diagnostic, or ends checking on a stale blocked acknowledgement | `blocked PGN import preserves identity and surfaces the actual diagnostic without automatic retry`; `explicit blocked PGN retry keeps identity and waits beyond its acknowledgement (lost response: %s)`; `blocked PGN dialog shows its diagnostic and explicitly retries the original operation` |
| PGN progress shows red/debug errors, permits duplicate submission, or continues after closing | `PGN dialog locks submission and settings while confirmation is working without a failure notice`; `PGN pending timeout is informational and Check again completes the existing import`; `ambiguous PGN delivery offers informational recovery without claiming a failed import`; `failed PGN dialog remains an actionable error and permits a new deliberate attempt`; `closing PGN dialog cancels confirmation without reporting cancellation as a failure`; `cancelling PGN confirmation stops polling and preserves its pending identity` |
| Reconstructed PGN payloads or HTTP/Celery replay create a second logical import | `test_postgres_pgn_import_dispatches_parsed_payload_with_idempotency` checks deterministic repeated payloads; `verifyPgnImportReplayWithoutDuplicates` in `scripts/test-postgres-docker.mjs` checks real PostgreSQL receipt/result and repertoire/line/card identities and counts before and after service recreation |
| PGN payload measurements hide branch expansion or count the wrong serializer bytes | `test_pgn_payload_diagnostic_counts_branches_moves_annotations_and_serializer_bytes`; `test_branching_pgn_source_grows_linearly_while_expanded_moves_grow_quadratically` in `backend/tests/test_postgres_cutover.py` |
| PostgreSQL guided integrity repair bypasses Celery or loses its idempotency key | `test_postgres_integrity_repair_dispatches_prepared_plan_with_idempotency` |
| Guided repair applies a stale issue signature or locks cards before the graph task, risking a deadlock | `test_postgres_integrity_repair_rejects_stale_issue_signature` |
| Guided PostgreSQL repair deletes a line's custom training depth | `test_postgres_integrity_repair_copies_line_training_depth_before_delete` |
| Pending guided repair appears saved or receives a new command ID on retry | `a pending integrity repair retains its command ID until its receipt completes` |
| Guided repair retry breaks the current SQLite product's signature-deduplicated job | `SQLite integrity repair retries through its signature-deduplicated endpoint` |
| PostgreSQL repertoire edit leaves a previous clean integrity result visible | `test_postgres_repertoire_edit_invalidates_old_clean_integrity_in_same_transaction` |
| PostgreSQL integrity traversal holds a read transaction during chess computation | `test_postgres_integrity_source_closes_read_transaction_before_chess_scan` |
| PostgreSQL integrity source staging loses its cursor or publishes after its lease expires | `test_postgres_integrity_source_stages_one_restartable_slice` |
| PostgreSQL integrity aggregation reads every source in one transaction or loses its position cursor | `test_postgres_integrity_aggregation_merges_two_positions_per_lease` |
| PostgreSQL integrity evaluation stages ordinary single-response positions as conflicts | `test_postgres_integrity_evaluation_stages_only_conflicting_positions` |
| PostgreSQL graph completes before its integrity scan is durable and refreshes an unvalidated queue | `test_postgres_graph_finalization_requests_integrity_before_queue_refresh` |
| PostgreSQL integrity scan publishes results after a newer graph generation supersedes it | `test_postgres_integrity_publication_discards_stale_graph_generation` |
| PostgreSQL integrity task failure leaves the browser waiting without an actionable scan error | `test_postgres_integrity_failure_updates_visible_scan_state` |
| PostgreSQL integrity slice writes during foreground activity or replays after its lease changes | `test_postgres_integrity_foreground_contention_restart_and_stale_replay` |
| Opening-graph rebuild has only whole-task timing, hiding whether input reads, chess calculation, or publication dominates | `test_opening_graph_rebuild_reports_prepare_compute_publish_phase_timings` |
| Every motif detector replays and copies the same candidate line before inspecting it | `test_motif_candidates_replay_each_line_once_for_all_detectors`; `test_python_motif_parity_fixture` |
| Training-card transition latency is not sampled from click through the next visible paint | `warm training cards advance to the next visible paint` |
| Builder similarity only verifies compact worker messages and leaves index initialization or representative query queue/compute/roundtrip latency unmeasured | `Builder similarity worker messages keep the position index in the worker` (250 legal opening lines) |
| Black training and tactic setup tests wait on deterministic UI timers after setup | `Black training mounts without selector loops and plays from the current position`; `tactic setup is applied and the final mate remains during feedback` |
| The full unit stage reports only suite wall time, leaving slow files unidentifiable without repeating the tests | `test plan records per-file Vitest timings without a second unit run` |
| Rust returns a partial card prefix ending before the trained side can play, while Python rejects it | `prefixes_match_python_including_incomplete_trained_turns`; `test_prefixes_match_rust_including_incomplete_trained_turns` |
| A failed Docker browser case prevents later durability checks and recovery reruns the entire browser matrix | `Docker durability recovery excludes only the already-run browser matrix` |
| The full validation path repeats regular Playwright tests locally and through Docker, and combined scopes can repeat suites | `full verification owns every test family once without repeating regular browser specs`; `Makefile runs one full plan and rejects combined verification scopes`; `regular and pinned Playwright plans partition every browser spec without overlap` |
| Pinned visual runs repeatedly download npm packages even though each run still installs a fresh dependency tree | `pinned visual runner caches npm downloads while reinstalling locked dependencies` |
| A focused pinned performance repeat ignores the selected timing directory and overwrites full-run raw samples | `pinned performance reruns retain raw artifacts in a chosen checkout directory` |
| Browser responsiveness reports visible paint but lacks input-delay and handler-duration samples where Event Timing is supported | `warm workspace and Builder move responsiveness` |
| Repeated repertoire FENs waste distance calculations during one query while distinct moves still need stable results | `repeated repertoire FENs retain match ordering and distinct next moves` |
| Shared opening prefixes replay identical chess moves during every worker index build | `shared repertoire prefixes are traversed once without changing indexed line identity` |
| Worker position queries compare candidates with incompatible side, castling, en passant, or material state | `worker compatibility buckets preserve matching across canonical FEN state` |
| Workspace fetch latency is opaque and cached reads could be mistaken for network requests | `workspace fetch records API response and ready latency once per network request` |
| An HTTP workspace failure can lose its status while formatting the endpoint outside a browser | `failed workspace fetch preserves HTTP status and records response without false ready timing` |
| Completed-tactic tests waste real time on deterministic move and feedback timers | `completed tactic advances while the previous review save is still pending`; `failed earlier save blocks grading after a completed tactic until ordered retry succeeds`; `failed tactic review save keeps the next card visible but blocks grading until retry`; `failed next-card read leaves a completed tactic on its final board for retry` |
| Opening graph step FEN keys must remain exact when reused from segment traversal for white and black routes | `test_graph_steps_reuse_exact_last_decision_fen_for_white_and_black_routes` |
| PGN variation traversal must retain nested branches and annotation positions when copying without move history | `test_pgn_variation_copy_without_history_preserves_nested_lines_and_annotations` |
| Tactic UI timer tests spend real wall-clock time waiting for predictable 800 ms callbacks | `tactic Show Move, capture dialog, and Restart retain the guided attempt and classify the opponent reply sound` |
| Database writer logs merge queue, gate, lock, and transaction time into an opaque hold duration | `test_database_writer_logs_queue_gate_lock_and_transaction_phase_timings` |
| Rapid Builder moves queue obsolete similarity searches behind an in-flight worker task | `rapid Builder positions coalesce queued matches to the latest FEN` |
| Builder repeatedly clones the full repertoire position index for similarity queries | `worker position index retains parity and rejects stale revisions`; `worker index protocol accepts canonical lines and rejects raw lines`; `Builder position index replaces and releases revisions without leaking stale matches`; `Builder similarity worker messages keep the position index in the worker` |
| Study worker backlog and computation duration are not distinguishable | `study worker reports queue compute and roundtrip durations` |
| Position similarity search must keep exact and near results in stable order while deduplicating by FEN and next move | `position search retains distance ordering and first duplicate identity` |
| Unrelated Builder rerenders rewrite repertoire selection and serialized session | `Builder unrelated rerender does not rewrite repertoire selection or session` |
| A recurring refresh starves a retried discovery admission behind lower priority derived work | `test_discovery_admission_replay_promotes_stalled_save_ahead_of_recurring_refresh` |
| An accepted but unconfirmed discovery remains stalled after reopen because only status is polled | `accepted discovery save replays its same choice after reopen to unblock preparation` |
| Live game summaries expose a matched position ply that the web rejects as unknown | `game summary accepts the matched position ply sent by the API` |
| Reopening after a discovery timeout leaves a saved failure and shows a red X without a new training action | `legacy failed discovery timeout reopens as an unconfirmed save with the same choice`; `legacy discovery timeout reopens as an unconfirmed save and retries the same choice`; `pending discovery save is checked before startup preview reads`; `reopening a saved guided card without input sends no failure and shows no red X` (unit and browser); `intentional failed attempt survives reload and replays once for its queue entry`; `intentional training failure is saved once and reload resumes it without another failure`; `stale failed-attempt marker cannot fail a different queued entry`; `test_stale_failed_attempt_request_cannot_mark_a_later_queue_entry` |
| A prefetched guided failure reaches the server before the earlier review or is replayed against a later card | `prefetched guided failure waits for the prior review and survives reload`; `queued guided failure 409 retains its marker and rotates a poisoned operation key`; `completed queue entry clears a stale guided marker without another failure`; `confirmed guided review clears its earlier failure marker`; `reload drains an earlier review before marking the next guided card`; `reloaded prefetched guided card waits for the earlier review before marking failure`; `test_queue_entry_state_distinguishes_head_future_and_completed_attempts`; `test_guided_review_after_stale_failure_marking_is_saved_once` |
| Stale guided review replay prevents today's training queue from opening and blames the queue read | `stale guided failure marking still saves the guided review once`; `stale guided failure replay completes before today's training queue opens`; `unresolved guided review remains saved when the review endpoint rejects it`; `unresolved review replay opens other training cards and pauses the pending card`; `unresolved guided review keeps its card paused while other training opens`; `test_guided_review_after_stale_failure_marking_is_saved_once` |
| Safari desktop shows a phone offline queue after a local API failure and can grade against stale cards | `desktop same-day prepared queue cannot replace a failed live request`; `desktop queue failure retains the active attempt and attributes the live request`; `WebKit desktop prepared queue outage preserves the position and blocks grading until Retry` |
| Saved offline review replay is mislabeled as a queue failure and blocks live training | `offline review replay failure is attributed to replay while the live queue opens`; `pending phone review remains saved while live training opens and a conflict remains recorded after reconciliation` |
| Preserved phone review conflicts cannot be inspected or explicitly discarded without changing computer reviews | `nine phone conflicts can be inspected and discarded individually without changing computer reviews` |
| Safari extension `fixinatorInputs` rejection is shown as a Tempo error, while sync status failures recur in diagnostics | `Safari extension fixinator rejection stays in diagnostics without a Tempo error panel`; `captures window exceptions and unhandled rejections`; `automatic game sync backs off after service failure and resumes after recovery`; `game sync status reports a new failure after recovery`; `game sync status failure does not block a successful live training queue` |
| iPhone fallback hides the live service error or rejects a cached shell after reload | `prepared phone queue survives API outage reload and syncs its review` (live failure reason and review replay); `local Tempo shell opens after the network drops` |
| iPhone training stopped after a generic Safari “Script error.” while Docker still served the older web and API builds | `older computer API blocks phone preparation with an update instruction`; `iPhone script error retains safe source coordinates without inventing a cause`; `iPhone script error without source stays explicit about missing evidence`; `iPhone study worker startup failure is actionable and records the safe script path`; `phone preparation waits for the new service worker to control the page`; `Pages service worker caches scoped static assets and bypasses API and external GETs` (shell survives cache pressure and readiness reports missing assets); `local Tempo shell opens after the network drops` (first installation has no update banner); `service worker update during an active phone attempt waits for a safe reopen`; `verify:phone-deployment` rejects the old API and checks the deployed web/API pair |
| Prepared phone queue must survive computer outages and replay credited repeats without overwriting desktop reviews | `test_phone_prepared_queue_replays_repeats_once_at_the_original_study_time`; `test_phone_replay_uses_the_actual_local_study_date_for_scheduling`; `test_phone_prepared_queue_rejects_competing_computer_review`; `test_phone_prepared_queue_rejects_a_card_edited_after_preparation`; `test_phone_prepared_queue_contains_every_entry_beyond_the_live_window`; `repeats an Again after four cards and a first clean pass at the end`; `does not create a clean repeat for an already studied, light, or hard card`; `local Tempo shell opens after the network drops`; `prepared phone queue survives API outage reload and syncs its review`; `yesterday's prepared phone queue never becomes today's training`; `offline guided failure stays guided after reopening the phone app` |
| Phone and desktop show different same-day queue counts because the phone retains an older prepared snapshot | `phone 225-card offline queue reconciles to the desktop 241-card count and next card after reconnect`; `pending phone review remains saved while live training opens and a conflict remains recorded after reconciliation`; `an older prepared response cannot replace a newer saved phone queue`; `complete queue reconciliation keeps a removed in-progress board paused until Retry`; `labels a saved phone queue as offline with its preparation time and pending review count`; `keeps an in-progress phone attempt while a 225-card queue refreshes to 241 cards` |
| Buttons and other click targets outside the chessboard have inconsistent hover feedback | `hover feedback covers controls outside the chessboard without changing the board` in `tests/browser/layout.spec.ts` |
| Slow daily queue read and card-to-card transition | `test_training_queue_window_limits_cards_and_preserves_order`; `advances to a prefetched card before review persistence and retains the total queue count`; `shows the prefetched next training card while the prior review request is still pending`; `next training card paints before the previous review finishes saving` |
| Completed tactic freezes and resets before its review is saved | `queue refresh during completed tactic feedback preserves the final position and attempt token`; `queue refresh removing a completed tactic retains its final board until grading`; `completed tactic survives queue reconciliation before its feedback timer grades it`; `reload during completed tactic feedback replays its durable result once`; `failed tactic review save keeps the next card visible but blocks grading until retry`; `failed next-card read leaves a completed tactic on its final board for retry` |
| Completed tactic freezes while a previous review save is pending | `completed tactic advances while the previous review save is still pending` (unit and browser); `failed earlier save blocks grading after a completed tactic until ordered retry succeeds`; `drains a review appended while an earlier review request is still in flight`; `times out an unresponsive review save and keeps it available for retry`; `timed-out queue read retains a completed tactic for an actionable retry` |
| Navigation times out under overlapping background discovery reads | `slow discovery pagination does not start overlapping background refreshes` |
| Review persistence fails after an optimistic card advance | `retains a failed review for retry after reload without duplicating the queue entry`; `replays failed-attempt marking before grading and preserves order` |
| Background queue reconciliation removes a card during an active attempt | `pauses an active attempt when queue reconciliation removes its entry` |
| Browser Stockfish timeout loses whole game scan and labels engine failure as save failure | `test_stockfish_timeout_resumes_at_unfinished_position_without_saving_partial_game`; `test_docker_game_scan_finalizes_complete_positions_once_with_actual_network`; `test_docker_game_report_rejects_wrong_history_and_depth_without_advancing`; `test_browser_activity_preempts_docker_search_without_database_access`; `test_legacy_browser_network_is_requeued_one_game_at_a_time_without_erasing_analysis` |
| An already-open browser tab can keep claiming full-game scans after the Docker worker is deployed | `test_predeployment_browser_tab_cannot_claim_new_game_analysis_after_docker_rollout` |
| Defensive recognition asks for four unmarked squares at once and hides useful feedback until the move | `guided defensive recognition reveals board arrows only after the assessment`; `validated false alarm ends after explanation without requesting a move`; `test_guided_recognition_reveals_route_after_assessment_and_sound_defense_pass` |
| Discoveries cram several positions into a narrow tray and a coverage gap shows the opponent's board | `discoveries badge waits for a safe break and does not interrupt twice`; `ready discovery queue paginates before navigation and keeps feed order`; `missing-response viewer shows the learner board after the reply and selects one move`; `discovery viewer compares one learner decision and returns from Builder` (phone and desktop); `test_coverage_discovery_prepares_learner_decision_after_uncovered_reply` |
| A post-gap discovery displays the earlier gap while recommending a later move, or a black repertoire faces the wrong way | `test_post_gap_discovery_recommendation_matches_displayed_black_decision`; `test_discovery_rejects_illegal_recommendation_root_with_actionable_reason`; `discovery viewer with a white decision rejects a black recommendation`; `black repertoire discovery keeps black at the bottom through route and preview` |
| Discovery modal includes incomplete recommendations that are not ready for review | `shows only ready discoveries in feed order and does not acknowledge hidden items`; `shows an empty ready queue when every cardless recommendation is unavailable`; `ready discovery queue paginates before navigation and keeps feed order`; `discovery viewer compares one learner decision and returns from Builder` (phone and desktop) |
| Discovery review lacks move navigation and a familiar, engine bounded default; saving a choice stalls the next discovery or can fail silently | `test_discovery_familiar_default_prefers_quality_within_one_hundred_cp`; `test_discovery_same_move_recognizes_comparable_repertoire_position`; `test_discovery_accepted_engine_branch_survives_publication_restart`; `test_discovery_admission_status_reports_terminal_failure`; `discovery board keys inspect route and preview while the familiar default stays selected`; `invalid discovery route falls back to the decision board`; `Add and train advances before saving and preserves the review order after refresh`; `confirmed failed discovery save remains visible with a retry action`; `discovery save outbox survives reload and waits for queued confirmation`; `confirmed discovery rejection stays visible and an explicit retry reuses the same request`; `transient discovery server failure is replayed after reload`; `terminal discovery admission failure remains retryable after acknowledgment`; `discovery viewer compares one learner decision and returns from Builder` (phone and desktop); `Add and train opens the next discovery before the save responds` |
| Discovery save reports a 15-second timeout as failure even though the server may still accept it; move comparisons omit repertoire frequency and close alternatives | `timed out discovery save remains unconfirmed and replays the same choice after reload`; `transient discovery server failure is replayed after reload`; `test_discovery_accepted_engine_branch_survives_publication_restart`; `test_discovery_branch_materialization_keeps_foreground_free_and_replays_once`; `discovery comparison shows repertoire counts, engine tradeoffs, and an eligible alternative`; `discovery comparison keeps mate evaluations separate from centipawn gaps` |
| Discovery saves with two 64-character hashes exceed the PostgreSQL operation limit, old failures stay stuck, and startup can falsely announce confirmation | `legacy rejected discovery saves recover with a bounded key after reload`; `uncertain discovery retries retain one operation key and the same choice`; `accepted discovery readmission after reload uses a new operation key`; `terminal discovery retry uses a new operation key for the same choice`; `failed discovery operation receipt requires a new key on explicit retry`; `discovery replay sends at most two due admissions and prioritizes the new choice`; `discovery confirmation waits for a queued admission`; `legacy long discovery save key recovers and queues the saved choice`; `test_postgres_discovery_terminal_readmission_requeues_existing_intent_once` |
| Recurring discovery popup shows unavailable values for stale persisted analysis evidence | `refreshes stale discovery evidence once and displays the recalculated values`; `describes valid zero-sample analysis without requesting an evidence refresh`; `test_stale_discovery_evidence_refresh_persists_current_fields_and_feed_returns_them` |
| GitHub issue #4: repertoire opportunities must promote a proven weak descendant without fabricating study, surface practical missing replies, and retain post-gap evidence | `test_issue4_strong_route_weak_target_promotes_without_reviews_or_parent_maturity`; `test_issue4_one_game_and_successful_unstudied_decision_do_not_promote`; `test_issue4_transposed_game_routes_aggregate_at_one_canonical_target`; `test_issue4_dismissed_unchanged_opportunity_stays_dismissed_until_material_games`; `test_issue4_personal_common_move_surfaces_without_masters_or_cohort_data`; `test_issue4_post_gap_finding_reuses_existing_card_and_api_lists_it`; `test_issue4_game_finding_records_canonical_opponent_gap_and_engine_consequence`; `test_issue4_covered_and_low_probability_replies_do_not_create_noise`; `test_issue4_background_scan_yields_to_foreground_and_replays_without_duplication`; `issue 4 opportunities explain promotion, degraded sources, and explicit actions` |
| Played captures/checks sound differently by actor, or the checked king has no persistent board cue | `played moves use distinct move, capture, and check recordings and respect persisted sound settings`; `tactic Show Move, capture dialog, and Restart retain the guided attempt and classify the opponent reply sound`; `checked king gets a persistent translucent red cue that clears when the position changes` |
| Failed tactic advances before correction | `tactic failure remains interactive until the full guided solution is complete` |
| Puzzle setup omitted / final move snaps back | `tactic setup is applied and the final mate remains during feedback` |
| Motifs share counters or stale timers | `motif progress is independent and stale completion cannot advance another deck` |
| Repeated clean solves unlock a stage prematurely | `clean progress counts distinct puzzle IDs` |
| Unseen tactical review reveals its answer | `unseen tactic review has no automatic teaching arrow` |
| Training repeats a completed entry / duplicate submissions | `completed queue entry is idempotent and reinforcement schedules into the future` |
| Failed review save advances or loses the completed card | `failed review save retains the completed card for retry` |
| Review retry creates a duplicate review | `test_completed_queue_entry_is_idempotent_and_reinforcement_schedules_into_the_future` |
| Queue refresh failure is reported as a review-save failure | `successful review is not reported as failed when queue refresh fails` |
| Frontend errors do not expose safe, copyable debugging state | `frontend errors show a redacted copyable debug bundle`; `frontend render failures retain a copyable fallback` |
| Retryable SQLite queue contention aborts refresh immediately with an opaque 503 | `retryable queue contention retries before reporting a failure and retains the active attempt` |
| Persisted queue validation metadata is rejected by the strict frontend adapter | `queue cards accept persisted pending-validation state without a diagnostic` |
| Study migration metadata rejects existing queue cards and prevents phone preparation | `queue cards accept study migration metadata from existing opening and tactic records`; `prepared phone queue validates study metadata beyond the live window and keeps offline exercises` |
| Published study exercises have nullable repertoire IDs, a distinct kind, and a prepared snapshot | `published study exercise queue cards retain their nullable repertoire and study identity`; `test_study_queue_window_count_matches_published_prepared_cards` |
| Direct SQLite initialization omits study tables and breaks foreground queue paths | `test_database_initialize_migrates_study_tables_for_direct_queue_users` |
| Web proxy serves HTTP 502 while the API is still starting | `test_tempo_web_waits_for_api_health_before_proxying` |
| Foreground API reads fail through the proxy during repertoire integrity work | `test_foreground_api_reads_survive_integrity_work` |
| A recovered integrity lock remains visible and counts toward permanent failure | `test_integrity_slice_recovers_from_a_transient_database_lock_without_stale_error` |
| Games responses expose SQLite-only sync fields | `test_game_summaries_never_expose_persistence_only_sync_fields`; `backend game response and strict frontend schema remain in parity` |
| First-party Games contract drift silently empties and caches the library | `game contract drift fails once instead of silently emptying the library`; `invalid game responses are never cached as empty success` |
| Corrected Games data requires navigation and old diagnostics flood the UI | `corrected game data replaces stale cache without navigation`; `repeated diagnostics are grouped and clear after successful validation` |
| Repertoire computation holds a SQLite write lock during chess traversal | `test_background_repertoire_computation_holds_no_sqlite_write_transaction` |
| Background contention blocks foreground writes instead of retrying | `test_background_database_contention_retries_without_blocking_foreground_writes` |
| A large derivation backlog blocks card review | `test_correct_review_succeeds_while_one_thousand_derivation_jobs_are_queued` |
| Interrupted derivations duplicate findings or fail to resume | `test_derivation_backlog_resumes_after_restart_without_duplicate_findings` |
| Sync is starved behind the derivation backlog | `test_sync_jobs_are_not_starved_behind_derivation_work` |
| Startup integrity analysis blocks the app and active training/tactics | `test_startup_serves_training_and_tactics_while_integrity_sweep_is_running` |
| Foreground review waits behind a background database slice | `test_foreground_review_preempts_each_background_database_slice` |
| Foreground SQLite requests collide with an already-open background write section | `test_foreground_connection_waits_for_active_background_section` |
| Large integrity scans lose progress or duplicate issues after restart | `test_large_integrity_sweep_commits_and_resumes_one_source_at_a_time` |
| Integrity rescans quarantine a previously clean repertoire or unrelated tactics | `test_last_known_good_repertoire_remains_trainable_during_rescan`; `test_never_validated_repertoire_is_quarantined_without_blocking_tactics` |
| Import bypasses the new-card limit | `legacy introduced-but-unreviewed queue is capped without losing reviews` |
| One sample deletion removes both | `deletion isolates records sharing a source filename` |
| Flipping exits Black repertoire | `builder flip preserves repertoire identity and history across remounts` |
| Local app shows sample games on failure | `test_local_sync_persists_errors_and_success_without_sample_fallback`; `Docker Games shows actual empty records and actionable sync errors, never sample success` |
| Game-sync diagnostics blame the sync endpoint when loading settings fails | `game-sync settings failures identify the settings endpoint` |
| Sync status invisible | `automatic game sync has a visible spinner and reports provider failure` |
| Chess.com mixed-case usernames and shared Site headers collapse games | `test_chesscom_mixed_case_username_and_shared_site_header_import_every_distinct_game` |
| Queued or single-provider game sync results are rejected as missing provider objects | `queued game sync accepts an empty provider map without a diagnostic`; `single provider sync result does not require the other provider`; `malformed queued provider data still fails strict validation` |
| Accepting a shorter opening prefix loses the removed decision instead of creating a focused continuation card | `test_accepted_shorter_opening_prefix_creates_a_one_player_decision_continuation_card`; `test_black_and_white_prefix_splits_preserve_the_opponent_setup_move`; `test_prefix_split_retry_creates_exactly_one_parent_child_and_queue_entry`; `test_prefix_split_preserves_the_complete_repertoire_line_and_original_reviews` |
| Shortening a prefix opens a slow preview, rejection reappears after refresh, or acceptance stalls before the next card | `backend/tests/test_prefix_split.py::test_rejected_prefix_split_stays_hidden_until_another_failed_review`; `test_prefix_split_accept_enqueues_rebuild_atomically_without_preview`; `test_prefix_split_rolls_back_cards_when_rebuild_cannot_be_persisted`; `test_prefix_split_stale_revision_keeps_original_card_and_queue`; `tests/unit/prefix-split-training-regressions.test.tsx::prefix split offer accepts immediately without opening a preview editor`; `rejected prefix offer stays hidden until a later failure is loaded`; `failed prefix split save keeps the offer and shows a retryable error`; `tests/browser/prefix-split-training.spec.ts::accepting a prefix split skips preview and advances to the next queued card`; `rejecting a prefix split remains dismissed after queue reload` |
| Opening routes need a compact initial drill without truncating or duplicating their later progression | `test_configured_black_route_materializes_as_one_cumulative_prefix`; `test_identical_complete_prefixes_share_one_global_schedule`; `test_early_divergent_route_prefixes_may_repeat_shared_moves`; `test_post_prefix_cards_test_exactly_one_learner_decision`; `test_full_line_descendants_are_materialized_beyond_prefix_depth`; `test_descendant_requires_a_mature_parent`; `test_any_mature_incoming_path_unlocks_a_transposed_descendant`; `deduplicates identical complete prefixes globally`; `keeps route-specific full prefixes when branches diverge before the configured depth`; `materializes every move after the initial prefix as a one-decision descendant` |
| Reimporting with a shorter initial-prefix setting leaves the old long prefix in place or drops later decisions | `test_reimport_can_shorten_initial_prefix_without_truncating_descendants` |
| Existing ready publications retain the old decision-only graph after the hybrid schema migration | `test_hybrid_graph_migration_enqueues_each_existing_repertoire_once` |
| Changing the import default silently reshapes legacy repertoire prefixes with no saved per-line depth | `test_hybrid_migration_freezes_legacy_line_prefix_depth_once` |
| Opening graph rebuilds can overwrite newer input, hold SQLite during traversal, lose manual prefix choices, or damage queue/history state | `test_repeated_graph_rebuilds_are_generation_guarded_and_idempotent`; `test_graph_rebuild_holds_no_sqlite_connection_during_chess_traversal`; `test_exact_archived_prefix_restores_its_original_reviews_and_schedule`; `test_synthesized_prefix_evidence_caps_stability_and_copies_no_reviews`; `test_failed_seed_verification_does_not_revoke_introduced_descendants`; `test_graph_publication_never_rewrites_completed_queue_attempts`; `test_cumulative_graph_prefix_offers_splitting_but_decisions_do_not`; `test_manual_prefix_split_survives_graph_rebuild`; `test_branch_removal_unlinks_or_archives_only_unreferenced_decision_cards` |
| A conflict crossed inside a cumulative opening prefix is omitted from card-level integrity blocking | `test_cumulative_prefix_integrity_block_covers_any_crossed_decision`; `test_integrity_blocks_only_affected_decision_segments` |
| Graph publication can leave an integrity slice indexing the obsolete cumulative-card source list | `test_graph_publication_restarts_integrity_scan_with_current_sources` |
| Production-sized opening graph traversal can monopolize the API process | `test_production_scale_opening_graph_calculation_is_bounded`; `test_graph_rebuild_holds_no_sqlite_connection_during_chess_traversal` |
| Daily queue frontier reconciliation scans every graph step for every card and holds the writer near the foreground latency limit | `test_daily_queue_graph_unlock_has_a_card_first_lookup_index` |
| Accepting a shorter opening prefix carries the old overload remediation state into the new parent | `test_prefix_split_resets_remediation_state_on_shortened_parent` |
| Prefix-split timestamps or gameplay-priority queue states quarantine valid study cards | `test_accepted_shorter_opening_prefix_creates_a_one_player_decision_continuation_card`; `test_legacy_timestamp_introduced_at_is_repaired_to_a_study_date`; `gameplay-prioritized queue cards remain valid typed study cards` |
| Repertoire completeness can hide locally common opponent replies or double-count transpositions | `test_coverage_threshold_uses_conditional_node_probability_rather_than_root_path_probability`; `test_coverage_requires_the_union_of_minimum_probability_and_cumulative_mass_replies`; `test_coverage_uses_explorer_sample_weight_and_maia_smoothing`; `test_unknown_or_stale_coverage_data_never_reports_a_repertoire_complete`; `test_transposed_repertoire_positions_are_evaluated_once_and_credited_correctly`; `test_opponent_reply_without_a_trained_response_remains_a_coverage_gap` |
| Coverage ignores the player's recent rating and speed cohort | `test_coverage_uses_nearest_rating_and_personal_speed_cohort` |
| One provider failure blocks the other account | `test_one_provider_failure_does_not_discard_other_provider_success` |
| Incremental sync misses late games or creates duplicates | `test_incremental_overlap_catches_late_games_without_duplicates` |
| Sync silently hides filtered, rejected, and duplicate records | `test_sync_reports_filtered_rejected_and_duplicate_counts` |
| Game analysis stops on navigation or submits twice after reload | `test_background_game_analysis_resumes_after_reload_and_submits_once` |
| Game analysis discarded MultiPV evidence or accepted an illegal candidate line | `two-pass game scan preserves a bounded MultiPV set with its decision FEN`; `test_game_analysis_persists_bounded_candidate_lines_and_evidence_versions`; `test_game_analysis_rejects_illegal_candidate_pv_before_persistence`; `test_multipv_gameplay_event_records_exploited_tactical_opportunity_and_evidence` |
| Game analysis claim rejects the returned evidence-version field | `game analysis claims accept the evidence version returned by the backend` |
| Motif classification counted harmless pins or lost structured alternate evidence | `test_python_motif_parity_fixture`; `test_detector_contract_preserves_structured_pin_evidence`; `test_primary_motif_precedence_is_stable_and_keeps_secondary_results` |
| Repertoire identity is lost after a deviation or transposition | `test_repertoire_comparison_retains_identity_across_deviation_and_transposition` |
| A repertoire line ending is reported as a player error | `test_line_ending_is_out_of_book_rather_than_a_player_deviation` |
| A recurring tactical motif silently changes the curriculum | `test_recurring_motif_recommends_but_does_not_activate_a_tactics_pack` |
| Tactical suggestions use a game count instead of the requested calendar window | `test_tactics_suggestions_use_thirty_days_and_explicit_opportunity_denominator` |
| Tactical suggestions hide how many eligible opportunities occurred | `test_tactics_suggestions_use_thirty_days_and_explicit_opportunity_denominator` |
| A suggested tactics pack activates before the user chooses it | `TacticalCatalogPanel suggestions > activates a suggested tactics pack only after user action` |
| Completing the selected tactical pack hides the catalog and leaves Tactics apparently frozen | `completed selected pack keeps the catalog available for choosing another pack` |
| Recomputing both-side gameplay events creates duplicates | `test_gameplay_events_reuse_both_sides_analysis_and_recompute_idempotently` |
| Statistics score and rolling windows obscure their denominators | `test_statistics_score_rate_and_engine_metrics_use_exact_denominators` |
| Unanalyzed games contaminate engine-derived statistics | `test_statistics_score_rate_and_engine_metrics_use_exact_denominators` |
| Day-of-week and hour statistics ignore the configured timezone or DST | `test_statistics_local_day_and_hour_respect_timezone_and_dst` |
| Opening and endgame evaluations reverse meaning for Black | `test_opening_and_endgame_evaluations_use_player_perspective` |
| Guided game review repeats findings from the same position or exceeds five prompts | `test_guided_review_ranks_and_deduplicates_top_five_actionable_positions` |
| Guided game review loses its place across navigation or reload | `test_guided_review_resumes_and_correction_does_not_change_fsrs` |
| A guided correction silently changes FSRS scheduling | `test_guided_review_resumes_and_correction_does_not_change_fsrs` |
| Tactical opportunities lose their denominator, queue state, or card provenance | `test_tactical_statistics_has_explicit_zero_safe_conversion_and_pin_breakdown`; `test_tactical_queue_skip_keeps_finding_pending_and_ignore_removes_it`; `test_tactical_card_preview_is_side_effect_free_and_save_admits_one_personal_tactics_card` |
| Tactical themes show misleading percentages or the wrong queue orientation | `tactical statistics render an explicit zero-denominator rate`; `tactical queue uses the player's board orientation` |
| Games summary transports moves, timelines, or findings for every library row | `test_games_summary_pagination_omits_heavy_fields_and_caps_rows` |
| A large Games library renders more than fifty rows at once | `test_games_summary_pagination_omits_heavy_fields_and_caps_rows` |
| Daily chess insights run before sync watermarks and game analysis settle | `test_daily_insights_wait_for_sync_and_analysis_completion` |
| A late-arriving game leaves a stale daily snapshot or insight | `test_late_game_arrival_invalidates_affected_daily_snapshot` |
| Ignoring gameplay evidence changes scheduling | `test_ignored_gameplay_finding_never_changes_card_scheduling` |
| A confirmed gameplay miss double-counts, changes FSRS, or reorders its queue entry on replay | `test_accepted_repertoire_finding_prioritizes_without_fsrs_review`; `test_confirmed_gameplay_miss_replays_without_fsrs_review_or_queue_reordering` |
| A real-game repertoire miss is not linked to its existing card or cannot bring it into training | `test_real_game_miss_maps_to_existing_card_once_and_reaches_introduction_queue`; `test_studied_game_miss_prioritizes_without_changing_fsrs_or_creating_review` |
| Opponent moves, line endings, or missing cards create false player priority | `test_opponent_deviation_and_line_end_create_no_player_miss_event`; `test_missing_card_keeps_finding_without_priority_or_review` |
| Transpositions lose decision identity or later correct gameplay is not measurable | `test_transposed_decision_keeps_canonical_card_identity`; `test_targeted_study_updates_fsrs_and_later_success_is_measurable` |
| Reprocessing or late imports double-count a game miss in FSRS | `test_studied_game_miss_prioritizes_without_changing_fsrs_or_creating_review`; `test_late_imported_miss_is_historical_and_does_not_change_fsrs` |
| A repertoire card awaiting validation receives an automatic FSRS lapse it cannot train | `test_blocked_existing_card_keeps_game_evidence_without_automatic_again` |
| A completed study review leaves a stale real-game miss bonus | `test_study_review_consumes_real_game_miss_bonus` |
| GitHub issue #1: gameplay misses masquerade as controlled FSRS failures, or a finding lacks canonical evidence | `test_studied_game_miss_prioritizes_without_changing_fsrs_or_creating_review`; `test_accepted_repertoire_finding_prioritizes_without_fsrs_review`; `test_targeted_study_updates_fsrs_and_later_success_is_measurable`; `test_same_second_study_timestamps_consume_only_earlier_game_misses`; `test_repertoire_finding_without_canonical_event_requires_reanalysis`; `test_blocked_repertoire_finding_preserves_event_without_queuing`; `Games prioritizes a canonical miss and explains the targeted study card`; `Games shows the reanalysis instruction when a canonical miss is missing` |
| Game-derived priority reason is lost between the queue and training card | `real-game priority reason crosses the strict queue schema into a practice card`; `real-game priority reason appears on the training card` |
| Background game derivation prevents a foreground card review | `test_foreground_review_completes_while_real_game_derivation_computes` |
| Startup backfill reattaches an archived opening card removed by graph publication | `test_restart_does_not_relink_archived_opening_card_removed_by_graph`; Docker durability fixture waits for graph and queue publication before checking restart persistence |
| A reinforcement entry loses its placement and queue metadata on projection rebuild | `test_reinforcement_order_survives_daily_queue_projection_rebuild`; Docker study durability check |
| First big mistake creates duplicate or unpreviewed cards | `test_first_big_mistake_creates_a_previewed_deduplicated_middlegame_card` |
| Board clipped at narrow viewport | `local import respects the daily limit; Black prompts and Builder flip survive Settings and refresh`; `high-DPI board geometry stays aligned through narrow resize and orientation flips` |
| Repair board / keyboard navigation inconsistent | `repair uses the shared board and arrows navigate the complete solution; Escape closes` |
| Random endgames start illegally | `random endgames never leave the nonmoving king in check` |
| Engine not usable in production | `production Stockfish returns playable engine moves without clipping the board` |
| Maia runtime/module loading errors, including production JavaScript MIME type | `Maia initializes matching runtime assets and returns legal playable probabilities` |
| Board controls fall below the viewport | Board-and-controls bounds assertions in every browser workspace regression |
| Unfinished new cards accumulate beyond tomorrow's allowance | `test_unfinished_unreviewed_cards_do_not_bypass_tomorrows_new_card_limit` |
| Explorer/Masters practical results missing from source rows | `source rows show frequency, WDL, practical score and support keyboard preview` |
| Help/reload permits a false clean solve | `test_help_failure_survives_reload_and_cannot_be_graded_as_a_clean_solve` |
| Light admission ignores settings or reverses a lapse | `test_light_discovery_uses_settings_and_never_returns_to_light_after_a_lapse` |
| Rust prefix differs from Python | `prefixes_match_shared_python_golden_fixtures`; `test_prefixes_match_shared_rust_golden_fixtures` |

Append every new reported issue and its test names here. All listed tests belong to the regular suites.

- Analysis activity count growth shifts desktop navigation — `analysis activity count growth keeps desktop navigation anchored`.
- Saving status moves the training board and card layout — `saving result notification does not move the board or card`.
- Long phone review conflicts move the training layout or disappear before they can be copied — `nine phone conflicts can be inspected and discarded individually without changing computer reviews`; `a transient popup fades while its notification remains in the tray`; `severity JSON export includes safe details and excludes secrets`.
- Notifications reorder incorrectly, lose save transitions or repeat conflicts, disappear on reload, or fail when storage is full — `new notifications and updates remain newest first with severity thresholds`; `saving status resolves in place and repeated unresolved conflicts do not duplicate`; `active work stays visible until resolved and history keeps the latest 500`; `history survives module reload and still works when storage writes fail`.
- The notification tray is clipped on a narrow phone — `notifications tray remains inside a 320px phone viewport`.
- Old notifications keep the badge count elevated and obscure new arrivals — `clearing one notification removes only its badge contribution and retains exported history`; `clear all acknowledges every retained notification regardless of severity filter`; `unchanged cleared incidents stay cleared while changed and recurring incidents count again`; `cleared notifications survive reload and remain usable when storage writes fail` in `tests/unit/notification-regressions.test.tsx`; `notification clear controls preserve history across reload and count new arrivals at 320`; `notification clear controls preserve history across reload and count new arrivals at 1280` in `tests/browser/activity-tray.spec.ts`.
- Games displays an unrelated tactical position or mismatched arrows while reviewing a game — `Games selection and move navigation keep the board on the selected game`; `selecting another game changes piece placement and move highlights together`.
- Games displays a placeholder position before full game moves load — `Games shows loading until the selected game's full moves arrive`.
- Game sync records a SQLite lock after provider success — `test_sync_finalization_retries_transient_database_lock_without_refetching_providers`.

- Sync status exposes SQLite-only fields to strict clients — `test_sync_status_never_exposes_persistence_only_result_json`.
- One provider inherits another provider's error — `test_provider_status_never_inherits_another_provider_error`.
- Game sync blocks foreground Settings reads — `test_slow_game_sync_does_not_delay_settings_read`.
- A correct card shows feedback but cannot advance during sync — `test_correct_card_review_advances_while_game_sync_is_active`.
- Background work is opaque or its controls discard work — `test_activity_projection_lists_every_background_source_and_pages_without_losing_counts`; `test_activity_projection_pages_one_thousand_queued_jobs_without_a_sweep_write`; `test_activity_controls_survive_restart_and_prioritize_only_their_queue`; `test_activity_progress_rejects_stale_generation_and_lease`; `test_pausing_coverage_invalidates_browser_lease_and_resume_reopens_queue`; `test_derivation_pauses_after_a_phase_and_resumes_without_replaying_completed_work`; `test_activity_progress_keeps_foreground_settings_read_responsive`; `shows truthful progress and lets the user pause and prioritize eligible work`; `keeps a visible actionable error when the activity service fails`; `keeps the database writer failure actionable in the new tray`; `retains retry for a failed durable task`; `pages a large queue without losing the full activity count`; `study worker activity reports queued running and completion in order`; `activity tray stays reachable and controls queued work 320`; `activity tray stays reachable and controls queued work 390`; `activity tray stays reachable and controls queued work 1280`.
- Daily queues group reviews, new cards, or content types — `test_daily_queue_is_stable_within_a_day_and_mixed`; `test_daily_queue_uses_a_different_seed_for_the_next_day`.
- A repertoire trains contradictory player moves — `test_same_repertoire_trained_player_move_conflict_is_detected`; `test_opponent_branches_and_cross_repertoire_moves_are_not_conflicts`; `test_new_conflicting_branch_enters_integrity_repair`.
- Repertoire transpositions or incomplete player-turn endpoints can create ambiguous or missing answers — `test_repertoire_integrity_sweep_pauses_conflicting_transpositions_after_import`; `test_repertoire_integrity_requires_one_response_at_player_turn_endpoints`; `test_integrity_resolution_rewrites_routes_and_truncates_losing_continuations`; `test_integrity_repair_archives_losing_card_history_without_transferring_mastery`; `test_legacy_integrity_issues_are_swept_at_startup_and_excluded_from_training`; `test_missing_endpoint_resolution_accepts_an_unsaved_legal_move`; `test_invalid_sources_materialize_as_distinct_blocking_issues`; `test_shared_cards_remain_trainable_only_through_clean_repertoires`; `test_stale_or_illegal_integrity_resolution_is_atomic`; `repertoire repair modal renders canonical position and all evidence states`; `deferred repertoire repair stays paused and resumes from the repertoire card`; `successful final repair refreshes and restores the training queue`.
- Review position handoff cannot find matching games — `test_review_position_filters_games_and_summarizes_played_moves`; `review position opens consistently in analysis builder and games`.
- Encountered repertoire gaps remain weeks away or create lines silently — `test_game_gap_with_nearby_mistake_prioritizes_existing_unseen_card_for_tomorrow`; `test_missing_game_gap_line_requires_preview_before_card_creation`.
- Cold routes wait for every service asset before rendering — `cached route data renders before background refresh and reconciles afterward`.
- Background Stockfish blocks interactive work or becomes a failed job when paused — `background game analysis yields to interactive engine work and resumes once`; `test_paused_background_analysis_releases_its_lease_without_failure`.
- Builder Explorer requests omit required Lichess authentication or collapse source failures — `authenticated Explorer requests send the bearer token to both sources and cache their independent results`; `missing Explorer authentication reports sign-in required without making anonymous requests`; `an authentication rejection is distinct and never exposes the bearer token`; `rate limits are retryable and preserve cached source data as stale`; `Masters outage does not discard ready Lichess results`; `Lichess outage does not discard ready Masters results`; `network failures preserve cached moves and report offline state`; `HTTP 200 with an incompatible payload is invalid and cannot poison the cache`; `legacy paired cache migrates without adding authentication to its key`; `legacy persistent Lichess token migrates into the session and is removed from local storage`; `Builder requires Lichess sign-in before Explorer requests and offers its connection action`; `Builder OAuth completion stores the session token and retries the current Explorer position`; `Builder does not retry Explorer requests with a rejected token until reconnection`; `test_explorer_session_handoff_keeps_authorization_in_memory_only`; `test_coverage_claim_waits_for_in_memory_lichess_session_and_keeps_job_queued`; `test_coverage_explorer_forwards_bearer_token_without_persisting_it`; `test_coverage_requeues_after_rejected_session_and_does_not_repeat_bad_token`.
- High-frequency opening trunks crowd out the later lines that matter — `test_completed_zero_personal_and_explorer_support_with_low_maia_deprioritizes_the_line`; `test_unavailable_sources_are_unknown_instead_of_zero`; `test_strict_prefix_repertoire_records_do_not_become_duplicate_completed_lines`; `test_london_trunk_moves_do_not_add_repeated_near_full_priority`; `test_common_multi_move_line_can_outrank_a_rare_single_move_line`; `test_single_and_multi_move_cards_use_normalized_frontier_scores`.

| Refactor regression | Required regression |
| --- | --- |
| Application aliases fail in Vitest | `application aliases resolve in the regular frontend suite` |
| Invalid tokens carry validated move brands | `null and malformed moves remain raw diagnostics while valid moves are canonical` |
| Provider moves bypass domain validation | `engine candidates validate legal UCI and PV while preserving evaluations` |
| Queue adapters lose persisted attempt identity | `queue mapping preserves identity and guided state and rejects illegal persisted lines` |
| Training selectors loop or Black cannot respond after automatic White move | `Black training mounts without selector loops and plays from the current position` |
| Training board resets to starting FEN during completion | `unseen tactic review has no automatic teaching arrow and retains the final mate before reinforcement` |
| Self-reported first clean solve reveals later reinforcement plies | `self-reported first clean solves receive reinforcement without teaching later plies` |
| Show move label and guidance toggle conflict with automatic teaching | `Show move during initial teaching records failure and keeps required guidance` |
| A previous failed position's comment leaks into later positions | `position notes and annotations reveal only at the failed position and remain hidden in clean training` |

| Local study readiness | Required regression |
| --- | --- |
| Docker serves packaged tactics as forbidden / empty stage | `real packaged tactics and standard chess sounds are readable and preloaded before opening Tactics`; `real hanging-piece packs contain 100 validated playable cards after their setup move`; `failed asset requests report HTTP errors and remain retryable without sample fallback` |
| Diagnostics block interaction or show stale results | `background diagnostics yield before computation and discard stale generations` (worker-backed in browser workflows) |
| Tabs fetch/validate only after the first click | `tab preloading prepares the first unfinished tactic stage and shares the validated deck request`; `preloaded local records are invalidated after mutations rather than hiding new study data` |
| Builder source details cannot be compared | `Builder comparison keeps covered moves and displays all source details together`; `Builder source comparison is immediately reachable beside the board` |
| Unconnected databases silently appear blank in comparison | `Builder comparison exposes database connection and source status rather than unexplained blanks` |
| Move sounds resemble birds rather than chess pieces | `moves and captures play distinct standard chess recordings and respect persisted sound settings`; production asset availability browser test |
| Board sounds are silent after restart, then old cues arrive together | `startup prepares all board sounds before the first move and drops cues while loading`; `delayed board sound requests expire instead of bursting when playback becomes available`; `repeated pending cues and muting cancel earlier playback requests`; `delayed startup audio never replays earlier board moves and the next move sounds normally` |
| Later reviews reveal teaching arrows | `tomorrow and later opening reviews remain unassisted even when teaching storage is empty`; `test_study_reinforces_today_reviews_tomorrow_and_persists_unassisted_later_reviews` |
| A game-miss priority card with an earlier Again review reveals arrows, and arrow-following can count as recall | `test_priority_game_miss_queue_reports_prior_study_without_a_clean_pass`; `previously studied game miss without a clean pass tests recall without arrows`; `previously studied game-miss priority card starts without a teaching arrow`; `unseen game-miss priority introduction teaches once and returns for unassisted recall`; `test_assisted_opening_teaching_returns_for_a_separate_clean_recall` |
| Training cards do not show how many completed study reviews preceded this display | `test_queue_reports_only_valid_saved_study_review_count`; `real-game priority reason crosses the strict queue schema into a practice card`; `labels a card's prior study review count`; `previously studied game-miss priority card starts without a teaching arrow`; `unseen game-miss priority introduction teaches once and returns for unassisted recall` |
| Saved study work lost on Docker recreation | `verifyStudySurvivesContainerRecreation` in the mandatory Docker suite checks all SQLite store checksums, queue entries/order, FSRS, teaching, notes and guided state |
| Tactical admissions/reviews do not return to the queue | `test_tactical_failures_requeue_once_and_clean_reviews_survive_restart_and_return_when_due`; `test_again_reappears_after_four_other_entries` |
| Tomorrow shifts an extra day after UTC midnight | `test_tomorrow_is_the_local_review_day_even_after_utc_midnight` |
| Board and pieces use different bounds after resize | `frame padding remains outside equal square board dimensions at desktop, narrow, and high-DPI sizes`; exact surface/square/piece assertions in every browser `boardVisible` check |
| Already studied unassisted cards freeze | `readable renamed store fields retain local authority, failure, and sound through Home selectors`; `previously studied unassisted card is playable after atomic queue hydration and refresh`; `same-entry tactical refresh clears stale feedback pause so the board stays playable` |
| Background refresh resets an ongoing attempt | `same-entry queue refresh preserves position and an active reply transition` |
| Timers from previous cards lock replacements | `stale opponent replies and completion timers cannot lock or complete a replacement queue entry` |
| Malformed external records freeze or discard the queue | `malformed FEN, null UCI and illegal queue lines are quarantined without discarding valid study cards`; `controlled queue payload structural drift produces a named diagnostic instead of unsafe domain values` |
| Provider drift leaks unsafe move data | `third-party analysis accepts new provider fields but rejects invalid counts, probabilities and illegal moves` |
| Bad saved state silently disappears | `malformed stored Builder state is retained for repair while a valid default remains available` |
| Incomplete backups enter persistence | `backup snapshots require all version, timestamp, checksum, table, and count fields` |
| Late queue response overwrites a replacement attempt | `late queue responses cannot replace a newer playable queue entry` |
| Maia inference occupies the board input thread | `Maia inference starts in a dedicated worker and validates candidates without occupying board input`; real Maia workflow runs in browser and Docker suites |
| Worker payload drift causes unsafe state | `controlled worker messages reject malformed FEN, null UCI and unexpected structural fields` |
| Browser schemas differ from Python transport contracts | `Pydantic settings response and strict Zod adapter accept the same transport fixture` |
| Tactics are admitted for tomorrow after UTC midnight | `test_tactic_discovery_uses_local_day_after_utc_midnight`; `test_default_scheduler_uses_local_day_not_utc_day` |
| Board shifts or clips after high-DPI resize/flip | `high-DPI board geometry stays aligned through narrow resize and orientation flips` |
| Legacy tactic counters select an unrelated puzzle | `test_tactic_progress_returns_distinct_discovered_ids_including_legacy_clean_records`; `legacy clean puzzle identities select the next unattempted deck position rather than the attempt counter` |
| Show Move/Restart abandon a failed tactic or retain stale X | `tactic Show Move and Restart retain one guided attempt then clear X and unlock the next puzzle` |
| Comparison sorting or missing values are incorrect | `comparison headers sort ascending then descending with missing values last and preserve playable hover rows`; `Builder source comparison is immediately reachable beside the board` |
| Explorer/Masters details overwhelm comparison | `compact database cells reveal WDL frequency and practical score only on demand` |
| Exact transpositions do not offer the played route | `exact transposition banner adds the played route as a persisted branch without waiting for Maia`; `transposition dismissal survives Builder remount and existing routes never prompt`; `Builder exact transposition confirms a conflicting trained move then saves the route` |
| Navigation lacks active-page semantics or visible headers waste space | `Builder source comparison is immediately reachable beside the board`; `high-DPI board geometry stays aligned through narrow resize and orientation flips` |
| Red X stays over the board during guided completion | `red board feedback disappears after one second even while the failed attempt remains active` |
| Incomplete Black opening cards auto-play the only move then freeze Train | `test_incomplete_black_prefix_is_not_created_or_queued`; `test_legacy_incomplete_black_prefix_is_quarantined_from_queue` |
| Builder cannot remove one response line from current position | `test_delete_branch_prefix_removes_nimzo_descendants_and_preserves_qgd`; `test_delete_branch_rebuilds_missing_retained_cards_without_server_error`; `builder delete line removes Nimzo branch descendants and keeps QGD response`; `opening card editor can jump to Builder with line-removal context`; `Edit card opens Builder line-removal context and deletes the selected branch` |
| Clicked board square maps to a different square | `forwards selected squares and drawn square markers with exact square identity`; `builder annotation save preserves exact clicked square identity`; `Builder right-click annotation saves the exact clicked square` |
| One-column Train layout collapses the board after a resize | `Black Train prompt remains playable with a fully visible narrow board` |
| Games shared-board shell leaks ownership or remounts embedded board during migration | `Games shared board publishes readonly state and releases ownership on unmount` |
| Builder shared-board shell leaks ownership or remounts embedded board during migration | `Builder shared board publishes shell ownership and hides local board instance` |
| Tactics shared-board shell leaks ownership or remounts embedded board during migration | `Tactics shared board publishes shell ownership and hides local board instance` |
| Endgames shared-board shell leaks ownership or remounts embedded board during migration | `Endgames shared board publishes shell ownership and hides local board instance` |
| Training shared-board shell leaks ownership or remounts embedded board during migration | `Training shared board publishes shell ownership and hides local board instance` |
| Persistent shell remounts Chessground while board ownership changes across workspaces | `persistent board shell reuses one Chessground instance across board owner switches` |
| Burying a training card reshuffles it today instead of hiding it until tomorrow | `bury removes every occurrence of the active card and allows burying the last card`; `test_bury_excludes_card_until_next_day_without_review_or_schedule_change`; `test_bury_removes_all_queued_cycles_and_can_finish_the_daily_queue`; `test_postgres_bury_handler_excludes_all_cycles_without_reordering_and_rejects_stale_entry`; `training Bury hides the card for today across reload and reports a failed bury`; `defensive training can bury before loading or grading the exercise` |
| Defensive card grades a future rook square while showing the earlier board (reported 36.Rc4? Ne3+) | `test_reported_rc4_fork_requires_preview_with_rook_on_c4`; `test_defensive_recognition_holds_forks_beyond_the_immediate_reply`; `test_reported_rc4_preview_grades_c4_only_after_proposed_move`; `test_reported_rc4_audit_preserves_attempts_and_removes_invalid_review_effect`; `test_defensive_preview_queue_blocks_unteachable_card_without_erasing_history`; `test_defensive_rubric_audit_yields_to_foreground_and_replays_after_restart`; `test_defensive_auto_admission_holds_legacy_fork_without_immediate_preview`; `reported Rc4 recognition uses the after-move rook square with keyboard and touch`; `reported Rc4 defensive preview stays readable and returns to the decision at 390px` and `1440px` |
| Ready continuation preview rejects the API's engine report provenance | `continuation preview accepts report provenance and rejects unknown candidate fields`; `missing-response viewer shows the learner board after the reply and selects one move`; `discovery viewer compares one learner decision and returns from Builder 390` and `1470` |
| Shared shell layout drifts between desktop and mobile across board workspaces | `shared board shell keeps board region fixed left on desktop and top on mobile across board workspaces` |

| UI consistency and reliability | Required regression |
| --- | --- |
| Deleted/missing SQLite mount reports healthy | `test_unavailable_database_is_actionable_and_never_healthy` |
| Builder annotations leak into Games | `builder annotations never appear in games`; `stale sessions cannot overwrite or release a replacement owner` |
| Board moves and changes size between workspaces | `board bounds remain identical across workspace navigation` (viewport matrix) |
| Endgame actions inaccessible on phones | `endgame study actions remain reachable on narrow screens` |
| Nested workspaces clip desktop content | `desktop workspace panels do not clip controls or content` |
| Board toolbar placement varies between modes | `board controls retain consistent placement across workspaces` |
| Application navigation recreates the board | `application navigation retains one Chessground instance` |
| Service failures masquerade as empty data | `failed initial loads never display empty records or zero statistics` |
| Split resizing loses preference or changes other workspaces | `shared split supports keyboard reset and persistence without changing workspace preference` |
| Responsive navigation and task tabs break across browser engines | `critical navigation, board input, split and dialogs work across browser engines` |
| Inaccessible controls and page overflow | `workspace accessibility and reflow` (phone and desktop) |
| Appearance drifts between sections and viewports | Mandatory Linux `visual.spec.ts` baselines for all eight sections, unavailable service and phone import dialog |
| Test cleanup could run against a user's database | `destructive browser fixtures refuse production and unmarked services` |
| State leaks across arbitrary board mode transitions | `all board owner pairs clear transient fields on acquisition`; `every directed workspace transition isolates annotations and board input` |
| Pointer resizing loses its preferred allocation | `pointer resizing persists and clamps without horizontal overflow` |
| Warm navigation/resize stalls | `warm workspace shells paint within 200ms p95 without long interaction tasks` in the mandatory pinned Linux suite |

- `failed settings reads cannot overwrite authoritative settings with defaults` — `tests/browser/recovery.spec.ts`: failed initial settings load disables writes and offers retry.
- `malformed progress is unavailable and retry recovers real measurements` — malformed data cannot become empty or zero statistics.
- `touch board input and rotation preserve legal position at DPR` — legal/illegal touch moves, read-only Games and orientation changes at DPR 1/2.
- `drag input and promotion retain the established queen-promotion behavior` — real Chessground drag and promotion.
- Existing backend regressions `test_delete_branch_prefix_removes_nimzo_descendants_and_preserves_qgd` and `test_delete_branch_rebuilds_missing_retained_cards_without_server_error` exposed a missing route; `test_branch_removal_preserves_shared_cards_and_other_repertoire_history` additionally protects shared card ownership and reviews.
- Existing `test_new_cards_per_day_applies_separately_to_each_repertoire`, `test_new_cards_per_day_respects_lower_limit`, and `test_new_cards_per_day_handles_uneven_repertoire_sizes` protect the restored per-repertoire admission limit.
- `tablet section menu stays inside the header and never overlaps the board` — removes inherited two-row navigation positioning at tablet breakpoints, discovered during baseline review.
- `workspace timeout is actionable and a retry can recover` — bounded service reads abort after 15 seconds, evict the failed request, and allow a fresh retry.
- `test_repertoire_lines_read_stays_available_during_write_compatibility_contention` — a repertoire-line read stays available during a competing write-compatible section. This confirms the GET query-only path; the reported timeout still needs live-service timing evidence.
- `training startup does not preload unrelated workspace requests` — the active training screen fetches its required data without launching other workspace reads at startup, reducing foreground contention during repertoire-line loading.
- `unavailable repertoire lines do not falsely grade another legal move` — failed line loading is actionable and blocks grading an unverified alternate move until a retry succeeds.
- `application navigation retains one Chessground instance` additionally traverses Settings, Progress, and Repertoire; the hidden board remains mounted while its workspace relinquishes ownership.
- `shared toolbar flip persists while stepping through a game` — toolbar flip and keyboard flip share the rendered board's orientation behavior, so move navigation cannot overwrite it.
- `owner changes clear an unfinished square selection at the same position` — same-FEN ownership transitions still cancel transient board selection.
- `tablet menu Escape restores the visible navigation trigger` — keyboard dismissal returns focus to the tablet trigger rather than the hidden phone control; covered in Chromium, Firefox, and WebKit.
- Mandatory `board-unavailable` and `training-feedback-phone` screenshots cover an initial board load failure and visible practice feedback, alongside the all-section viewport gallery.
- `import waits for saved settings before writing a repertoire` — import cannot race the initial settings response and submit a temporary default depth. Loading errors are actionable and retryable.

- Phone toolbar controls must meet the 44px touch-target contract even when a narrow viewport has a fine pointer: `phone board controls provide 44px touch targets with every pointer type` (`tests/browser/layout.spec.ts`). This reproduced the 36px board-toolbar specificity override before the fix.

- The regular browser, visual, and performance suites serve a production build; development-server reloads must not reset navigation during recovery or real Maia initialization. Covered by the existing Maia, Settings recovery, and single-Chessground navigation regressions.
- Annotation gestures wait for Chessground's drawing animation frame before releasing the pointer; the existing `Builder right-click annotation saves the exact clicked square` regression verifies the persisted square, while `builder annotations never appear in games` verifies isolation.
- Endgames visual references assert a fixed FEN before capture so asynchronous random-number consumption cannot change the photographed position.

## Defensive tactical threats (GitHub issues 9–13)

- Discovery engine reports cannot authorize defensive training with a wrong restricted root, insufficient depth, or illegal PV: `backend/tests/test_defensive_threat_persistence.py::test_discoveries_restricted_engine_report_rejects_wrong_root_and_short_depth`.
- Saved defensive reports are audited in one foreground-preemptible slice and archived only once on replay: `backend/tests/test_defensive_threat_persistence.py::test_discoveries_report_repair_yields_to_foreground_and_replays_once`.
- Verified defensive threats automatically enter the daily queue once within the separate introduction limit: `backend/tests/test_defensive_threat_persistence.py::test_discoveries_verified_defense_auto_admits_once_with_daily_cap`.
- Studied opening decisions are promoted only by recurring engine-confirmed loss, with sound deviations and incomplete horizons excluded from the loss average: `backend/tests/test_repertoire_opportunities.py::test_discovery_studied_engine_mistakes_exclude_sound_deviations_and_incomplete_horizons`.
- Explicit discovery admission bypasses locked ancestors and the automatic daily cap while duplicate clicks remain idempotent: `backend/tests/test_repertoire_opportunities.py::test_discovery_explicit_locked_decision_survives_daily_cap_and_duplicate_clicks`.
- A recognition error requires reinforcement even after a sound defensive move, and replay records one review: `backend/tests/test_defensive_threat_persistence.py::test_discovery_recognition_error_reinforces_even_with_sound_defense_once`.
- A supported knight route and sound move complete the staged recognition exercise: `backend/tests/test_defensive_threat_persistence.py::test_discovery_recognition_correct_route_and_sound_defense_pass`.
- The Discoveries badge waits for an exercise or editing safe break, does not open over an editor, and acknowledgement prevents a second automatic interruption: `tests/unit/discoveries-tray-regressions.test.tsx::discoveries badge waits for a safe break and does not interrupt twice`.
- Black-side evaluation changes keep the learner sign, while mate outcomes stay typed and separate from centipawn averages: `backend/tests/test_repertoire_opportunities.py::test_discovery_black_evaluation_sign_and_mate_are_typed_separately`.
- A discovery inside a prefix card splits the final learner decision and queues that continuation without inventing reviews: `backend/tests/test_repertoire_opportunities.py::test_discovery_prefix_split_isolates_target_without_transferring_reviews`.
- A paused verified defense can be explicitly queued beyond the automatic daily cap, with its admission source retained: `backend/tests/test_defensive_threat_persistence.py::test_discovery_paused_defense_can_train_now_beyond_automatic_daily_cap`.
- An accepted engine continuation persists its intent through branch save, waits for graph and integrity publication, then queues the new card without a synthetic review: `backend/tests/test_repertoire_opportunities.py::test_discovery_accepted_engine_branch_survives_publication_restart`.
- Defensive engine requests appear in Analysis activity and pause prevents another worker claim: `backend/tests/test_defensive_threat_persistence.py::test_discovery_engine_activity_states_and_pause_preempts_claim`.

- Issue 9 legal knight-fork geometry, king and major targets, actual-piece route, and played versus engine provenance: `backend/tests/test_defensive_threat_detection.py` (`test_issue9_*`).
- Issue 10 real learner-turn anchors, bounded to three previous decisions and excluding hypothetical future turns: `backend/tests/test_defensive_threat_detection.py` (`test_issue10_*`).
- Issue 11 compatible full-history requests, typed scores, legal refutations, capturable knights, net material exchange, mate and centipawn handling, and report lease identity: `backend/tests/test_defensive_threat_validation.py` (`test_issue11_*`); `backend/tests/test_defensive_threat_persistence.py::test_issue11_analysis_report_requires_matching_lease_and_request`.
- Issue 12 stable candidate identity, dismissal and resurface on materially changed played evidence, no offensive or FSRS side effects, analysis supersession, foreground concurrency, and restart replay: `backend/tests/test_defensive_threat_persistence.py` (`test_issue12_*`).
- Issue 13 manual approval, active queue admission, minimal prompt, flexible legal-move grading, ambiguous or illegal no-review outcomes, one definitive review, and idempotent retry: `backend/tests/test_defensive_threat_persistence.py::test_issue13_approved_rubric_grades_unlisted_move_and_schedules_once`; `backend/tests/test_defensive_threat_grading.py` (`test_issue13_*`).

## Tactical pack catalog expansion
- `existing tactical puzzles survive splitting into 25-card packs` preserves every original Lichess puzzle and source field (`backend/tests/test_tactical_catalog.py`).
- `expanded tactical catalog contains every requested theme and complete pack` verifies 47 themes, 692 packs, 17,300 unique legal puzzles, and source metadata (`backend/tests/test_tactical_catalog.py`).
- `tactical pack migration preserves reviews scheduling and completion` verifies additive backup/migration and legacy identity retention (`backend/tests/test_tactical_catalog.py`).
- `active tactical packs share one daily introduction quota` verifies five fair, idempotent reservations without fabricated solves (`backend/tests/test_tactical_catalog.py`).
- `deactivating a tactical pack preserves scheduled reviews` verifies queued cards remain after activation changes (`backend/tests/test_tactical_catalog.py`).
- `practice in an inactive pack still admits the puzzle to reviews` remains covered by tactic attempt admission and workspace flow regressions.
- `tactical completion counts distinct clean solves across practice and training` is covered by distinct progress identity and review regressions.
- `all tactical difficulties remain directly accessible` is covered by the pack catalog unit and browser workflows.
- `tactical catalog groups and activation controls remain reachable on phones` verifies grouped controls, 44px targets, and the visible board (`tests/browser/layout.spec.ts`).

## UI focus and consistency refactor
- `primary navigation exposes one Insights destination` verifies Progress and Statistics are represented by one visible Insights destination.
- `legacy progress and statistics destinations open the matching Insights tab` preserves compatibility for old internal view selections.
- `context tabs preserve workspace state across resize and navigation` verifies selected records, board state, and tab state survive workspace changes.
- `tactics solving remains focused while every pack stays reachable` verifies Solve and Packs contexts preserve the active attempt.
- `inactive tactical catalog content does not block board interaction` verifies collapsed and filtered catalog content remains lazy and non-blocking.
- `games review findings and library retain independent state` verifies each Games context preserves its selected game and findings queue.
- `builder tabs preserve engines repertoire history and annotations` verifies Builder state survives Compare, Repertoire, Moves, and Notes changes.
- `settings sections and save feedback remain reachable on phones` verifies section navigation, unsaved state, save feedback, and keyboard reachability.
- `repertoire cards keep secondary and destructive actions reachable` verifies overflow actions expose rename, export, and delete without obscuring Browse.
- `shared workspace controls retain one consistent hierarchy` verifies headings, tabs, notices, and primary actions across all workspaces.

## Availability and background-work recovery

- `test_background_game_sync_routes_use_background_database_sections` protects automatic settings, sync enqueue, and sync-status requests from foreground/background contract violations.
- `test_sync_enqueue_does_not_rewrite_settings_or_schedule_coverage` keeps sync enqueue free of account-setting and coverage side effects.
- `test_only_coverage_setting_changes_enqueue_one_refresh_per_repertoire` coalesces coverage refresh scheduling to actual coverage-setting changes.
- `test_production_scale_priority_computation_holds_no_sqlite_connection` exercises 1,600 lines, 850 cards, and 120,000 occurrences with a bounded pure-compute phase.
- `test_foreground_review_and_workspace_reads_succeed_during_priority_computation` verifies settings, progress, and review writes remain available while priority scoring runs.
- `test_priority_refresh_coalesces_repeated_game_and_coverage_triggers` protects one durable generation-coalesced job per repertoire.
- `test_interrupted_priority_refresh_replays_once_after_restart` verifies startup recovery requeues an interrupted generation and publishes one replacement.
- `test_stale_priority_generation_cannot_overwrite_newer_inputs` prevents stale calculations from committing after a newer trigger.
- `test_optimized_priority_scoring_matches_existing_parity_fixtures` protects scoring parity across the indexed implementation.
- The priority-generation table grew to 87.78 GB by retaining 910 generations and repeating shared evidence per card — `test_priority_evidence_omits_repeated_edge_states_and_preserves_status`; `test_priority_publication_atomically_enqueues_retention`; `test_priority_retention_keeps_only_published_and_active_generation`; `test_priority_retention_restarts_and_foreground_reads_continue`; `test_storage_reclaim_preserves_authoritative_fingerprints_and_compacts`.
- An older SQLite runtime reported a NULL `settings.coverage_path_floor` after compaction of a freshly initialized or upgraded file — `test_storage_reclaim_preserves_authoritative_fingerprints_and_compacts`; `test_settings_schema_upgrade_materializes_path_floor_before_compaction`.
- Browser cache growth remains bounded without losing static offline recovery — `workspace persistence rejects oversized API responses and bounds total bytes`; `Pages service worker caches scoped static assets and bypasses API and external GETs`.
- Newly encountered replies lose to generic forecasts, or likely unseen replies are ignored — `test_recently_encountered_reply_can_outrank_public_forecast_for_tomorrow`; `test_unseen_likely_reply_outranks_unseen_rare_reply_for_tomorrow`.
- An in-progress coverage refresh replaces reliable evidence with partial values — `test_priority_refresh_uses_last_completed_coverage_until_replacement_is_complete`.
- Tomorrow's admission waits on pending scoring, ignores the last publication, or displaces due reviews — `test_next_day_admits_from_last_published_scores_without_delaying_due_review`.
- Stockfish timeout erases opponent-move evidence or implies successful gap analysis — `test_stockfish_timeout_preserves_personal_priority_evidence_and_remains_retryable`.
- Recurring SQLite contention and false-empty workspace projections — `test_concurrent_queue_progress_repertoire_and_settings_reads_never_return_busy`; `test_workspace_reads_complete_under_one_second_during_full_background_backlog`; `test_get_endpoints_are_query_only`.
- Training queue refresh times out while discovery previews occupy browser requests — `discovery preview backlog leaves a prompt foreground training queue refresh`; `superseded queue request releases its browser connection without replacing the current attempt`; `timed-out queue read retains a completed tactic for an actionable retry`; `retryable queue contention retries before reporting a failure and retains the active attempt`.
- Daily admission races, refresh replacement, and restart drift — `test_daily_queue_admission_is_single_flight_and_idempotent`; `test_last_published_queue_remains_playable_during_refresh`; `test_daily_queue_remains_exact_across_restart_and_midday_admission`.
- Integrity-blocked openings hide or inflate playable tactics — `test_needs_repair_openings_do_not_hide_or_inflate_due_tactics`.
- Background commits outrank interactive review writes — `test_foreground_review_preempts_queued_background_commits`.
- Oversized background publications escape the operational transaction budget — `test_background_commit_budget_is_enforced_at_production_scale`.
- CPU-bound priority scoring starves the API process — `test_cpu_bound_task_does_not_starve_api_requests`.
- Durable work is duplicated, lost on restart, or publishes stale generations — `test_repeated_triggers_coalesce_by_kind_key_and_generation`; `test_durable_task_replays_once_after_process_restart`; `test_stale_task_generation_cannot_publish`.
- Terminal background failures are invisible or cannot be retried — `test_terminal_task_failure_is_visible_and_retryable`.
- `automatic game sync backs off after service failure and resumes after recovery` protects 5/10/20/60-second failure backoff, recovery reset, and idle polling.
- `test_discoveries_backfill_restarts_after_one_game_without_duplicate_scan` protects foreground writes during a paused backfill, resumable one-game slices across restart, and stale replay.
- `defense recognition keeps findings hidden until the staged answer is submitted` protects assessment, explanation, and answer concealment in the defensive UI.
- `test_discovery_accepted_engine_branch_survives_publication_restart` also protects source-game links, learner color, and saving the inspected continuation rather than only its first move.
- `test_discoveries_feed_paginates_beyond_first_hundred_per_repertoire` protects total counts and later pages when a repertoire has more than 100 active findings.
- `discoveries tray loads later pages without losing the first page` protects paginated discovery browsing while the badge refreshes.
- `test_discoveries_complete_sound_refuted_fork_becomes_validated_control`; `test_discoveries_validated_false_alarm_finishes_after_recognition_with_one_review`; `test_discoveries_auto_admission_includes_one_validated_control_under_daily_cap`; and `validated false alarm ends after explanation without requesting a move` protect conservative capturable-knight controls, their admission, and a single scheduled review without a move stage.
- `test_discoveries_reanalysis_keeps_defense_card_and_genuine_review_history` protects stable defensive card identity and real scheduling history while refreshed evidence is temporarily unavailable.
- `test_discoveries_auto_admission_prioritizes_recurring_position_before_recent_isolated_one` protects recurrence priority over a more recently played isolated incident.
- `test_discovery_recommendation_worker_keeps_foreground_free_and_replays_once` protects full-history Docker recommendation requests, foreground access during computation, restart, and idempotent replay.
- `test_discoveries_auto_admission_includes_one_validated_control_under_daily_cap` also protects automatic defensive admission provenance in the queue response.
- `Docker owns defensive engine claims while the browser remains passive` protects removal of the browser defensive-analysis claimant; the Node worker smoke test verifies restricted searches.
- `test_issue11_analysis_report_requires_matching_lease_and_request` also verifies that legacy browser clients cannot claim Docker defensive searches.

## Fractional priority validation and card-level integrity recovery

- Pasted SAN/PGN analysis is misplaced, duplicated, silently truncated, or marks a gap covered without a response — `test_paste_san_variations_matches_repertoire_and_saves_batch`; `test_paste_pgn_fen_variations_and_partial_gap_context`; `test_paste_preview_requires_choice_when_multiple_match_or_none_match`; `test_paste_matches_a_line_starting_at_existing_terminal_position`; `test_paste_duplicate_conflict_and_stale_preview_are_safe`; `test_paste_marks_gap_covered_only_with_opponent_move_and_response`; `test_paste_batch_conflict_requires_confirmation_and_is_atomic`; `test_paste_rejects_incomplete_trained_side_line_without_writing`; `paste SAN from a repertoire gap previews its destination and saves the continuation`; `paste analysis reports a preview service failure without claiming a save`.

- Disabling defensive cards leaves them in today's stack or erases their history — `test_defensive_stack_toggle_hides_today_without_erasing_reviews_or_queue_entries` checks same-day restoration, accurate due counts, and stale queue actions; `defensive daily stack setting persists across navigation and reload` covers the Settings workflow.
- Background analysis stops with the browser or starves foreground work — `test_external_worker_gate_yields_to_foreground_and_replays_bounded_task`; Docker integration waits for analysis publication with no browser open.
- Container Maia probabilities drift from interactive Maia — `container Maia probabilities match browser inference for both colors`.

- Adaptive cohort fields make “Check coverage” reject a valid response — `test_coverage_summary_accepts_adaptive_cohort_settings_without_diagnostic`; `check coverage loads adaptive settings without a validation alert`.
- Fractional weighted personal-game evidence invalidates repertoire records — `test_fractional_personal_game_evidence_is_valid_repertoire_data`; `test_workspace_validation_does_not_drop_fractional_priority_records`; `fractional priority evidence loads both repertoires without a diagnostic`.
- Whole-repertoire repair status quarantines safe cards — `test_integrity_scan_blocks_only_cards_crossing_unresolved_positions`; `test_unaffected_due_reviews_remain_playable_when_repertoire_needs_repair`; `unaffected opening reviews remain mixed with tactics during repertoire repair`.
- Blocked cards are hidden inside playable due totals — `test_blocked_opening_cards_are_reported_but_not_counted_as_playable`.
- Queue refresh rewrites history or duplicates recovered work — `test_queue_refresh_never_resurrects_completed_attempts`; `test_same_day_repair_requeues_newly_unblocked_due_cards_once`.
- An obsolete integrity scan overwrites newer published blocks — `test_integrity_block_publication_is_generation_guarded`.
- Guided repair chooses a move without explicit user input — `test_guided_repair_never_auto_selects_a_move`.
- Repair traversal holds SQLite or migrates mastery to changed content — `test_repair_worker_holds_no_sqlite_connection_during_chess_traversal`; `test_repair_preserves_unchanged_card_reviews_and_archives_changed_history`.
- Per-repertoire statistics keep shared-card inventory and valid first daily study attempts distinct: `test_repertoire_statistics_shared_prefixes_and_first_valid_daily_study_attempt`.
- Per-repertoire game adherence uses primary matches and links a reached position to its actual game ply: `test_repertoire_statistics_primary_game_decisions_and_position_navigation`; `repertoire statistics inspect a reached position and retain context 390`; `repertoire statistics inspect a reached position and retain context 1280`.
- Unlock dates remain conditional and repair pauses suppress them: `test_repertoire_statistics_unlock_forecast_and_paused_parent`.
- Encountered decision positions paginate without repeating rows: `test_repertoire_positions_paginate_without_repeating_decisions`.
- Repertoire changes refresh games in durable slices without blocking foreground reads or duplicating a replayed slice: `test_repertoire_game_refresh_yields_to_foreground_and_replays_once_after_restart`.
- PostgreSQL repertoire refresh admits a versioned position-index task for each affected game: `test_postgres_repertoire_refresh_admits_position_index_with_job_version`.
- PostgreSQL defensive validation publishes its result and admission task only under the current lease, and stale redelivery has no effect: `test_postgres_threat_validation_commits_result_and_admission_with_lease`.
- PostgreSQL defensive backfill advances one analyzed game at a time, ignores stale delivery, and completes after repertoire scans: `test_postgres_threat_backfill_advances_one_game_and_completes_after_repertoires`.
- PostgreSQL defensive card admission queues a daily refresh in the same leased slice and stale redelivery cannot admit twice: `test_postgres_defense_admission_handoffs_queue_only_under_current_lease`.
- PostgreSQL game findings hand off a versioned real-game miss task, and each missed decision advances once before the next phase: `test_postgres_game_findings_publication_handoffs_versioned_misses`, `test_postgres_game_misses_advance_one_event_then_handoff_with_version`.
- PostgreSQL finding slices reuse a durable prepared snapshot; unseen cards are read during preparation and final source verification, not once for every staged item: `test_postgres_game_findings_stage_one_item_and_replay_after_restart`.
- PostgreSQL gameplay events remain generation fenced through staging, publication, feature calculation, and repertoire handoff: `test_postgres_game_events_stage_then_publish_without_stale_replay`, `test_postgres_game_features_publish_then_handoff_with_current_version`, `test_postgres_game_priority_handoff_finishes_only_after_last_repertoire`.
- The SQLite import streams existing gameplay events into the PostgreSQL legacy table behind the versioned read view: `test_postgres_gameplay_event_import_targets_legacy_table`.
- PostgreSQL priority generations publish only after all card rows stage; a replayed stale lease cannot stage another row: `test_postgres_priority_generation_stages_and_publishes_after_replay`.
- GitHub issue #30: PostgreSQL priority refresh must calculate once for a stable 1,000-card generation, resume staging after restart, keep each write batch bounded, and skip preparation for stale leases: `test_priority_1000_records_calculate_once_across_resumable_slices`, `test_priority_stale_lease_skips_expensive_preparation`.
- GitHub issue #30: an incomplete preparation can retry without duplicate rows; shared, repertoire, or scoring changes cannot publish stale priorities; empty generations publish, and count mismatches withhold publication: `test_priority_crash_before_checkpoint_retries_without_duplicate_rows`, `test_priority_source_change_withholds_publication_and_requests_followup`, `test_priority_repertoire_change_withholds_publication`, `test_priority_scoring_version_change_requests_followup`, `test_priority_empty_repertoire_publishes_complete_generation`, `test_priority_missing_staged_row_withholds_publication`.
- GitHub issue #30: PostgreSQL miss evidence is exposed through a view, so source-change triggers must watch its backing tables and publication transition: `test_priority_miss_source_triggers_target_tables_and_publication`.
- PR #49 review R1: schema-20 cursor payloads and incomplete cursor-zero preparations recover through a fenced replacement or safe retry without stale publication: `test_priority_legacy_nonzero_cursor_requests_one_fenced_replacement`, `test_priority_cursor_zero_incomplete_preparation_recovers_with_or_without_legacy_signature`, and the disposable PostgreSQL priority recovery rehearsal.
- PR #49 review R2: shuffled recalculation after a committed preparation batch preserves one card per deterministic ordinal, while pre-fix manifests request a fresh generation: `test_priority_shuffled_retry_keeps_unique_cards_and_contiguous_ordinals`, `test_priority_old_ordering_manifest_requests_replacement_before_writing`, and the disposable PostgreSQL priority recovery rehearsal.
- PR #49 review R3: bounded retention protects both published and active priority generations, reclaims abandoned state, and rejects an expired lease: `test_postgres_cutover_priority_retention_locks_bounded_primary_keys`.
- PR #49 review R4: production game-position publication and direct visible backing-row changes advance the priority epoch, including publication between paged personal-evidence reads; stale preparations are rejected, the prior publication survives, and replacement uses current evidence. The schema-22 upgrade fences pre-repair preparations once: `priority_position_publication_invalidates_prepared_generation` in `scripts/check_postgres_priority_recovery.py`, exercised by the regular disposable PostgreSQL gate.
- GitHub issue #30: frozen shared-card and transposition evidence is independent of record enumeration; source/scoring invalidation and PostgreSQL crash recovery publish only complete current generations: `test_priority_frozen_shared_transposition_evidence_is_order_independent` and `scripts/check_postgres_priority_recovery.py` in the regular Docker durability gate.
- GitHub issue #30: retention reclaims abandoned prepared rows while preserving the active generation: `test_priority_retention_reclaims_abandoned_preparations_but_keeps_active`.
- Every PostgreSQL priority request admits a Celery task for the same generation, including coverage and game handoffs: `test_postgres_priority_request_admits_matching_celery_generation`, `test_postgres_maia_submit_publishes_candidates_in_bounded_sets`.
- PostgreSQL discovery admission creates its graph rebuild under the active lease and admits a published card exactly once: `test_postgres_discovery_branch_rebuild_is_admitted_once_under_lease`, `test_postgres_discovery_admission_completes_published_card_without_replay`.
- PostgreSQL daily statistics refresh uses a foreground receipt and a leased background publication, so stale delivery cannot publish a second snapshot: `test_postgres_daily_statistics_request_admits_same_day_task`, `test_postgres_daily_statistics_publishes_only_under_current_lease`.
- PostgreSQL defensive audit and historical backfill commands return the durable task ID through their foreground receipt: `test_postgres_defensive_admin_receipt_matches_durable_task`.
- PostgreSQL defensive candidate dismiss, approve, train-now, pause, and resume decisions share one foreground receipt; approval refreshes the queue and resume admits defensive work in that transaction: `test_postgres_defense_candidate_action_uses_one_receipt_and_followup`.
- PostgreSQL defensive engine claims reclaim one stale lease, report callbacks validate evidence and lease before queuing candidates, and failure/release/retry preserve their HTTP contracts: `test_postgres_threat_claim_reclaims_one_lease_and_returns_claimed_request`, `test_postgres_threat_report_validates_lease_and_queues_candidates_atomically`, `test_postgres_threat_failure_release_retry_preserve_http_contract`, `test_postgres_threat_routes_reuse_engine_operation_id`.
- Per-repertoire statistics show distinct practice and game percentages and report service failure instead of false zeroes: `repertoire statistics separates study accuracy and game adherence and opens supporting games`; `repertoire statistics shows an actionable failure instead of zero values`.
- Per-repertoire statistics GETs remain query-only and satisfy the foreground read budget: `test_repertoire_statistics_gets_are_query_only_and_within_foreground_read_budget`.
- Per-repertoire game comparisons stay visibly stale while card counts remain available: `repertoire statistics labels stale game comparisons while retaining published card counts`.
- First publication never presents placeholder zero counts as real statistics: `repertoire statistics hides empty counts until the first graph is published`.
- Failed game recomparisons show a recovery path rather than a silent stale figure: `repertoire statistics directs a failed comparison task to service status`.
- Repertoire statistics retain readable metric and board layouts at phone and desktop widths: pinned `Repertoire statistics 390` and `Repertoire statistics 1280` visual baselines.

## Authored Studies and generalized study exercises

- A FEN-only PGN root is discarded or imports directly into training: `backend/tests/test_studies.py::test_study_fen_only_import_preserves_root_and_does_not_enroll`.
- Updating changed source text replaces old position occurrences or local rubrics, or fails exact reimport idempotency: `backend/tests/test_studies.py::test_study_changed_source_update_preserves_old_occurrences_and_rubrics`.
- A square exercise from a zero-move root cannot use the shared queue exactly once: `backend/tests/test_studies.py::test_study_square_exercise_from_fen_only_position_reviews_once`; `tests/browser/studies.spec.ts` (`FEN-only study square exercise is authored enrolled and reviewed through the real workspace`).
- A correct response after revealing a hint receives unguided scheduling: `backend/tests/test_studies.py::test_study_correct_answer_after_hint_is_saved_as_guided_again`.
- A queued card whose pinned revision diverged from its exercise can still create an attempt or review: `backend/tests/test_studies.py::test_study_attempt_rejects_card_revision_mismatch_without_scheduling`.
- Editing an assessment silently changes old meaning or erases scheduling evidence: `backend/tests/test_studies.py::test_study_assessment_revision_reset_preserves_old_review`.
- Native content transfer loses stable IDs or accepts changed content under an existing identity: `backend/tests/test_studies.py::test_study_native_bundle_preserves_identity_and_rejects_changed_content`.
- Card ownership migration loses legacy reviews, queue rows, or foreign keys: `backend/tests/test_studies.py::test_study_migration_preserves_legacy_cards_reviews_and_foreign_keys`.
- Python and browser exercise graders disagree on authored move, square, route, choice, or explanation cases: `backend/tests/test_studies.py::test_study_python_grader_matches_original_golden_cases`; `tests/unit/study-exercise-grading.test.ts`.
- A prepared study answer cannot be completed offline or syncs only a grade: `tests/browser/studies.spec.ts` (`prepared study response is graded offline and replayed with its actual squares`).
- A committed explanation cannot be self-assessed and validated after a phone disconnect: `tests/browser/studies.spec.ts` (`prepared explanation is self assessed offline and replayed through server validation`).
- A stale study revision discards the phone's actual response, or an unknown grader version is answered offline: `tests/browser/studies.spec.ts` (`stale study revisions retain the phone answer as a replay conflict`; `unknown prepared study grader versions are unavailable offline`).
- Feedback transport failure after a committed study answer blocks recovery or duplicates an attempt: `tests/browser/studies.spec.ts` (`saved study attempt can retry feedback without a duplicate review`).
- A Study service outage presents a raw fetch failure without a recovery instruction: `tests/browser/studies.spec.ts` (`Study workspace reports an actionable local service outage`).
- Reviewing one study exercise exposes a sibling answer on the same day, or an explicit practice override stays hidden: `backend/tests/test_studies.py::test_study_answer_revealing_sibling_is_buried_until_explicit_practice`.
- PGN branches or annotations vanish and malformed source records look enrollable: `backend/tests/test_studies.py::test_study_pgn_preview_keeps_variations_annotations_and_invalid_record_diagnostics`.
- A failed card migration leaves partial schema or no restorable pre-migration backup: `backend/tests/test_studies.py::test_study_migration_failure_rolls_back_and_backup_restores`.
- Study introductions consume the opening allowance or exceed their own allowance: `backend/tests/test_studies.py::test_study_new_exercise_allowance_is_independent_and_due_reviews_remain`.
- Exercise types cannot be authored from the actual workspace: `tests/browser/studies.spec.ts` (`all five study exercise types can be authored from the workspace`).
- A large mixed queue makes foreground workspace reads exceed one second while background work is backlogged: `backend/tests/test_durable_work_queue.py::test_workspace_reads_complete_under_one_second_during_full_background_backlog`.

## PostgreSQL and Celery cutover rehearsal

- PostgreSQL prioritized opening refresh reorders gameplay misses, breadth, or shared cards while moving selection outside a transaction: `backend/tests/test_postgres_cutover.py::test_postgres_priority_opening_plan_preserves_gameplay_breadth_and_shared_cards`.
- A restarted prioritized opening slice admits two cards or accepts an expired lease: `backend/tests/test_postgres_cutover.py::test_postgres_priority_opening_slice_checkpoints_one_item_and_rejects_stale_replay`.
- Prioritized opening publication sends SQLite `json_extract` through a native PostgreSQL cursor and aborts the durable queue refresh: `backend/tests/test_postgres_cutover.py::test_postgres_priority_opening_publication_translates_opportunity_json`. A disposable PostgreSQL end-to-end run exposed this at phase 12, then completed after the fix.
- PostgreSQL study admission exceeds the daily allowance, admits a buried sibling, or duplicates a replayed card: `backend/tests/test_postgres_cutover.py::test_postgres_study_admission_slices_respect_quota_burial_and_replay`.
- Study admission runs ahead of foreground activity or publishes a stale task lease after restart: `backend/tests/test_postgres_cutover.py::test_postgres_study_admission_waits_for_foreground_and_checkpoints_restart`.
- A PostgreSQL queue mix publishes a stale membership after a foreground edit or replays an expired lease: `backend/tests/test_postgres_cutover.py::test_postgres_queue_randomization_replans_changed_membership_and_rejects_stale_lease`.
- An unchanged or empty queue plan advances after a foreground admission changes its membership: `backend/tests/test_postgres_cutover.py::test_postgres_queue_randomization_noop_rechecks_foreground_membership`.
- Queue mixing uses a correlated review lookup that exceeds the 50 ms PostgreSQL background-read bound: `backend/tests/test_postgres_cutover.py::test_postgres_queue_randomization_splits_review_lookup_into_bounded_reads`.
- PostgreSQL opening quarantine holds a database transaction while validating cards, skips a valid card, or repeats a prior diagnostic: `backend/tests/test_postgres_cutover.py::test_postgres_opening_quarantine_validates_outside_database_and_replays_once`.
- Quarantine runs ahead of foreground work or loses its cursor and clears diagnostics again after restart: `backend/tests/test_postgres_cutover.py::test_postgres_opening_quarantine_yields_to_foreground_and_resumes_cursor`.
- A queue refresh reports a ready generation while its durable task remains leased, or advances generation again on stale replay: `backend/tests/test_postgres_cutover.py::test_postgres_queue_projection_and_task_completion_commit_together`.
- Celery marks a queue slice complete outside its atomic projection transaction or fails to wake the next slice: `backend/tests/test_postgres_cutover.py::test_postgres_queue_celery_dispatch_keeps_atomic_slice_receipt`.
- Repeated PostgreSQL queue-ensure commands create duplicate active refresh jobs or leave the projection in a failed state: `backend/tests/test_postgres_cutover.py::test_postgres_queue_ensure_command_coalesces_active_refresh`.

- SQLite syntax crosses the PostgreSQL adapter without an explicit translation or error: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_translates_placeholders_and_rejects_runtime_pragma`.
- Reordered JSON fields change a command's durable idempotency identity: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_idempotency_digest_is_payload_order_independent`.
- A command timeout is presented as a failed or successful save: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_ambiguous_timeout_stays_pending`.
- Broker failure gives the browser false success or no recovery action: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_broker_failure_reports_actionable_error`.
- A background database slice starts while another process holds foreground admission: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_foreground_admission_blocks_background_slice`.
- A Celery `202` response drops a phone or desktop review before PostgreSQL confirms it: `tests/unit/review-outbox-regressions.test.ts` (`keeps a Celery-accepted review until its operation receipt confirms the save`).
- A phone review is lost or double counted after a competing computer review or an uncertain save: `backend/tests/test_phone_offline_training.py` (`test_phone_review_after_computer_review_is_credited_once_in_completion_order`, `test_older_phone_conflict_keeps_computer_schedule_and_warns_when_snapshot_missing`, `test_legacy_phone_retry_after_uncertain_save_does_not_count_twice`, `test_phone_repeat_is_credited_when_computer_did_not_schedule_that_repeat`, `test_phone_review_of_changed_card_is_preserved_with_actionable_warning`); `tests/unit/review-outbox-regressions.test.ts` (`retries one phone review identity after an uncertain save and clears it only on confirmation`, `keeps a phone review when the server omits persistence confirmation`); `tests/browser/phone-offline-training.spec.ts` (`competing computer review credits the saved phone result and explains the schedule fallback`).
- A stale persisted workspace projection appears as a successful live API read: `tests/unit/background-workspace-regressions.test.tsx` (`stale API cache waits for a live response before reporting success`); `tests/unit/game-contract-recovery-regressions.test.tsx` (`corrected live game data wins over an older persisted cache`).
- Priority cleanup relies on SQLite rowids, deletes an unbounded PostgreSQL batch, or races a newer task generation after checking a stale lease: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_priority_retention_locks_bounded_primary_keys`.
- PostgreSQL rows silently yield column names when legacy callers unpack aggregate values: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_row_supports_mapping_and_sqlite_value_iteration`.
- Reusing a command ID for a different payload returns another save's receipt: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_reused_key_cannot_return_another_commands_receipt`.
- The activity read fails on a SQLite JSON-array function after migration: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_schema_keeps_json_array_length_available`.
- A Games summary refresh aborts the selected game's pending detail read and leaves the board loading: `tests/browser/games-board-context.spec.ts` (`Games shows loading until the selected game's full moves arrive`).
- Games repeatedly refetches its own summary after each workspace-data ready event, overwhelming related endpoints and hiding a decision error: `tests/browser/real-game-feedback.spec.ts` (`Games shows reanalysis without repeatedly fetching its own summary`).
- A legacy discovery admission HTTP 202 is mistaken for a Celery operation and loops as an unconfirmed save: `tests/unit/discovery-admission-outbox-regressions.test.ts` (`legacy discovery admission 202 keeps its intent receipt without a Celery operation ID`); `tests/browser/recovery.spec.ts` (`legacy discovery timeout reopens as an unconfirmed save and retries the same choice`).
- PostgreSQL Study chapter and link edits bypass the named Celery command boundary: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_study_chapter_routes_dispatch_named_commands`. Concurrent chapter creation was also checked against the isolated PostgreSQL rehearsal; the parent row lock produced distinct positions 0 through 3.
- PostgreSQL Study archive or unarchive bypasses Celery, or archive reports success without queue-refresh admission: `backend/tests/test_postgres_cutover.py::test_postgres_study_archive_routes_dispatch_idempotent_commands` and `backend/tests/test_postgres_cutover.py::test_postgres_study_archive_queues_refresh_with_mutation`; a rolled-back PostgreSQL rehearsal verified the durable task enqueue.
- PostgreSQL exercise suspend, resume, or archive bypasses Celery or leaves its card and queue state inconsistent: `backend/tests/test_postgres_cutover.py::test_postgres_exercise_availability_routes_dispatch_idempotent_commands` and `backend/tests/test_postgres_cutover.py::test_postgres_exercise_availability_mutates_cards_and_queue_in_one_command`.
- PostgreSQL exercise creation or enrollment bypasses Celery, or an enrollment replay creates a second card or refresh: `backend/tests/test_postgres_cutover.py::test_postgres_exercise_create_and_enroll_routes_dispatch_idempotent_commands` and `backend/tests/test_postgres_cutover.py::test_postgres_exercise_enrollment_replay_creates_one_card_and_queues_once`; a rolled-back PostgreSQL rehearsal executed the full create/enroll/replay/suspend/resume/archive chain.
- PostgreSQL exercise “train now” bypasses Celery or a replay inserts a second explicit queue entry: `backend/tests/test_postgres_cutover.py::test_postgres_exercise_train_now_dispatches_foreground_command` and `backend/tests/test_postgres_cutover.py::test_postgres_exercise_train_now_replay_keeps_one_explicit_queue_entry`; a rolled-back PostgreSQL rehearsal verified create/enroll/train-now/replay with one entry.
- PostgreSQL exercise revision bypasses Celery, loses the distinction between omitted and explicitly cleared metadata, or leaves an active stale card after a material reset: `backend/tests/test_postgres_cutover.py::test_postgres_exercise_revision_dispatch_preserves_optional_field_presence` and `backend/tests/test_postgres_cutover.py::test_postgres_exercise_revision_keeps_metadata_schedule_and_resets_material_card`; rolled-back PostgreSQL SQL verified notes-only and material revision paths.
- PostgreSQL Study attempts bypass Celery, replay into duplicate rows or reviews, accept two answers for one queue entry, or lose pending self-assessment: `backend/tests/test_postgres_cutover.py::test_postgres_study_attempt_routes_dispatch_idempotent_foreground_commands`, `backend/tests/test_postgres_cutover.py::test_postgres_study_practice_attempt_replays_and_self_assesses_once`, and `backend/tests/test_postgres_cutover.py::test_postgres_study_review_attempt_rejects_second_answer_for_one_queue_entry`; rolled-back PostgreSQL rehearsal verified practice, automatic review scheduling, pending review self-assessment, and replay.
- A Study exercise runner treats Celery HTTP 202 as saved or retries with a new ID: `tests/unit/study-attempt-pending-regressions.test.tsx` (`Study attempt and self-assessment retry pending Celery operations with the same IDs`).
- A background Study admission acts on a stale pre-lock candidate after a foreground answer buries its sibling or fills the daily quota: `backend/tests/test_postgres_cutover.py::test_postgres_study_admission_rechecks_burial_and_quota_after_foreground_lock`.
- A foreground queue insert races a background admission or review reorder for the same position, or expected PostgreSQL contention exhausts task retries: `backend/tests/test_postgres_cutover.py::test_postgres_review_reserves_card_then_queue_position_before_reordering`, `backend/tests/test_postgres_cutover.py::test_postgres_queue_randomization_noop_rechecks_foreground_membership`, and `backend/tests/test_postgres_cutover.py::test_postgres_queue_contention_yields_without_spending_retry_or_replaying_stale_lease`. Two rehearsal PostgreSQL sessions verified same-date lock serialization and that a 25 ms background lock timeout yields `LockNotAvailable`.
- PostgreSQL queue fail and bury writes bypass Celery or duplicate a replayed bury: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_queue_fail_and_bury_dispatch_foreground_commands`; direct rehearsal executed both commands and verified a repeated operation ID returned the same receipt without burying twice.
- A Celery result-store outage after accepted dispatch is reported as a failed save before checking the PostgreSQL receipt: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_result_store_outage_checks_receipt_before_reporting_pending`.
- SQL translation adds `NULLS FIRST` to PostgreSQL `ON CONFLICT` targets and breaks translated upserts: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_conflict_target_does_not_include_null_ordering`; insert and conflict-update were executed against the isolated PostgreSQL rehearsal.
- Card identity copy paths query SQLite `PRAGMA table_info(cards)` after PostgreSQL cutover: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_card_copy_uses_backend_column_catalog`; the read-only rehearsal role returned all 32 cards columns in storage order.
- A PostgreSQL review bypasses Celery or two concurrent commands create duplicate reviews for one queue attempt: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_review_route_dispatches_idempotent_command`; a direct two-worker rehearsal created exactly one review and returned complete receipts for both operations. Existing review and queue backend tests passed after replacing SQLite last-insert calls with `RETURNING id`.
- A restarted PostgreSQL background slice advances with a stale generation or lease: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_background_slice_restarts_only_with_current_lease`. The PostgreSQL rehearsal also verified claim, atomic cursor advance, reclaim, and stale-lease rejection.
- The PostgreSQL queue read scans repertoire choices for the full card catalog instead of today's active cards: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_queue_repertoire_choices_scan_only_active_cards`. Same-data response parity and old/new latency samples are recorded in `docs/POSTGRES-REHEARSAL-2026-09-27.md`.
- A restart during queue unlocking skips eligible opening cards when replay changes the locked-card set: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_queue_unlock_slice_replays_and_advances_without_skips`.
- PostgreSQL game-refresh work starts during foreground activity or replays an expired lease into another game derivation: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_game_refresh_waits_for_foreground_and_discards_stale_replay`. A rolled-back PostgreSQL rehearsal ran the real SQL and task generation update.
- A PostgreSQL background read enters while foreground work is active: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_background_reads_respect_foreground_admission`.
- Threat-report repair runs ahead of foreground work or lets an expired lease replay its publication: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_threat_report_audit_yields_and_replays_once`. The real PostgreSQL audit also ran in a rolled-back rehearsal transaction.
- A Celery poll favors one background task kind over a higher-priority supported task, or claims an unported handler: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_background_claim_orders_supported_kinds_by_priority`. The allowed-kind claim SQL also ran against the isolated PostgreSQL rehearsal in a rolled-back transaction.
- Rubric audit passes SQLite integer truth values to PostgreSQL `CASE WHEN`, aborting publication: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_rubric_audit_uses_boolean_case_parameter`. A rolled-back PostgreSQL rehearsal exposed SQLSTATE `42804`, then completed after the boolean fix.
- PostgreSQL rubric audit reads ahead of foreground work or publishes a stale lease after restart: `backend/tests/test_postgres_cutover.py::test_postgres_cutover_rubric_audit_yields_and_discards_stale_replay`; the existing SQLite integration `backend/tests/test_defensive_threat_persistence.py::test_defensive_rubric_audit_yields_to_foreground_and_replays_after_restart` covers persisted replay. The gated PostgreSQL path was exercised in a rolled-back rehearsal.
- A pinned activity-tray screenshot reloads while workspace data is still loading, aborts `/api/tactics/progress`, and captures an unrelated error badge: `tests/browser/visual.spec.ts` (`activity-tray-390`, `activity-tray-1280`). The fixture supplies the activity response before initial navigation and asserts no notification badge before each screenshot.
- A SQLite volume archive copies only the main database file while committed WAL pages are missing, and its queue fingerprint disagrees with the manifest: `backend/tests/test_sqlite_cutover_snapshot.py::test_cutover_snapshot_includes_committed_uncheckpointed_wal_and_verifies_manifest`.
- A verified SQLite snapshot becomes unusable when Docker remounts it at a different path: `backend/tests/test_sqlite_cutover_snapshot.py::test_cutover_snapshot_includes_committed_uncheckpointed_wal_and_verifies_manifest` checks a relocated copy against the same digest and logical manifest.
- A PostgreSQL backup restores without operation receipts, publication state, or another public table: `backend/tests/test_postgres_backup_verification.py::test_postgres_backup_comparison_rejects_missing_table_and_changed_rows`; the disposable Docker gate restores and compares every public table before browser tests.
- PostgreSQL game account settings bypass Celery or leave stale provider credentials after an account is cleared: `backend/tests/test_postgres_cutover.py::test_postgres_game_accounts_update_dispatches_foreground_command` and `backend/tests/test_postgres_cutover.py::test_postgres_game_accounts_update_reconciles_provider_rows`.
- PostgreSQL mode blocks a read-only endgame tablebase probe merely because it uses HTTP POST: `backend/tests/test_postgres_cutover.py::test_postgres_endgame_probe_uses_read_only_tablebase_path`.
- PostgreSQL mode blocks pure card validation merely because it uses HTTP POST: `backend/tests/test_postgres_cutover.py::test_postgres_card_validation_remains_available_without_a_write_worker`.
- PostgreSQL mode blocks the read-only analysis paste preview or lets its HTTP POST open a writer: `backend/tests/test_postgres_cutover.py::test_postgres_analysis_paste_preview_uses_query_only_reader_without_write_worker`.
- A repeated analysis paste preview reparses every repertoire line, or a changed line reuses a stale position map: `backend/tests/test_analysis_paste.py::test_paste_position_map_cache_reuses_snapshot_and_invalidates_after_line_change`.
- PostgreSQL analysis paste saves from the API process, loses its idempotency key, or skips the read-only preview boundary: `backend/tests/test_postgres_cutover.py::test_postgres_analysis_paste_commit_dispatches_foreground_command_after_read_only_preview`.
- A completed PostgreSQL paste save changes the preview snapshot and makes replay of its original operation ID fail with 409 or rebuild the expensive preview: `backend/tests/test_postgres_cutover.py::test_postgres_analysis_paste_replay_uses_stable_user_request_after_snapshot_changes` and `test_postgres_analysis_paste_completed_replay_returns_receipt_before_rebuilding_preview`.
- A pending analysis paste appears saved, retries under a new operation ID, or allows a changed selection to overtake it: `tests/unit/analysis-paste-pending-regressions.test.ts::a pending analysis paste save reuses its operation ID and blocks a changed selection`.
- PostgreSQL Builder branch removal bypasses Celery, deletes lines from another route or starting position, or omits the graph rebuild intent: `backend/tests/test_postgres_cutover.py::test_postgres_branch_removal_dispatches_foreground_command_with_idempotency` and `test_postgres_branch_removal_matches_only_position_and_move_prefix_and_queues_graph`.
- A pending branch removal changes the board before the delete is confirmed or retries under another operation ID: `tests/unit/branch-removal-pending-regressions.test.ts::a pending branch removal reuses its operation ID and cannot delete a changed route`.
- PostgreSQL settings changes bypass Celery, lose an omitted defense flag, fail to request the daily queue refresh, or claim success before coverage jobs have been ported: `backend/tests/test_postgres_cutover.py::test_postgres_settings_update_dispatches_foreground_command`, `backend/tests/test_postgres_cutover.py::test_postgres_settings_update_refreshes_queue_and_preserves_omitted_defense_flag`, and `backend/tests/test_postgres_cutover.py::test_postgres_settings_update_rejects_unsupported_coverage_refresh`.
- The settings screen reports a Celery 202 as saved, retries with a new operation ID, or overtakes an earlier pending save: `tests/unit/settings-save-pending-regressions.test.ts` (`settings retries a pending Celery save with its durable operation ID`; `settings does not overtake an earlier pending save with changed values`).
- PostgreSQL game sync fetches an unbounded Lichess history before yielding its background slice: `backend/tests/test_game_sync.py::test_lichess_sync_window_bounds_provider_request_before_persistence` and `backend/tests/test_game_sync.py::test_lichess_sync_window_rejects_unbounded_page_size`.
- PostgreSQL game sync loops over every Chess.com archive in one fetch instead of checkpointing by month: `backend/tests/test_game_sync.py::test_chesscom_sync_fetches_one_archive_per_restartable_window`.
- Two concurrent PostgreSQL game-sync requests both create active jobs, or job creation commits without its durable background task: `backend/tests/test_postgres_cutover.py::test_postgres_game_sync_admission_serializes_and_checkpoints_job` and `backend/tests/test_postgres_cutover.py::test_postgres_game_sync_admission_coalesces_existing_active_job`.
- PostgreSQL provider game publication saves a game without its analysis and derivation intents, or duplicates the game on replay: `backend/tests/test_postgres_cutover.py::test_postgres_game_sync_publishes_one_game_and_followup_intents_atomically`; a rolled-back PostgreSQL rehearsal verified insert, duplicate replay, and update against the real schema.
- A game-sync record enters PostgreSQL during foreground activity or an expired lease replays the publication: `backend/tests/test_postgres_cutover.py::test_postgres_game_sync_record_waits_for_foreground_and_discards_stale_replay`.
- A provider sync window reads during foreground activity, skips a full page containing rejected games, or redispatches a staged record after lease replay: `backend/tests/test_postgres_game_sync_windows.py::test_game_sync_window_waits_for_foreground_before_database_read`, `backend/tests/test_postgres_game_sync_windows.py::test_game_sync_window_splits_a_full_provider_page_without_skipping_rejections`, and `backend/tests/test_postgres_game_sync_windows.py::test_game_sync_window_replay_dispatches_each_staged_record_once`.
- PostgreSQL sync reports completion before every provider window and record receipt completes, or a terminal provider failure leaves the job running: `backend/tests/test_postgres_game_sync_windows.py::test_game_sync_completion_waits_for_every_window_and_record_receipt` and `backend/tests/test_postgres_game_sync_windows.py::test_game_sync_terminal_task_failure_marks_job_failed`.
- A dense Chess.com monthly archive exceeds the staging bound, or split windows double-count games outside their time range: `backend/tests/test_postgres_game_sync_windows.py::test_game_sync_window_splits_dense_chesscom_month_into_bounded_time_ranges` and `backend/tests/test_game_sync.py::test_chesscom_split_month_counts_only_games_inside_its_time_window`.
- PostgreSQL game-sync HTTP bypasses the Celery command or drops its idempotency key: `backend/tests/test_postgres_cutover.py::test_postgres_game_sync_route_dispatches_celery_command`.
- A PostgreSQL game-sync admission ignores a provider rate limit and queues repeated requests: `backend/tests/test_postgres_cutover.py::test_postgres_game_sync_admission_preserves_provider_rate_limit`.
- An active SQLite game-sync job imported into PostgreSQL has no new window tasks and remains stuck after the browser retries: `backend/tests/test_postgres_cutover.py::test_postgres_game_sync_admission_recovers_imported_active_job_without_windows`.
- The browser reports a pending game-sync command as failed or starts another repair request while the first command has no receipt: `tests/unit/game-sync-command-pending-regressions.test.ts` (`game sync retains its operation ID until Celery confirms admission`; `game sync does not overtake a pending command with a different repair request`).
- PostgreSQL position notes bypass Celery or an empty note remains after the user clears it: `backend/tests/test_postgres_cutover.py::test_postgres_annotation_route_dispatches_idempotent_foreground_command` and `backend/tests/test_postgres_cutover.py::test_postgres_annotation_command_clears_empty_position_note`.
- A failed or pending position-note save appears in the browser's confirmed local copy, or retry uses a new operation ID: `tests/unit/position-annotation-pending-regressions.test.ts` (`a pending note keeps its operation ID and never enters the confirmed local copy`; `a failed note save leaves no confirmed local annotation`).
- PostgreSQL repertoire renames bypass Celery or lose their idempotency key: `backend/tests/test_postgres_cutover.py::test_postgres_repertoire_rename_dispatches_idempotent_foreground_command`.
- A pending repertoire rename appears complete or retries with a new operation ID: `tests/unit/repertoire-rename-pending-regressions.test.ts::a pending repertoire rename reuses its operation ID and waits for a receipt`.
- PostgreSQL repertoire deletion bypasses Celery, loses its idempotency key, cascades a shared card, or omits the game refresh intent: `backend/tests/test_postgres_cutover.py::test_postgres_repertoire_delete_dispatches_idempotent_foreground_command` and `test_postgres_repertoire_delete_preserves_shared_cards_and_queues_game_refresh`.
- A pending repertoire deletion removes a different repertoire or retries under another operation ID: `tests/unit/repertoire-delete-pending-regressions.test.ts::a pending repertoire deletion cannot remove another repertoire and reuses its operation ID`.
- A queued graph rebuild publishes after its repertoire is deleted and fails on a foreign key: `backend/tests/test_postgres_cutover.py::test_postgres_repertoire_delete_cancels_stale_graph_before_parent_removal`.
- A confirmed PostgreSQL repertoire deletion remains visible while the training queue refreshes: `tests/browser/workspace-flows.spec.ts::sample deletion uses repertoire identity and does not delete its same-filename sibling`.
- PostgreSQL endgame template admission writes from the API process or loses its idempotency key: `backend/tests/test_postgres_cutover.py::test_postgres_endgame_template_admission_dispatches_foreground_command`.
- Endgame template admission looks complete while its write is pending or retries with a new operation ID: `tests/unit/endgame-template-pending-regressions.test.ts::endgame admission remains pending and reuses its operation ID`.
- A PostgreSQL tablebase miss tries to write its cache from the read-only API role: `backend/tests/test_postgres_cutover.py::test_postgres_tablebase_probe_never_writes_from_read_only_api`.
- PostgreSQL tablebase lookups fail when Redis is unavailable or repeatedly fetch a successfully cached position: `backend/tests/test_postgres_cutover.py::test_postgres_tablebase_redis_cache_hit_and_failure_fallback`.
- PostgreSQL endgame attempts perform generation or network I/O inside a write transaction, or retry with a different position: `backend/tests/test_postgres_cutover.py::test_postgres_endgame_attempt_prepares_outside_worker_and_dispatches_idempotently`.
- A pending endgame attempt creates a second position or loses its operation ID: `tests/unit/endgame-attempt-pending-regressions.test.ts::a pending endgame attempt keeps its operation ID and does not create another position`.
- PostgreSQL migration 002 is applied but the API rejects its own current schema at startup: `backend/tests/test_postgres_cutover.py::test_postgres_startup_accepts_latest_checked_in_schema`.
- Opening-graph traversal holds a PostgreSQL read transaction or per-line slicing changes graph identity: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_line_slices_match_whole_graph_and_close_read_before_traversal`.
- Opening-graph staging writes an unbounded line or loses its cursor on replay: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_stage_checkpoints_eight_steps_and_replays_by_cursor`.
- Opening-graph links become visible as one unbounded transaction or publication starts before linking finishes: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_links_eight_cards_before_publication`.
- An incomplete or superseded PostgreSQL graph generation becomes visible: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_publication_rejects_missing_links_and_checks_generation`.
- Graph card state updates run as an unbounded publication transaction or precede the generation switch: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_classifies_eight_cards_after_publication`.
- PostgreSQL graph publication leaves stale integrity blocks or updates too many cards in one section: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_integrity_refreshes_two_cards_before_cleanup`.
- PostgreSQL graph cleanup removes a card still present in the new generation or sweeps all obsolete links in one transaction: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_cleanup_removes_only_obsolete_links_in_bounded_slices`.
- PostgreSQL graph marks a rebuild complete before its integrity scan intent is durable: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_finalization_checkpoints_integrity_scan_and_completion`.
- Cold SQL translation consumes the 50 ms graph-finalization transaction budget: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_finalization_warms_sql_before_bounded_transaction`.
- An opening-graph slice starts while foreground work is active or replays after its lease is replaced: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_stage_yields_to_foreground_and_discards_restart_replay`.
- PostgreSQL graph tasks remain unsupported by the Celery worker or skip a restartable phase: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_worker_routes_each_restartable_phase`.
- A graph task row is absent after import and a new task reuses a historical graph generation: `backend/tests/test_postgres_opening_graph.py::test_postgres_graph_enqueue_advances_past_imported_generation_without_task_row`.
- A PostgreSQL branch edit writes from the API process or leaves its graph rebuild intent behind: `backend/tests/test_postgres_cutover.py::test_postgres_branch_edit_dispatches_foreground_command_with_idempotency` and `test_postgres_branch_edit_checkpoints_graph_rebuild_with_line`.
- A pending branch save appears successful or replays under another operation ID: `tests/unit/branch-command-pending-regressions.test.ts::a pending branch save keeps its operation ID and cannot appear saved`.
- A PostgreSQL prefix split decision writes from the API process or loses its idempotency key: `backend/tests/test_postgres_cutover.py::test_postgres_prefix_split_decisions_dispatch_foreground_commands_with_idempotency`.
- A pending prefix split appears complete, changes from accept to reject, or retries under a new command ID: `tests/unit/prefix-split-pending-regressions.test.ts::a pending prefix split reuses its command ID and blocks a conflicting rejection`.
- PostgreSQL card revision bypasses Celery, drops its observed revision, overwrites a newer or superseded card, or returns the source revision for an existing target: `backend/tests/test_postgres_cutover.py::test_postgres_card_revision_dispatches_expected_revision_and_idempotency`, `test_postgres_card_revision_rejects_stale_edit_before_mutating_cards`, and `test_postgres_card_revision_reports_existing_target_revision`.
- A pending card edit appears saved, changes its requested revision, retries under another operation ID, or loses an existing target card's revision: `tests/unit/card-revision-pending-regressions.test.ts::a pending card edit reuses its command ID and blocks a changed revision`.
- PostgreSQL card archiving writes from the API process, loses its idempotency key, or returns a stale clean integrity status after requesting a new scan: `backend/tests/test_postgres_cutover.py::test_postgres_card_archive_dispatches_idempotent_foreground_command` and `test_postgres_card_archive_returns_unchecked_integrity_after_scan_intent`.
- PostgreSQL discovery dismiss, acknowledge, or snooze bypasses Celery or drops its command key: `backend/tests/test_postgres_cutover.py::test_postgres_opportunity_state_actions_dispatch_idempotent_commands`.
- A pending discovery action appears complete or allows a different action on the same discovery to overtake it: `tests/unit/opportunity-action-pending-regressions.test.ts::a pending discovery action reuses its operation ID and blocks another action on that discovery`.
- Saved discovery training admits a card without its prefix graph rebuild, bypasses the queue lock, or reports a pending save as queued: `backend/tests/test_postgres_cutover.py::test_postgres_discovery_training_checkpoints_prefix_graph_intent` and `tests/unit/opportunity-action-pending-regressions.test.ts::training a saved discovery waits for a confirmed queue receipt`.
- PostgreSQL discovery refresh starts a database slice ahead of foreground work, publishes after a replaced lease, or uses SQLite modulo syntax for recurring decisions: `backend/tests/test_postgres_cutover.py::test_postgres_opportunity_refresh_yields_to_foreground_and_discards_restart_replay` and `test_postgres_opportunity_recurring_query_uses_native_modulo`.
- A discovery refresh writes from the API process or reports a pending enqueue as complete: `backend/tests/test_postgres_cutover.py::test_postgres_opportunity_refresh_dispatches_idempotent_command` and `tests/unit/opportunity-refresh-pending-regressions.test.ts::a pending discovery refresh keeps one command ID until its queue receipt completes`.
- A PostgreSQL discovery recommendation reads while foreground work is active, keeps a database connection open during coverage traversal, or publishes after lease replacement: `backend/tests/test_postgres_cutover.py::test_postgres_discovery_recommendation_yields_to_foreground_and_discards_restart_replay`.
- An activity pause or priority change writes from the API process, loses its operation ID, or lets a later action overtake a pending one: `backend/tests/test_postgres_cutover.py::test_postgres_activity_control_dispatches_idempotent_command` and `tests/unit/activity-control-pending-regressions.test.ts::a pending activity control retains its ID and blocks an overtaking action`.
- PostgreSQL coverage seeding blocks foreground work, duplicates a node after restart, publishes a changed source generation, or reports a pending request as queued: `backend/tests/test_postgres_cutover.py::test_postgres_coverage_seed_yields_to_foreground_and_discards_restart_replay`, `test_postgres_coverage_seed_rejects_source_change_before_activation`, `test_postgres_coverage_seed_terminal_failure_marks_run_failed`, `test_postgres_coverage_refresh_dispatches_durable_seed`, and `tests/unit/coverage-refresh-pending-regressions.test.ts::a pending coverage refresh keeps its command ID until the build is queued`.
- A PostgreSQL branch add or removal leaves coverage stale because its background seed was not checkpointed with the line change or a running generation was reused: `backend/tests/test_postgres_cutover.py::test_postgres_branch_edit_checkpoints_graph_and_coverage_with_line`, `test_postgres_branch_removal_queues_coverage_with_changed_lines`, and `test_postgres_coverage_seed_supersedes_active_generation_after_branch_edit`.
- PostgreSQL portable snapshot export fails on its automatic coverage filter because a literal percent sign is parsed as a SQL placeholder: `backend/tests/test_postgres_cutover.py::test_postgres_migration_snapshot_binds_automatic_coverage_filter`.
- Maia coverage callbacks bypass Celery, take a foreground worker slot, lose a pending lease/save, ignore a paused run, or flood PostgreSQL with idle claim receipts: `backend/tests/test_postgres_cutover.py::test_postgres_maia_callbacks_dispatch_to_background_celery_with_receipts`, `test_postgres_maia_dispatch_reads_receipt_behind_background_gate`, `test_postgres_maia_background_contention_retries_same_operation`, `test_postgres_maia_idle_poll_reads_availability_without_receipt`, `test_postgres_maia_receipt_poll_uses_background_read_admission`, `test_postgres_maia_claim_respects_pause_and_uses_row_lease`, `test_postgres_maia_submit_publishes_candidates_in_bounded_sets`, and `tests/unit/maia-coverage-client-regressions.test.ts`.
- PostgreSQL coverage activation never starts Explorer, an imported active run has no task, an Explorer fetch holds a database section or overtakes foreground work, a stale lease republishes candidates, or a missing token silently leaves a run queued: `backend/tests/test_postgres_cutover.py::test_postgres_coverage_seed_queues_explorer_after_verified_activation`, `test_postgres_explorer_recovers_imported_active_run_once`, `test_postgres_explorer_closes_read_before_network_fetch`, `test_postgres_explorer_yields_node_read_to_foreground`, `test_postgres_explorer_discards_stale_lease_before_publication`, `test_postgres_explorer_restart_skips_fetch_for_expired_lease`, `test_postgres_explorer_missing_token_fails_run_with_actionable_error`, `test_postgres_explorer_terminal_failure_marks_run_failed`, and `test_postgres_explorer_session_token_reaches_separate_worker`.
- Repertoire rendering can replace a visible action-menu summary after a hover, leaving a one-shot hover assertion on an unhovered control: `tests/browser/layout.spec.ts` (`hover feedback covers controls outside the chessboard without changing the board`). The assertion reapplies hover while checking the actual computed gradient.
- PostgreSQL analysis progress or terminal task retry writes through the API process, loses an idempotency key, accepts an expired analysis lease, or leaves a retried task paused: `backend/tests/test_postgres_cutover.py::test_postgres_analysis_progress_and_task_retry_dispatch_out_of_api`, `test_postgres_analysis_progress_rejects_stale_lease_before_write`, and `test_postgres_manual_retry_requeues_and_unpauses_failed_task`.
- A PostgreSQL game-analysis poll writes from the API, floods command receipts when no work exists, claims a paused game, or races a second worker for the same job: `backend/tests/test_postgres_cutover.py::test_postgres_game_analysis_idle_claim_avoids_receipt_and_active_claim_uses_background_worker` and `test_postgres_game_analysis_claim_uses_skip_locked_and_preserves_lease_shape`.
- A Docker Stockfish position claim holds PostgreSQL across chess traversal, bypasses Celery, publishes after the game generation changes, endlessly polls an exhausted position, loses a pending report receipt, or accepts a stale position lease: `backend/tests/test_postgres_cutover.py::test_postgres_game_position_claim_dispatches_prepared_plan_to_background`, `test_postgres_game_position_preparation_closes_database_before_chess_traversal`, `test_postgres_game_position_claim_discards_stale_generation_before_write`, `test_postgres_game_position_exhausted_retry_fails_parent_instead_of_repolling`, `test_postgres_game_position_callbacks_dispatch_with_receipts`, `test_postgres_game_position_report_rejects_stale_lease_before_publication`, and `tests/unit/maia-coverage-client-regressions.test.ts::Docker engine callback waits for its PostgreSQL receipt with the worker header`.
- Legacy Stockfish timeout or NNUE provenance repair requeues multiple games, repeats after a receipt replay, or bypasses the Docker worker operation ID: `backend/tests/test_postgres_game_analysis_repairs.py::test_postgres_timeout_repair_requeues_one_locked_game_and_is_idle_safe`, `test_postgres_network_repair_advances_generation_and_records_provenance`, and `test_postgres_game_repairs_reuse_engine_operation_ids`.
- Discovery acceptance admits stale evidence, creates a duplicate task during replay, or loses the browser's operation ID: `backend/tests/test_postgres_discovery_acceptance.py::test_postgres_discovery_acceptance_creates_one_intent_and_durable_task`, `test_postgres_discovery_acceptance_replays_prior_without_duplicate_task`, `test_postgres_discovery_acceptance_rejects_stale_evidence_before_write`, and `test_postgres_discovery_acceptance_route_keeps_idempotency_key`.
- Manual game analysis writes a large result in one transaction, publishes after a stale lease, or loses the caller's operation ID: `backend/tests/test_postgres_manual_game_analysis.py::test_postgres_manual_analysis_admits_one_higher_generation_and_sliced_publication`, `test_postgres_manual_analysis_rejects_stale_lease_without_publishing`, `test_postgres_manual_analysis_replays_completed_request_key`, and `test_postgres_manual_analysis_route_retains_operation_id_and_returns_preparing`.
- PostgreSQL health reports success without a reader, both worker queues, or a queue that can progress, or PostgreSQL startup starts the SQLite writer/coordinator: `backend/tests/test_postgres_readiness.py::test_postgres_health_accepts_ready_queue_and_both_worker_classes`, `test_postgres_health_accepts_a_durable_queue_refresh_in_progress`, `test_postgres_health_reports_missing_worker_and_failed_queue`, and `test_postgres_startup_never_starts_sqlite_writer_or_coordinator`.
- The PostgreSQL Docker configuration accidentally mounts SQLite, gives the API a write credential, loses operation receipts or queue order on container recreation, or skips the browser product checks: `scripts/test-postgres-docker.mjs` runs as the `postgres_docker` stage of `make full` against `docker-compose.postgres.test.yml`.
- The Docker engine forgets a pending claim/report operation ID after a timeout or restart, or keeps replaying a definitively failed receipt: `tests/unit/durable-engine-request-regressions.test.ts::replays the same operation ID after a pending response and process restart`, `keeps an uncertain callback but removes a definitive failed receipt`, and `passes an explicit operation ID through the shared HTTP client`.
- PostgreSQL task claiming, expired lease recovery, retry, or contention yield erases an opening graph phase and stalls repertoire imports: `backend/tests/test_postgres_durable_phase.py::test_postgres_durable_claim_preserves_phase_after_expired_lease` and `test_postgres_durable_retry_and_contention_preserve_phase`.
- A foreground GET of Maia work availability waits behind its own foreground admission lease: `backend/tests/test_postgres_maia_availability_read.py::test_postgres_maia_availability_get_uses_foreground_reader`.
- A game position summary with no repertoire filter gives PostgreSQL an untyped NULL and returns HTTP 500: `backend/tests/test_postgres_position_summary.py::test_postgres_position_summary_types_optional_repertoire_filter`.
- PostgreSQL parent game-analysis callbacks bypass Celery, consume the foreground worker, or requeue a failed job without resetting its exhausted position and pause control: `backend/tests/test_postgres_cutover.py::test_postgres_game_parent_callbacks_dispatch_to_correct_celery_queue` and `test_postgres_game_parent_retry_resets_failed_position_and_pause`.
- A new PostgreSQL game-analysis generation hides or changes migrated analysis rows, or the SQLite importer tries to COPY into a read view: `backend/tests/test_postgres_cutover.py::test_postgres_versioned_game_analysis_import_targets_preserve_published_views`. Migration 009 also passed exact SQLite-to-PostgreSQL SHA-256 checks for all 126,356 move-analysis and 609,561 candidate rows in the disposable restored database.
- PostgreSQL game finalization writes analysis rows from the API, holds a long transaction over a full game, switches visibility before all rows exist, or loses follow-up work after publication: `backend/tests/test_postgres_cutover.py::test_postgres_game_finalization_admits_durable_publication_via_background_celery`, `test_postgres_game_publication_limits_each_slice_to_eight_moves`, `test_postgres_game_publication_rejects_incomplete_stage_before_visibility_switch`, and `test_postgres_game_publication_admission_checkpoints_durable_task`. Synthetic PostgreSQL rehearsals verified a 229-move publication in 30 bounded slices and a completed game with derivation, threat-scan, and repertoire follow-up intents.
- PostgreSQL study PGN preview is blocked as a write or opens a writer, and import commit bypasses Celery or loses parent links and duplicate detection: `backend/tests/test_postgres_cutover.py::test_postgres_study_import_preview_uses_query_only_reader_without_write_worker`, `test_postgres_study_import_commit_dispatches_parsed_source_with_stable_receipt`, and `test_postgres_study_import_worker_keeps_parent_links_and_duplicate_receipt`.
- A native study bundle import attempts SQLite `PRAGMA` on PostgreSQL or writes from the API process instead of the foreground Celery worker: `backend/tests/test_postgres_cutover.py::test_postgres_native_study_bundle_import_dispatches_celery_command`. A rolled-back PostgreSQL preserve/copy rehearsal also verified complete column inspection and chapter import.
- PostgreSQL guided review creation races another session, correction attempts advance the same item twice, or the browser treats a pending receipt as saved: `backend/tests/test_postgres_guided_review.py::test_postgres_guided_review_routes_dispatch_foreground_receipts`, `test_postgres_guided_review_start_locks_game_before_creating_session`, `test_postgres_guided_review_attempt_locks_session_before_advancing`, and `tests/unit/guided-review-pending-regressions.test.ts::a pending guided correction blocks another move and confirms the saved attempt`.
- Finding curation or acceptance writes from the API, races game exclusion, reprioritizes a noncanonical miss, or treats a pending receipt as saved: `backend/tests/test_postgres_findings.py::test_postgres_finding_routes_dispatch_idempotent_foreground_commands`, `test_postgres_finding_curation_locks_source_before_changing_status`, `test_postgres_accepted_repertoire_lapse_uses_canonical_miss_and_queue_lock`, and `tests/unit/game-finding-pending-regressions.test.ts::a pending finding decision keeps its operation ID and blocks another action`.
- Finding-card preview opens a writer, a save bypasses Celery, a stale tactical opportunity creates a card, or a pending save appears complete: `backend/tests/test_postgres_finding_cards.py::test_postgres_finding_card_preview_reads_only_and_save_dispatches`, `test_postgres_finding_card_save_rejects_stale_tactical_opportunity_before_writing`, and `tests/unit/finding-card-pending-regressions.test.ts::finding card preview does not save and a pending card save keeps its operation ID`.
- PostgreSQL finding preparation accidentally opens a writer or publishes evidence before a bounded derivation slice: `backend/tests/test_postgres_game_findings.py::test_postgres_game_findings_prepare_does_not_open_writer_or_publish`.
- Finding preparation scans all unseen cards in one background database section, delaying foreground work: `backend/tests/test_postgres_game_findings.py::test_postgres_game_findings_unseen_cards_use_short_paged_reads`.
- A PostgreSQL game derivation stops after repertoire comparison instead of handing off to findings, or its finding worker duplicates a staged item after restart: `backend/tests/test_postgres_game_repertoire.py::test_postgres_game_repertoire_stages_one_item_and_switches_all_views_together` and `backend/tests/test_postgres_game_findings.py::test_postgres_game_findings_stage_one_item_and_replay_after_restart`.
- A changed finding source publishes staged evidence from an older generation: `backend/tests/test_postgres_game_findings.py::test_postgres_game_findings_source_change_restarts_without_publication`.
- A new API route or mutation is omitted from the PostgreSQL cutover inventory: `backend/tests/test_postgres_route_contract.py::test_postgres_route_contract_matches_registered_endpoints` and `test_postgres_route_contract_tracks_remaining_blocked_product_routes`.
- A staged PostgreSQL mutation still receives the generic 503 from the runtime write guard, including discovery acceptance and game-analysis repair/manual save: `backend/tests/test_postgres_route_contract.py::test_staged_postgres_mutations_pass_the_runtime_write_guard`.
- PostgreSQL activity reports the inactive SQLite writer as unhealthy and falsely tells the user to restart Tempo: `backend/tests/test_postgres_operational_status.py::test_postgres_activity_omits_inactive_sqlite_writer_health`, `test_postgres_task_status_omits_inactive_sqlite_writer_health`, and `test_sqlite_activity_retains_writer_health`.
- A populated game-findings feed loads tens of thousands of rows or silently omits later findings for one game: `backend/tests/test_tactical_opportunities.py::test_game_findings_pages_preserve_order_and_total` and `tests/browser/real-game-feedback.spec.ts::Games loads every page of pending findings for the selected game`.
- A cached repertoire conflict result survives a changed line: `backend/tests/test_repertoire_conflicts.py::test_conflict_snapshot_cache_invalidates_after_line_edit`.
- A faster cold conflict scan merges legal en-passant positions or changes the reported EPD: `backend/tests/test_repertoire_conflicts.py::test_conflict_fast_position_key_preserves_en_passant_distinction`.
- A discovery read or continuation recommendation reuses parsed moves after a repertoire line changes: `backend/tests/test_repertoire_opportunities.py::test_discovery_read_cache_rebuilds_after_repertoire_line_change` and `test_discovery_recommendation_cache_rebuilds_after_line_change`.
- An inactive discovery preview retries and reports an expected 404, another discovery stalls, or a late removed or older preview replaces current evidence: `tests/browser/discovery-viewer.spec.ts::inactive discovery preview refreshes once without a retry notification and another discovery loads`, `late obsolete preview cannot replace a newer evidence version`, and `late removed preview cannot be reused when the discovery returns`. Unrelated HTTP, invalid-schema, and network preview failures remain visible in `genuine discovery preview ... remains observable`.
- An unchanged active discovery can remain hidden after an expected inactive-preview 404: `tests/browser/discovery-viewer.spec.ts::same-key inactive preview recovers after the retry delay without an error notification` and `repeated same-key inactive 404 previews stay bounded and silent` protect ready recovery, bounded requests, and silent expected 404s.
- Refreshing a review session after its active discovery disappears can select an unreviewable first feed item and strand the remaining item: `tests/browser/discovery-viewer.spec.ts::refresh selects the remaining review item when the first feed item is waiting`.
- Discoveries offers direct training for a prefix card whose target decision cannot be isolated, or eligibility and command diverge: `backend/tests/test_repertoire_opportunities.py::test_discovery_training_eligibility_rejects_earlier_prefix_target_without_writes` and `test_discovery_training_eligibility_accepts_supported_target`; `tests/browser/discovery-viewer.spec.ts::unsupported saved discovery explains Builder route without sending train`, `supported saved discovery checks eligibility before direct training`, and `genuine training eligibility failure remains visible and blocks direct training`; `tests/unit/validated-data-regressions.test.ts::discovery training eligibility requires an explanation when direct training is unavailable`.
- A successful unchanged feed poll can clear the only Retry button while leaving a cached eligibility 503: `tests/browser/discovery-viewer.spec.ts::eligibility recovers after an unchanged feed poll through an item retry` protects recovery without a reload and keeps Train disabled until the server confirms eligibility.
- A faster discovery acceptance index merges positions with and without a legal en-passant capture: `backend/tests/test_repertoire_opportunities.py::test_discovery_accepted_moves_keep_en_passant_positions_separate`.
- PostgreSQL tactical insight aggregation loses the overall totals or pin categories while avoiding a full opportunity scan: `backend/tests/test_postgres_tactical_statistics.py::test_postgres_tactical_summary_keeps_pin_breakdown_without_loading_all_motifs`.
- A PostgreSQL queue refresh scans all eligible opening cards in one 50 ms read and stalls new imports: `backend/tests/test_postgres_opening_candidate_pages.py::test_postgres_opening_candidates_read_small_pages_before_planning`.
- A PostgreSQL GET route fails under the reader role: `scripts/audit_postgres_reads.py` exercises all 58 registered GET routes against a restored copy and rejects unexpected 5xx responses.

## PostgreSQL-first test gate consolidation

- Browser/Node host UTC advances a day before the disposable API's New York clock, causing phone study submissions to fail with an expired prepared queue: `PostgreSQL browser and disposable API share the same day across the UTC midnight boundary` in `tests/unit/postgres-test-runner-regressions.test.ts` starts from UTC and checks actual Playwright and Node fixture clocks against every disposable API/worker timezone and the observed midnight boundary. Reproduced an undefined browser timezone and then the remaining Node UTC clock before pinning both; real `studies.spec.ts` and `phone-offline-training.spec.ts` retain the authoring/offline/replay workflow proofs.

- A slow initial Builder load makes the rerender performance regression attribute initial session persistence to an unrelated render: `Builder unrelated rerender does not rewrite repertoire selection or session` in `tests/unit/builder-performance-regressions.test.tsx` waits for the initial saved selection and session before measuring the unrelated render.
- A durability PGN imports contradictory White responses or a player-turn endpoint and fails integrity before study: `valid opponent-branch durability and background fixtures prescribe one White response per position` in `tests/runner/postgres-test-speedups.test.mjs` and `test_opponent_branch_lines_share_one_trained_response_without_integrity_conflict` in `backend/tests/test_repertoire_integrity.py`; `make docker-durability` verifies the same fixture reaches `/api/queue/today` through normal publication.
- Browser queue entries or reused guided/split cards invalidate the later durability scenario: `full browser coverage creates a fresh durability database before study commands` in `tests/runner/postgres-test-speedups.test.mjs`; `make full` checks the fresh queue and then verifies distinct review, guided-failure, and prefix-split cards.
- A completed invalid durability fixture waits for an impossible queue admission: `completed invalid study fixtures fail immediately instead of polling for impossible admission` in `tests/runner/postgres-test-speedups.test.mjs`; runner failure diagnostics include repertoire-scoped integrity, cards, queue, priority, graph tasks, projection, and operation receipts.
- PostgreSQL foreground study actions, queued background publication, restart persistence, or confirmed command identity regress: `PostgreSQL study state, queue order, guided failure, and command identity survive service recreation` in `scripts/test-postgres-docker.mjs`.
- The full gate executes regular Playwright twice or omits the PostgreSQL runner: `full verification owns every test family once without repeating regular browser specs` in `tests/unit/test-plan-regressions.test.ts`.
- A browser focus value is split, ignored, inherited from the product environment, or accepted when empty/unknown: `PostgreSQL runner forwards a focused spec as discrete arguments` and `PostgreSQL runner rejects empty, unknown, conflicting, and inherited filters` in `tests/unit/postgres-test-runner-regressions.test.ts`.
- A runner inherits product database, broker, or test endpoint settings: `test environment strips dangerous product database and broker settings before any runner work` in `tests/unit/postgres-test-runner-regressions.test.ts`.

The retired full SQLite runner's default assertions map to the PostgreSQL-backed regular browser matrix and service recreation scenario. Full SQLite runtime/browser specifics remain available through `make legacy-sqlite`; focused SQLite snapshot, validation, import-fidelity, source-nonmutation, destination-safeguard, historical-schema, product regressions, and `coverage_path_floor` checks remain in default unit/backend stages.

## Issue #31 — held-piece drag diagnostics

Opt-in bounded instrumentation measures a continuously held drag rather than inferring smoothness from its final square. Production board, timing, and engine policies remain unchanged.

- `tests/unit/held-drag-diagnostics-regressions.test.ts`: `held_drag_capture_is_bounded_and_disabled_by_default`, `held_drag_capture_cleans_up_on_visibility_cancel_capture_loss_and_unmount`, `held_drag_metrics_account_for_grab_offset_orientation_and_coalescing`, `held_drag_detector_reports_mid_drag_board_cancellation`, `held_drag_reset_reuses_capture_without_pointer_storage_network_or_layout_work`, `held_drag_frame_cadence_uses_callback_receipt_not_nominal_rAF_timestamp`, `held_drag_long_tasks_and_operation_correlations_are_bounded`, `held_drag_snapshot_metadata_cannot_change_live_capture_limits`.
- `tests/browser/held-drag.spec.ts`: `held_drag_detector_reports_mid_drag_board_cancellation` validates uninterrupted and injected-interruption controls; `held_drag_status_and_preview_scenario_observes_continuity_before_drop` compares the independent probe with diagnostics while status and preview responses arrive; `held_drag_training_phases_separate_drop_reply_review_and_readiness` checks separately correlated training phase completions.
- `tests/unit/performance-report-regressions.test.ts`: `held_drag_reports_reject_stale_or_incomparable_baselines`, `held_drag_reports_flag_new_interruptions_without_inventing_percentages`.
- Pinned `tests/browser/performance.spec.ts`: `held-piece drag baseline separates workloads and capture overhead` retains three cold/warm repetitions for five isolated workloads, with capture enabled and disabled. Timing distributions are advisory; deterministic behavioral checks remain required.

PR #47 diagnostic phase corrections: `tests/unit/held-drag-diagnostics-regressions.test.ts` covers `held_drag_phases_preserve_operations_started_before_the_first_hold`, `held_drag_phases_correlate_successive_holds_and_survive_session_eviction`, `held_drag_phase_completion_is_idempotent_and_preserves_errors_and_post_drop_work`, `held_drag_phase_reset_and_disable_invalidate_late_callbacks`, and `held_drag_active_operations_are_bounded_and_snapshot_tracking_is_isolated`.

PR #47 opponent-reply cancellation: `tests/unit/opponent-reply-diagnostics-regressions.test.tsx` mounts Home with fake timers and the real recorder. `opponent_reply_reset_closes_its_diagnostic_phase` and `opponent_reply_home_unmount_closes_its_diagnostic_phase` verify that the existing cancellation paths record one error edge and do not seed canceled operations into a subsequent drag. `opponent_reply_repeated_cancellation_preserves_registry_capacity` exercises 129 schedule/reset cycles and verifies a fresh operation can still be recorded. `opponent_reply_completion_and_stale_callbacks_preserve_newer_ownership` protects the 420 ms reply delay, normal completion, and newer reply ownership from an old callback. `opponent_reply_handled_failure_closes_its_diagnostic_phase` covers illegal-reply completion and subsequent cleanup. All cases retain an unrelated active operation without resetting the registry during the scenario.

PR #47 manifest selection: `tests/unit/performance-report-regressions.test.ts::held_drag_reports_select_the_newest_valid_manifest_and_prefer_full_on_ties` and `tests/unit/visual-runner-regressions.test.ts::held_drag_nested_performance_manifest_preserves_the_full_run_timestamp` protect standalone/full ordering, ties, invalid timestamps, and nested run identity.

PR #47 unrelated CI readiness repair: `tests/browser/studies.spec.ts::FEN-only study square exercise is authored enrolled and reviewed through the real workspace` waits for its exact exercise FEN before capturing the no-move invariant. Historical quality run 36785256539 (job 110125016293) failed because it captured the shared shell's initial FEN; retained trace snapshots show the exercise FEN arrived during coordinate filling, before Add. The equality assertion after Add remains immediate and unchanged in strength.
- A successful negative Discoveries training-eligibility response stays cached after the saved card becomes eligible without changing discovery identity: `negative discovery eligibility recovers through an explicit recheck after an unchanged feed refresh` in `tests/unit/discoveries-tray-regressions.test.tsx` verifies the unchanged feed keeps training disabled, manual recheck waits for a validated response, and Train revalidates before the command. `failed negative eligibility recheck remains observable and retries before enabling training` verifies request-error recovery through the same action.

- The PostgreSQL background-workload benchmark races the real defense engine's Celery claim for its synthetic threat request: `background workload isolates consumers and restores them after success`, `background workload isolates consumers and restores them after failure`, `background workload restores partially stopped consumers without masking the isolation failure`, and `background workload preserves benchmark and consumer restoration failures` in `tests/runner/postgres-test-speedups.test.mjs` cover the benchmark's consumer lifecycle in the regular unit gate. `scripts/check_postgres_background_workloads.py` independently verifies through fresh connections that the baseline timeout/rollback preserves the queued row and eligibility relationship, and that the second claim commits exactly one matching lease. Missing or wrong claims report row/control/relationship state rather than failing with a `TypeError`.

- The Study square-selection browser regression records the shell's initial FEN before the study runner publishes the authored position: `FEN-only study square exercise is authored enrolled and reviewed through the real workspace` in `tests/browser/studies.spec.ts` requires the authored FEN before capturing the baseline and retains the immediate unchanged-position assertion after Add. The CI trace showed the authored FEN arriving before Add, independently of answer selection.

- An eligibility request from an earlier feed appearance blocks a returned discovery or authorizes it with obsolete state: `returned discovery starts fresh eligibility before obsolete request settles` and `obsolete eligibility settlement cannot overwrite replacement success` in `tests/unit/discoveries-tray-regressions.test.tsx` exercise both settlement orders with stale positive, negative, HTTP, malformed JSON/schema, and network outcomes. `obsolete eligibility cleanup preserves replacement request ownership` rejects duplicate requests after an old finally; `eligibility identity change away and back invalidates earlier requests` covers fingerprint/card-ID generations. `obsolete eligibility body decoding cannot publish validation diagnostics` covers schema/HTTP failures after removal during decoding. `obsolete command-time eligibility cannot cache authorization or submit Train` covers obsolete success/HTTP/network outcomes; `current eligibility failure remains observable and retryable` preserves genuine failures and recovery. Existing manual Recheck, Retry, and pre-command validation regressions remain in the same regular suite.


## Adaptive opening segmentation (four-phase delivery)

PR 1 preserves whole-card scheduling and presents advisory structural estimates.
The existing fixed-prefix/cue-identity assertions are unchanged.

| Acceptance | Named coverage / delivery state |
| --- | --- |
| AS-01 | `test_shared_trunk_preserves_coverage_with_20_tests_instead_of_48`; `test_legal_early_branch_retains_opponent_cue_and_ends_on_learner_move`; `AS-01 preview explains 48-to-20 savings and has no Apply action`; real PostgreSQL/browser preview. Actual adaptive queue coverage waits for PR 3. |
| AS-02 | PR 3/4 pending: known not-due trunks omitted from tested decisions. |
| AS-03 | `test_compatible_transposition_shares_suffix_but_preserves_incoming_bridges`; `test_transposed_presentations_share_decision_identity_with_separate_provenance`; PostgreSQL shared decision summaries. Adaptive scheduling sharing remains PR 3. |
| AS-04 | `test_decision_identity_preserves_policy_color_turn_castling_and_legal_en_passant`. |
| AS-05 | `test_duplicate_routes_and_existing_shared_cards_do_not_inflate_support`. |
| AS-06 | `test_black_custom_root_and_opponent_start_count_actual_learner_decisions`. |
| AS-07 | `test_cycle_preserves_bounded_occurrences_without_walk_enumeration`. |
| AS-08 | `test_three_clean_first_responses_are_three_observations`; `test_shadow_six_decision_failure_preserves_clean_predecessors_and_unreached_successor`; `AS-08 appends before advancing without awaiting deferred local persistence`; browser `AS-08 deferred evidence persistence leaves rendered moves and aggregate review responsive`; real PostgreSQL retained partial attempt. |
| AS-09 | `test_assistance_before_response_stays_assisted_and_later_assistance_does_not_rewrite_recall`; `test_manual_again_has_no_fabricated_first_response_and_correction_is_not_clean`; journal category/deduplication regression; browser `AS-09 actual teaching arrows manual Again and revealed correction retain first response context`. |
| AS-10 | `test_postgres_shadow_replay_atomicity_and_scheduling_invariance` (regular durability runner): concurrent exact replay, conflicting events/context/terminal seals and combined-review rollback. PR 3 scheduling ownership cutover remains pending. |
| AS-11 | PostgreSQL rehearsal retains two genuine same-day attempts and two distinct clean study days; `AS-11 queue refresh preserves logical identity while restart and reinforcement replace it`; offline browser retains independent parent/repeat IDs. |
| AS-12 | PR 3 pending: partial exercise cannot complete longer card. |
| AS-13 | Stable decision identity is already independent of presentation/generation; adaptive history preservation pending PR 3/4. |
| AS-14 | `test_superseded_segmentation_generation_cannot_publish`; `AS-14 keep-current sends observed versions and only confirmed metadata removes advice`; stale preference rejection in disposable PostgreSQL. Executable apply pending PR 3. |
| AS-15 | PostgreSQL rehearsal accepts out-of-order terminal gaps and late historical replay without replacing newer facts; browser exact ambiguous checkpoint retry and orphan recovery regressions; `AS-15 restart retains an uncommitted partial journal for storage recovery`; `AS-15 denied IndexedDB open can retry after access is restored without reloading unsaved work`; journal storage-gap regression. Adaptive ownership replay remains PR 3. |
| AS-16 | `test_legacy_review_dispatch_preserves_exact_payload_and_command_fingerprint`; optional invalid manifest compatibility; legacy outbox tests; browser full offline capture/repeat/parent reconciliation; PostgreSQL competing phone history-only review; `test_offline_repeat_reconciles_parent_aggregate_only_fallback`; parameterized `AS-16 immediate/deferred opening_evidence_conflict/opening_evidence_unavailable archives rejected journal before confirmed aggregate-only save`; browser parent fallback retains diagnostics after queue refresh; definitive rejection aggregate-only fallback and ambiguous original-key retry. Ownership cutover remains PR 3. |
| AS-17 | PR 3/4 pending: active adaptive plan rebuild/invalidation. PR 1 never modifies active attempts. |
| AS-18 | PR 3 pending: shared-target deduplication with quota/prerequisite/fairness protection. |
| AS-19 | Existing PR 1 advisory invariance tests retained. `test_postgres_shadow_replay_atomicity_and_scheduling_invariance` compares five evidence-enabled/absent reviews under identical fixtures/clocks: FSRS/card state, due dates, intervals, queue/requeue/admission fields, locked descendants and prefix-split previews. Checkpoints leave scheduling tables unchanged. |
| AS-20 | PR 3 activation/opt-out remains pending. Additive migration 30 seeds actual saved revisions without inferred observations; regular PostgreSQL upgrade, service recreation and every-table backup/restore retain shadow provenance and summaries. |

### Opening-evidence command error transport (PR #69)

- `test_command_receipt_preserves_structured_http_detail_through_immediate_dispatch` failed for both evidence conflict/unavailable codes before the repair. It exercises the actual gateway serializer, receipt reader, dispatcher and HTTP response. `test_deferred_command_failure_keeps_legacy_message_and_structured_detail` protects delayed delivery; JSON-native empty/null values, legacy HTTP receipts, generic failures and the 16 KiB detail bound have named cases in the same regular backend file.
- `test_postgres_command_receipt_preserves_http_detail_and_failed_handler_rollback` runs in regular PostgreSQL operation-recovery durability. Both codes and delivery timings retain the persisted envelope, roll back handler writes and replay the immutable failed receipt.
- Parameterized `AS-16 immediate/deferred opening_evidence_conflict/opening_evidence_unavailable retains diagnostics before aggregate-only delivery` waits for archival before fallback and asserts unchanged inputs, omitted completion and distinct delivery key. The corresponding outbox cases assert durable rejection archives exist before the second POST and confirmed saves clear pending reviews, including unavailable IndexedDB.
- `AS-16 legacy deferred failed receipt still delivers the aggregate-only review`, unrelated immediate/deferred 409/422/500 cases, and denied immediate fallback permission preserve compatibility and error boundaries. `AS-15 pending evidence receipt retries the original payload and key without fallback` and the existing ambiguous delivery regression retain immutable retry identity.
- Rejected diagnostic storage must not block authoritative review delivery: parameterized `AS-16 immediate/deferred opening_evidence_conflict/opening_evidence_unavailable diagnostic archive quota failure still saves the aggregate review` failed on the reviewed PR head before the repair. A real `Storage.prototype.setItem` quota failure permits the required primary marker, then verifies unchanged aggregate fields, omitted completion, the `:aggregate-only` key and confirmed outbox drain with IndexedDB unavailable. `AS-16 immediate/deferred opening_evidence_conflict/opening_evidence_unavailable primary rejection marker failure prevents fallback and preserves retry` keeps primary persistence mandatory, stops the second POST and retries the original frozen delivery after storage recovers.
- `AS-16 diagnostic archive denied read/malformed data cannot block aggregate-only delivery` and `AS-16 diagnostic warning failure cannot block aggregate-only delivery` keep optional diagnostics best effort. `AS-16 failed diagnostic archive retains aggregate-only identity across uncertain save retries` retains the persisted rejection marker and frozen fallback payload/key until confirmation, without repeating the archive write or keyed warning. Successful archive preservation and unrelated/ambiguous evidence rejection boundaries remain covered by the existing regular tests.
- CI fixture boundaries: `repair retries survive delayed operation and task transitions after reload without another source edit` waits for the acknowledged retry's persisted delivery marker before reload, rather than cancelling its POST on an optimistic label. `FEN-only study square exercise is authored enrolled and reviewed through the real workspace` pins the browser date to the disposable server queue day and waits for committed current-day preparation; the prior fixture mismatched days after UTC midnight. Both retain the full workflow assertions and zero automatic retries.
| AS-21 | `test_segmentation_analysis_yields_restarts_and_replays_idempotently`; `test_group_preparation_reads_only_eight_indexed_occurrences_and_closes_connection`; `scripts/check_postgres_opening_segmentation.py` proves real foreground FSRS review while traversal is paused, durable cursors, stale lease replay, 50ms background sections and zero traversal on cached reads. |
| AS-22 | `test_no_savings_or_insufficient_support_does_not_claim_improvement`; preview-only UI labels. Learner adaptation pending PR 4. |

New pure tests: `backend/tests/test_opening_segmentation.py`.
Worker tests: `backend/tests/test_postgres_opening_segmentation.py`.
Client contracts: `tests/unit/opening-segmentation-regressions.test.tsx`.
Real product browser: `tests/browser/opening-segmentation.spec.ts` (390px and 1280px).
PostgreSQL rehearsal runs through the regular durability/full runner's isolated
background workload stage; it never uses the live study database.

PR #50 snapshot repair: `tests/unit/opening-segmentation-regressions.test.tsx` covers normal bound pagination, rebuilds between segment/route pages, stale/building/failed lists, removed/changed selections, late list/detail/command responses and preference idempotency. `backend/tests/test_opening_segmentation_snapshot_api.py` covers missing and superseded pagination bindings, all snapshot identity components and preference mismatch without writes. `scripts/check_postgres_opening_segmentation.py::test_preview_response_remains_consistent_during_concurrent_postgres_publication` changes the publication and route names during a real read; the response must retain the original complete snapshot.

PR #50 discovery synchronization: `late removed preview cannot be reused when the discovery returns` retains its two-ready-discoveries and replacement-request assertions. The next polling interval waits for the previous response to finish and for the tray's feed count to reflect removal/return. CI run 36871513621 trace shows the second clock jump overlapping response 2; unchanged local `26bbf2a` passed the exact case (1 case, 1.9s), so the trace, rather than a local repeated failure, is the root-cause evidence. No sleeps, timeouts or preview validity assertions were relaxed.

PR #50 empty-cursor boundary: `test_pagination_requires_snapshot_and_rejects_republication` also sends explicitly empty segment/route cursors; naming a pagination cursor requires a snapshot even when its value is empty. Initial requests without cursor parameters retain compatibility.

PR #50 durable preference compatibility: `test_preference_dispatch_preserves_legacy_receipt_payload_and_new_snapshot` proves an old request/key reaches the gateway with its exact original payload (no newly inserted nullable snapshot field), while new requests preserve their snapshot. The legacy case failed before excluding absent optional fields from dispatch serialization; a null field would otherwise change the receipt fingerprint and reject an uncertain retry.

PR #50 current-main migration reconciliation: `test_segmentation_migration_has_unique_number_and_matches_schema_readiness` rejects duplicate migration numbers, readiness drift and a segmentation filename/recorded-version mismatch. Main added migrations 21–23 while this unpublished preview migration still used 21; integration run 36897266117 failed readiness (`24 != 23`). The new regression reproduced the duplicate number locally. Segmentation now uses migration 24; existing startup-readiness and disposable upgrade/restore checks remain required.

## CI reliability maintenance

CI reliability maintenance: `tests/runner/ci-reliability.test.mjs` (invoked by `tests/unit/ci-reliability-regressions.test.ts`) covers `core and required integration failures block quality`, `missing cancelled and unexpectedly skipped required work cannot pass quality`, `absent required command and selected test results cannot pass quality`, `explicit inapplicability is accepted and reported but unexpected skips fail`, `quarantined harness failures remain visible with required replacement coverage`, `rename deletion unknown paths and missing history select conservative coverage`, `new unclassified specs fail planning and every test has nightly and release coverage`, `leaf source selection includes complete families and rendering selects pinned checks`, `split complete coverage preserves every PostgreSQL product scenario`, `deployment requires complete verification and scheduled or verification-only runs cannot deploy`, `failed-layer rerun leaves successful unrelated jobs intact and diagnostic retry cannot green the gate`, `actual browser collection grep selects exactly the planned tests`, and `diagnostic artifact failure still cleans owned resources and preserves both failures`. `split complete verification matches the existing full command inventory` in `tests/unit/test-plan-regressions.test.ts` protects the decomposition boundary. The unresolved initial phone/Study visibility signature remains required coverage; no active quarantine or automatic retry is installed.

CI decomposition also retains the full runner's no-skip/no-exclusive guard: `CI and local runners preserve rejection of exclusive skipped and unfinished regressions` applies the shared guard to controlled fixture files and checks every entry point. A quarantined test still runs; no test is marked skipped to establish quarantine.

CI collected-file boundary: `collected browser filenames cannot bypass inventory through extensions or nested paths` fails the original selector. Validation now includes filenames returned by actual collection, not just root `.spec.ts` directory entries, and preserves relative paths. New `.test.ts`, `.spec.tsx`, or nested specs cannot silently remain unselected or inherit another file's basename classification.

CI collection also covers `complete browser verification is unfiltered and tagged collection preserves selection`: complete browser work uses no focus filter, and partial selection accounts for tags reported by Playwright, including tags on enclosing suites. The executed-ID comparison remains authoritative.

Current-main integration: `current-main integration retains CI and segmentation regression registrations` checks every native CI harness test name, the full-command inventory test, segmentation structural/snapshot/preference/migration registrations and AS-01–AS-22 mappings. Merge `2f5521d` preserved executable coverage but replaced all four CI registration paragraphs with PR #50 entries; the new regression failed on that merged registry before restoring the CI entries additively. Both coverage groups remain required.

## Tactic capture

Backend capture regressions in `backend/tests/test_tactic_capture.py` cover:

- `test_captured_tactic_create_queues_valid_tactic_today`
- `test_captured_tactic_create_is_idempotent`
- `test_captured_tactic_reuses_existing_tactic_without_erasing_history`
- `test_captured_tactic_rejects_invalid_fen_and_illegal_solution`
- `test_captured_tactics_consume_remaining_daily_tactic_introductions`
- `test_capture_after_daily_tactic_allowance_still_queues_without_evicting_existing_tactics`
- `test_recapturing_existing_tactic_does_not_consume_new_tactic_allowance`
- `test_capture_reopens_finished_queue_even_with_zero_automatic_allowance`
- `test_capture_conflict_does_not_relabel_or_resurrect_card`
- `test_capture_places_after_four_cards_and_replay_does_not_move_it`
- `test_game_tactic_migration_normalizes_only_known_plural_rows`
- `test_capture_accepts_black_castling_en_passant_and_underpromotion`
- `test_capture_api_uses_foreground_receipt_and_rejects_mismatched_key`
- `test_game_tactic_import_repair_verifies_original_before_recorded_destination_changes`
- `test_game_tactic_import_repair_never_mutates_after_failed_exact_verification`

`test_game_tactical_miss_uses_shared_tactic_capture_path` in
`backend/tests/test_tactical_opportunities.py` first failed against the plural
`tactics` defect, then proves canonical cards, game ownership, provenance,
accepted status, guided placement, and replay.

`tests/unit/tactic-capture-regressions.test.tsx` covers empty/incomplete setup,
free dragging, invalid FEN rendering, legal line replacement, confirmation,
underpromotion and FEN state, minimal UCI requests, invalid-save prevention,
queue invalidation, durable retry identity/bytes, reopened locked edits,
terminal failure/new identity, completed receipt replay, and accurate source
links. The named `Python tactic capture result satisfies the frontend durable
capture contract` producer/consumer test runs in the ordinary unit gate.
Existing CardEditor repair, revision history, prefix split, and Tactics guided
attempt tests remain in the regular gate.

Real-board `FEN capture records a real-board solution and is reviewed through
ordinary Training` and `manual capture places and removes pieces and freely
drags an incomplete setup on the real board` run in `tactic-capture.spec.ts`,
registered in the training family. `capture modal keeps incomplete setup
draggable and restores focus across browser engines` covers Chromium, Firefox,
and WebKit. Pinned `Capture tactic dialog 390` and `Capture tactic dialog 1280`
cover responsive rendering.

The regular durability gate runs `scripts/check_postgres_tactic_capture.py`:
real capture/automatic admission contention, publication-time quota recheck,
connection restart and unchanged receipt replay, atomic invalid/collision
rejections, and completed-queue reopening at zero allowance. The populated
upgrade rehearsal verifies narrow plural normalization while preserving
identifiers, reviews, archival state, and interval history. Backup comparison
includes every table, including capture events and operation receipts.

`editor mode transition refreshes geometry after the setup palette moves the
board` protects Chessground hit testing when switching setup/solution mode. It
failed before the geometry refresh; the real FEN and manual browser cases also
failed to record a move before that fix. Held-drag preservation remains covered
by the affected unit file and existing cross-browser board workflow.

`capture retries an unknown or unavailable receipt with the saved body and
retains identity through 202` covers lost delivery and SQLite compatibility;
`card repair offers Lichess restoration only for explicitly packaged tactics`
protects the ownership boundary.

`capture remains available while tactics load or fail and explains durable capture
in the demo`, `capture remains available from Packs`, and `capture remains
available after a tactic pack is complete` protect access across workspace states
in `tests/unit/shared-board-shell-tactics-regressions.test.tsx`.

`test_postgres_route_contract_matches_registered_endpoints` reproduced the
missing capture route declaration from the first PR CI run. The route contract
now classifies `POST /api/tactics/captures` as a staged foreground command;
`test_staged_postgres_mutations_pass_the_runtime_write_guard` also covers it.

Browser fixture isolation: the existing `FEN-only study square exercise is
authored enrolled and reviewed through the real workspace` now also asserts
that product fixtures start with no active automatic packs. Running it after
`tactical catalog groups and activation controls remain reachable on phones`
reproduced leaked activation before the fix. Product setup confirms pack
deactivation before archiving cards; the source-neutral active-card quota can
therefore never replenish another test's cards. Enrollment, real mixed Training,
authored FEN, unchanged board during square selection, assessment and export
assertions remain required. No test identity, CI selection, or timeout changed.

## Per-repertoire daily new-card overrides

- `backend/tests/test_repertoire_settings.py::test_repertoire_daily_overrides_apply_independently_and_reset_to_default` — independent 10/5 limits and resetting to the default.
- `test_global_daily_limit_changes_only_inheriting_repertoires` — global changes preserve explicit overrides.
- `test_repertoire_limit_changes_today_preserve_completed_work_and_due_reviews` — lowering today's limit or setting zero removes only remaining automatic introductions.
- `test_seven_of_ten_learned_allows_ten_new_tomorrow_without_rollover` — exactly seven learned out of ten produces ten new cards tomorrow, with repeat refreshes remaining idempotent. Existing `test_unfinished_unreviewed_cards_do_not_bypass_tomorrows_new_card_limit` remains in the regular suite.
- `test_shared_card_review_retries_do_not_consume_owners_new_card_allowance` — a reviewed shared card's retry never spends the owner repertoire's separate introduction allowance (reproduced failing before the fix).
- `test_shared_card_is_deduplicated_and_charged_to_admitting_repertoire` — shared cards are introduced once and charged to the admitting repertoire.
- `test_repertoire_override_rejects_invalid_payloads`; `test_repertoire_override_rejects_missing_and_system_repertoires`; `test_existing_repertoire_migrates_to_inherited_limit` — validated API boundaries and historical SQLite compatibility.
- `test_postgres_opening_publication_rechecks_lowered_limit_and_current_admissions`; `test_postgres_repertoire_settings_write_invalidates_old_queue_checkpoint`; `test_postgres_repertoire_settings_endpoint_dispatches_durable_command` — publication rechecks, generation invalidation, restart, and durable routing. Portable SQL fixtures prove behavior, not real PostgreSQL semantics.
- `tests/unit/repertoire-daily-limits.test.tsx` — zero/reset controls, validation, failed loads, demo availability, pending save locking, lost transport, reload, exact identity/body replay, and completed receipt validation.
- `tests/unit/api-schema-parity-regressions.test.ts::Python repertoire limit response matches the frontend contract for inherited, zero, and custom limits` — producer/consumer compatibility.
- `tests/browser/settings-repertoire-limits.spec.ts::repertoire limits update today's queue, persist after reload, and reset to default` — real Settings/queue workflow with save failure recovery; registered in the complete repertoire browser family.
- `scripts/check_postgres_repertoire_limits.py` — real PostgreSQL 10/5 limits, seven-of-ten reset, zero reconciliation, stale plan rejection, inheritance, and operation replay in the regular durability runner. The runner's command-recreation scenario preserves and replays an override across container recreation; schema-upgrade coverage verifies existing repertoires inherit after migration 29.

- `backend/tests/test_postgres_route_contract.py::test_postgres_route_contract_matches_registered_endpoints` — repertoire settings is registered as a staged foreground command; reproduced the missing route before adding the contract entry.
- `backend/tests/test_repertoire_settings.py::test_repertoire_override_rejects_missing_and_system_repertoires` — a persisted `__defense__` row rejects opening-limit writes and is absent from repertoire listing while normal openings remain. Reproduced HTTP 200 before the fix.
- `backend/tests/test_repertoire_settings.py::test_shared_card_integrity_change_does_not_refund_admitting_repertoire_allowance` — reviewed and unreviewed A-owned cards admitted under B retain B’s consumed allowance after A is blocked. Covers SQLite reseeding/legacy reconciliation and PostgreSQL planning/reviewed-count SQL; reproduced extra planned admission before the fix.
- `tests/unit/repertoire-daily-limits.test.tsx::inherited custom input uses the current default and preserves an edited draft` — a 10→12 global default change initializes Custom to 12, while an edited 5 survives mode toggles and later global changes. Reproduced stale 10 before the fix.
- `tests/unit/repertoire-daily-limits.test.tsx::inherited repertoire uses API effective limit when page default is stale` — an inherited API value of 12 wins over a stale page default of 10 for current status, Custom initialization and the exact saved request. Reproduced displaying 10 before consuming the backend effective value.
- `tests/unit/repertoire-daily-limits.test.tsx::reset to inheritance uses effective limit returned by save` — a reset response with inherited effective 12 updates the row immediately and clears the previously saved custom 5 as a draft. Reproduced displaying 10 after reset.
- `tests/unit/repertoire-daily-limits.test.tsx::refreshed custom override replaces a clean row without becoming an unsaved draft`; `newer repertoire refresh ignores older in-flight data` — reused rows consume newly confirmed backend values; older requests cannot overwrite a newer response. The page default remains a global preview for custom repertoires. Both failed before the refresh/state repair.
- `tests/unit/repertoire-daily-limits.test.tsx::repertoire editing requires backend %s instead of guessing a default` — missing override/effective fields produce the explicit upgrade error; the shared listing schema remains optional for compatibility with other consumers. The missing-effective case reproduced silently displaying the page default.
- The existing `inherited custom input uses the current default and preserves an edited draft` and `repertoire pending saves retain exact bytes and identity through lost transport and reload` now refresh backend values independently of the page default. Unsaved custom 5 and locked pending bytes/identity survive; confirmed status follows the backend. Both extended cases failed on the stale frontend before the fix.

### PR #54 durability proof isolation

Current-main integration also preserves `background_metric_buckets` for the
`daily_queue` kind in both restoration regressions below. Before restoring those
rows, all four present/absent cases failed with leaked diagnostic counters.
`test_repertoire_limit_migration_has_unique_number_and_matches_schema_readiness`
failed on duplicate migration 26 when merging background diagnostics and again on
duplicate 27 when handled discoveries introduced migrations 27 and 28. The
repertoire migration now uses 29, with a matching ledger and readiness version.

`backend/tests/test_tactical_catalog.py::test_tactical_pack_migration_preserves_reviews_scheduling_and_completion`
also protects the additive repertoire schema: the old five-value positional insert
failed against the new six-column table. Fixtures now name their columns rather
than appending NULL. The repository audit found six positional repertoire inserts,
all in tests; all six now use explicit lists, preserving the intentional miniature
and historical schemas in the other fixtures.

`backend/tests/test_repertoire_limit_proof_isolation.py` protects the shared
disposable environment. Portable SQL covers restoration; the existing Docker
proof retains real production commands, receipt replay, PostgreSQL locks and
generation invalidation.

- `test_repertoire_limit_proof_finally_restores_queue_state_after_assertion_failure` — existing and absent singleton cases; both failed before the fix with leaked task, event and projection rows. Also verifies repertoire/card/link/review/queue/receipt cleanup and unrelated rows.
- `test_repertoire_limit_proof_restores_pruned_history_both_dates_and_stale_cards` — exact task fields/ID, all 100 historical events including events pruned by enqueue, present/absent today projections, tomorrow projection, and unrelated rollover fields survive mutation and cleanup. Also restores present/absent priority source epochs for owners and shared links after card-update triggers; the trigger-enabled fixture reproduced that additional leak before epoch restoration.
- `test_repertoire_limit_proof_cleanup_failure_is_loud_and_atomic` — restoration failure surfaces with the original assertion as context and rolls back fixture deletion and partial queue restoration together.
- `test_repertoire_limit_proof_reconciliation_preserves_unrelated_entries` — reconciliation publishes only fixture entries while advancing past unrelated candidates.
- `tests/runner/postgres-test-speedups.test.mjs::repertoire limit recreation fixture includes its final White response` — the PR's recreation PGN ends after White's prescribed move so integrity validation permits study admission. Reproduced `w !== b` on `1. e4 e5 *` after the isolated Docker run exposed `missing_response`; fixed with `2. Nf3`, without bypassing integrity checks or extending waits.
- `tests/runner/postgres-test-speedups.test.mjs::repertoire limit recreation fixture survives backup then leaves unrelated study state intact` — executes the real recreation/backup action bodies with I/O seams, verifies override replay and backup before cleanup, and preserves unrelated fixtures. Reproduced the leftover recreation entry after Docker study durability rejected guided failure with HTTP 409; production deletion now removes that owned repertoire before the next foreground workflow.

## Tactic capture orientation and typed SAN

Capture now faces the accepted starting side and permits typed SAN without changing
its durable UCI command contract. Named regressions in
`tests/unit/tactic-capture-regressions.test.tsx`:

- `capture orients to the accepted starting side and stays fixed through moves and pending FEN changes`
- `capture accepts numbered Black-first SAN lines and saves canonical UCI moves`
- `capture rejects an invalid SAN line atomically and retains text for correction`
- `capture SAN entry replaces the continuation at the cursor and mixes with board moves`
- `capture SAN entry stays locked while saving and awaiting capture confirmation`
- `capture rejects empty, ambiguous, and non-SAN input without changing an existing solution`
- `capture SAN entry refuses invalid or unconfirmed starting positions`
- `capture SAN entry preserves special moves and disambiguation from %s` (castling, en passant, underpromotion, and disambiguation fixtures)

Real-board workflow coverage:
`Black-first capture keeps its orientation while typed SAN and real-board moves save one solution`
in `tests/browser/tactic-capture.spec.ts`.
Cross-browser coverage:
`capture accepts SAN from Black's perspective across browser engines`.
The pinned `Capture tactic dialog 390` and `Capture tactic dialog 1280` cases also
check SAN input visibility and width after switching to Solution.


### PR #53: reject chess.js null moves during SAN entry

Strict chess.js parsing accepts `--`, but a tactic solution must contain a real
move. `playSanSolution` rejects the parsed null move before committing any state.
`capture rejects empty, ambiguous, and non-SAN input without changing an existing solution`
now includes `--` and `e5 --`, asserting false return and unchanged moves, cursor,
and preview FEN. The named component regression
`capture rejects null SAN moves inline and retains typed text without enabling a save`
covers `--` and a valid prefix followed by `--`, proving atomic rejection, retained
input, an inline error, disabled save, and no backend request. Both regressions
failed against the original PR implementation before the guard was added.

## Repertoire-wide canonical prefix

`backend/tests/test_canonical_repertoire_prefix.py` covers:

- `test_italian_prefix_suppresses_sicilian_and_philidor_coverage_nodes` (failed before the scope boundary: opponent nodes included plies 1 and 3; now only 5 and 7).
- `test_canonical_prefix_parses_one_legal_san_sequence_and_rejects_nulls`.
- `test_canonical_prefix_requires_exact_complete_game_history` (incomplete games and alternative initial move orders are excluded).
- `test_canonical_prefix_accepts_matching_stubs_and_verified_continuations`.
- `test_canonical_prefix_durable_preview_resolves_continuations_in_a_later_pass`.
- `test_canonical_prefix_reports_first_conflict_and_disconnected_lines_without_deleting`.
- `test_canonical_prefix_stale_preview_cannot_save_after_a_source_edit`.
- `test_canonical_prefix_preview_rejects_owned_card_changes_without_a_link` (the preview source fingerprint covers owned and shared cards).
- `test_canonical_prefix_rejects_off_scope_additions_and_clearing_restores_analysis`.
- `test_canonical_prefix_preview_yields_to_foreground_restarts_and_replays_idempotently`.
- `test_matching_game_feedback_starts_after_prefix_while_training_routes_remain_intact`.
- `test_canonical_prefix_probabilities_condition_on_assumed_opponent_moves`.
- `test_canonical_prefix_anchored_continuation_retains_downstream_probabilities_and_absolute_horizon` (shortening a prefix restores downstream reach probabilities without changing saved training lines).
- `test_canonical_prefix_black_boundary_preserves_later_position_transpositions`.
- `test_canonical_prefix_revisions_are_independent_and_stale_coverage_is_unknown`.
- `test_canonical_prefix_matching_stub_without_continuation_never_reports_complete`.
- `test_canonical_prefix_shared_card_edit_cannot_escape_any_linked_repertoire`.
- `test_canonical_prefix_stale_recommendation_worker_cannot_publish`.
- `test_canonical_prefix_refresh_preserves_previously_handled_discoveries` (scope refresh retains the handled evidence and accepted work introduced by #55).
- `test_canonical_prefix_game_matches_and_statistics_exclude_sicilian_philidor_and_incomplete_games` (all imported games remain available).
- `test_canonical_prefix_repertoire_api_exposes_only_normalized_typed_metadata`.
- `test_canonical_prefix_discovery_admits_verified_new_gap_routes_and_rejects_stale_queued_work`.
- `test_canonical_prefix_opportunity_publication_locks_repertoire_before_task_to_avoid_foreground_deadlock`.

`test_canonical_prefix_discovery_acceptance_requires_current_scope_revision` in
`backend/tests/test_postgres_discovery_acceptance.py` protects the publication
revision contract for both current and obsolete discoveries.

`tests/unit/canonical-prefix-regressions.test.tsx` covers explicit suggestion
acceptance, conflicts blocking saves, clearing only after explicit save, recovery
of a lost save acknowledgement, retention of a pending operation's identity,
replay of undelivered commands, server-error receipt recovery, reopening an
interrupted preview, and the workspace/coverage transport contracts.

`canonical Italian prefix persists without changing training and rejects Philidor additions`
in `tests/browser/canonical-repertoire-prefix.spec.ts` exercises the real
PostgreSQL workflow at phone and desktop sizes. `Canonical prefix dialog` in
`tests/browser/visual.spec.ts` pins the preview and controls at both sizes.
The workflow waits for the initial durable compatibility check to become ready
before asserting its shared-opening suggestion. Full-matrix CI captured normal
`checking` responses at the earlier immediate assertion; all preview reads were
successful, with no product error or stale result.
Its header placement assertions failed before the scoped close-button layout:
the desktop close button sat outside the dialog and the phone name crowded it.
The regular PostgreSQL durability scenario verifies a preview queued while the
worker is stopped, resumption, unchanged cards/reviews/scheduling, persistence of
verified anchors after service recreation, and replay of the original save
receipt without a second revision.

`test_canonical_prefix_snapshot_copy_preserves_source_revision_and_restores_trigger`
in `backend/tests/test_postgres_import_verification.py` protects exact legacy
snapshot recovery: copy does not advance imported source revisions, and the
trigger is restored within the same transaction. Real PostgreSQL import/verify
and service recreation remain part of the durability gate.

## Contextual keyboard shortcuts

- PR #64 browser navigation ownership: `static preview boards preserve browser navigation defaults before and after activation` and `navigable boards consume browser navigation at the revealed frontier` (`board-shortcuts-regressions.test.tsx`) distinguish explicit history capability from static F/R/help support. The static case failed before the capability guard; the boundary case remains protected.
- PR #64 real-board review fixes: `incorrect study answers retain their live board while Home End and R browse the revealed reference` (`keyboard-context.spec.ts`) checks actual submitted and reference pieces without another attempt. `reported Rc4 defensive preview stays readable and returns to the decision at 390px` and `1440px` (`defense-preview.spec.ts`) now browse the refutation with End, restore through Continue/N, and play a legal defense with the same hinted recognition attempt.
- PR #64 defense transition: `continuing to defense resets browsed refutation history and preserves recognition via %s` (`defense-recognition-regressions.test.tsx`) covers Continue and N returning from End to the original legal decision, preserving the hinted recognition and its attempt ID, without submitting another result. Both transitions failed at the live FEN assertion before the shared reset handler.
- PR #64 off-line study answer: `study feedback browses the reference line and R restores an off-line submitted answer (shared board: %s)` (`study-attempt-pending-regressions.test.tsx`) submits an incorrect legal line through StudyExerciseRunner, then verifies Home/End/R and frontier navigation preserve its answer, hint and single grading request on both embedded and shared boards. Both cases failed at End before the reusable history fix.
- Revealed navigation and attempt-preserving reset: `navigation never crosses the revealed frontier of an unanswered training card`, `R restores a custom-FEN live decision while history remains read-only and attempts advance independently` (`board-shortcuts-regressions.test.tsx`); `tactics shortcuts stop before the unanswered solution and restore the live attempt`; `endgame shortcuts browse only actual played moves without probing or grading again`; `study shortcuts preserve submitted moves and hints without revealing or submitting an unanswered line`; `defensive shortcuts preserve recognition selections and hint consequences without uncovering an answer`.
- Ownership and event protection: `only the last active visible board handles keys and popup boards fence the background`, `typing widgets modifiers IME and handled events preserve their keyboard behavior`, `Hint and Next reuse only enabled actions and holding a letter does not repeat it`, `letter-shortcut preference persists while arrows Escape and contextual help remain active`, `letter shortcuts can be disabled immediately when browser storage writes fail` (`board-shortcuts-regressions.test.tsx`).
- Input fencing: `R restores an applied same-FEN move and rejects its delayed callback without restarting` (`board-authoritative-restoration-regressions.test.tsx`), plus the existing drag, authoritative restoration and shared-shell ownership suites.
- Named failing baselines for the reported Escape gaps: `Escape dismisses the local data popup and restores its opener`, `Escape dismisses the notification tray and restores its opener`, `Escape dismisses Builder position search despite its dialog boundary` (`popup-shortcuts-regressions.test.tsx`). All three failed before implementation on main `937aee7` and pass with the dispatcher.
- Layering and history retention: `Escape closes one topmost popup per press and passive toasts do not block board commands`; `Escape dismisses only the newest visible toast and retains notification history` (`notification-regressions.test.tsx`).
- Real browser proof: `training arrows never uncover the next answer and R restores the decision without grading`, `Builder shortcuts cancel a held piece and preserve notes, selection and splitter keys`, `Settings letter preference takes effect immediately and persists while arrows and help work`, `Escape closes Builder search and header popups one at a time and restores their openers` (`keyboard-context.spec.ts`). Visible piece geometry is checked rather than only React FEN attributes.
- PR #87 required-CI readiness repair: `training arrows never uncover the next answer and R restores the decision without grading` now asserts `data-input-enabled="true"` before its first real e2/e4 clicks. The loading placeholder already has the initial FEN, so FEN equality alone does not prove input readiness. CI run `37279590400` and a controlled, temporary queue-response gate reproduced ignored clicks while input was disabled, followed by the unchanged initial FEN after readiness. The gate is not part of the repair. Existing e4/e5 FEN, rendered-piece, revealed-frontier, Home/F/R and zero-review-write assertions remain intact; product board and migration-diagnostic behavior are unchanged.
- Cross-browser proof: `contextual board keys and nested popup Escape work across browser engines` (`cross-browser.spec.ts`) covers Chromium, Firefox and WebKit, alongside the existing tablet focus and capture workflows. `four comparison boards retain distinct routes and independent ply navigation` now verifies one active board, F/R and arrow navigation; guided review restoration verifies concealed and revealed navigation boundaries.
- Pinned appearance: `keyboard help and letter preference remain readable 390` and `1280` (`visual.spec.ts`) check touch-target size, dialog spacing, and that workspace controls do not overlap the help contents. The desktop overlap assertion was added after the first pinned review exposed the divider crossing the help popup; help now renders above the workspace through a portal. The new regular browser file belongs to the complete board CI family; no regression is skipped or excluded from the release gate.
## Issue #34 — visibility-aware status polling

Passive status reads pause while hidden/offline. Visible closed activity counts and writer health refresh every 30 seconds plus request duration on success; open activity and sync status retain their active 2-second / idle 15-second completion-relative intervals. Activity failure backoff is 5/10/20/60 seconds while open, with a 30-second minimum while closed. Sync failure backoff remains 5/10/20/60 seconds. Explicit recovery resets backoff. Hidden/offline status is last-known; returning visible/online refreshes it. No acquisition timer, command/outbox recovery, discovery scheduler, or backend policy changes.

- `tests/unit/status-polling-regressions.test.tsx`: `activity_polling_matches_open_visible_online_policy`, `activity_refresh_events_coalesce_without_parallel_requests`, `activity_obsolete_offsets_and_unmounted_sessions_cannot_publish`, `activity_offset_changes_ignore_late_success_and_preserve_one_flight`, `activity_offset_changes_ignore_late_error_and_preserve_one_flight`, `activity_rapid_open_close_preserves_a_single_closed_timer` cover timing, single-flight wakes, offset ownership, and lifecycle cleanup.
- `activity_equivalent_responses_preserve_render_identity`, `activity_idle_polling_and_real_progress_match_policy`, `activity_writer_failure_and_recovery_remain_actionable`, `activity_failure_backoff_and_recovery_match_policy_open_true`, `activity_failure_backoff_and_recovery_match_policy_open_false`, `activity_remount_offline_does_not_claim_service_recovery`, and `activity_invalid_payload_and_explicit_retry_preserve_actionable_errors` cover unchanged snapshots, real changes, errors, health notifications, and verified recovery.
- `sync_status_hidden_offline_and_failure_backoff_match_policy`, `sync_status_recovery_bursts_are_single_flight`, `sync_status_equivalent_responses_preserve_consumer_identity`, `sync_status_progress_errors_and_recovery_publish_changes`, `sync_status_failure_backoff_recovers_without_duplicate_incidents`, `sync_status_unmounted_responses_cannot_resolve_new_session_incidents`, `sync_status_superseded_by_a_manual_command_cannot_publish_an_old_completion`, `sync_status_equal_provider_counts_retain_identity_and_changed_counts_publish`, and `sync_status_invalid_payload_does_not_publish_false_success_and_recovers` cover passive sync status. Parameterized `sync_status_job_<status>_retains_its_existing_interval` covers all six job states.
- `passive_status_throttling_preserves_game_acquisition_and_pending_commands` and `activity_controls_remain_prompt_while_passive_reads_are_suspended` protect acquisition cadence, manual commands, and receipt confirmation independently of display polling.
- `equivalent_status_during_actual_training_avoids_parent_commits_and_board_publications` exercises the real TrainingView with active status polling. Parameterized `status_request_counts_match_the_sixty_second_window_<scenario>` covers closed/open, active/idle, hidden, and offline counts under controlled clocks. The open window counts its explicit open refresh and excludes the preceding closed bootstrap; hidden/offline windows launch no passive requests.
- `tests/browser/activity-tray.spec.ts::status_recovery_during_training_preserves_held_drag_and_command_execution` holds a real piece across a delayed status response and coalesced recovery burst, then checks explicit activity control. The existing navigation-growth case explicitly wakes the closed trigger before assessing geometry.

These count/identity assertions establish reduced polling and React work, not a drag-latency improvement. Existing held-drag preservation and diagnostics specs remain independent boundary coverage.

`manual_sync_startup_suspends_passive_reads_until_command_state_and_then_reconciles` covers a completed bootstrap read followed by delayed manual settings, wake/timer events during startup, queued command publication, rejected pre-command status, and one legitimate post-command reconciliation. It failed on PR #59 before separating command invalidation from status recovery. The existing in-flight supersession regression remains in the suite.

`activity_coalesced_success_clears_failure_before_react_commits` batches a failed read and immediately successful queued follow-up without an intervening React commit. It verifies no alert/attention, resolution of a retained incident, single-flight request counts, and exactly one closed-panel timer. It failed on PR #59 before synchronously tracking requested errors; polling, control, and retry errors now use the same publisher.

`manual_sync_post_command_status_reconciles_before_react_commits` verifies that an immediately completed post-command read can reconcile queued command state to a completed server job, even when it equals the bootstrap snapshot and the command/read share a React batch.

`manual_sync_precommand_error_is_not_erased_by_historical_completed_status_<failure>` covers missing usernames, settings/enqueue HTTP and validation failures, and blocked receipts: each retains its local error without an immediate historical-job reconciliation and through repeated later historical polls while the idle timer continues. All six strengthened cases failed on PR #59 before explicit error ownership. `manual_sync_<pending|complete>_receipt_keeps_immediate_status_reconciliation` preserves prompt reconciliation for non-blocked pending operations and confirmed saved commands, including unchanged receipt identity and POST counts.

`successful_manual_sync_supersedes_prior_manual_error` protects local errors from historical completions and unrelated active-job errors, retains the error during a delayed retry, then verifies a validated subsequent command and immediate reconciliation can clear it. Command errors and queued React state share one publication contract; status-derived errors retain their existing recovery policy.

`activity_initial_offline_mount_reports_unavailable_not_empty` verifies no initial offline read, an explicit unqueried message without a service incident, one coalesced online recovery, and known-empty rendering only after success. `activity_offline_after_success_preserves_last_known_empty_status` retains known-empty counts offline. `activity_offline_after_success_preserves_last_known_items_and_writer_health` retains cached details and actionable writer health when the tray first opens offline, without a read or false recovery. Initial false-empty and missing cached-detail cases failed on PR #59 before the fix.

`activity_polling_preserves_current_main_diagnostics_throttle` protects main's activity-triggered diagnostics read and 15-second minimum across open polling, offline suspension and coalesced recovery. Activity/sync request-count fixtures exclude this separate diagnostic endpoint; its real request path remains covered by the named integration regression.

Issue #37: background progress diagnostics (observability only).
The regular Python suite includes `backend/tests/test_background_diagnostics.py`:

- `test_background_diagnostics_classifies_queue_states_and_eligibility` and
  `test_background_oldest_eligible_age_excludes_delayed_paused_and_blocked` cover
  queued/delayed/paused/leased/retrying/failed/complete without treating delay as
  eligible starvation.
- `test_background_pending_age_survives_retry_deferral_and_reclaim` and
  `test_background_generation_replacement_and_restart_are_visible` preserve dirty
  age independently of current generation age and lifecycle churn.
- `test_background_duplicate_delivery_does_not_inflate_semantic_completion` and
  `test_engine_callback_replay_does_not_inflate_position_or_preemption_counts`
  distinguish committed slices, full generations and accepted semantic units.
- `test_background_admission_wait_is_separate_from_handler_execution` and
  `test_background_diagnostics_preserves_foreground_responsiveness` prove the
  timing seam without changing the gate's blocking behavior.
- `test_background_stale_delivery_and_result_discard_are_distinct`,
  `test_background_stale_delivery_skips_the_expensive_handler`,
  `test_background_missing_task_delivery_is_observed_without_a_dangling_event`,
  and `test_background_stale_publication_lock_and_removed_result_are_observed`
  cover pre-execution and publication fences, including deleted rows.
- `test_background_lease_expiry_and_reclaim_are_counted_once` and
  `test_engine_defense_expired_lease_reclaim_and_claim_are_observed` cover lease
  churn without modifying claim eligibility or capacity.
- `test_engine_preemption_seconds_are_separate_from_successful_work` separates
  abandoned/preempted search time from accepted successful positions.
- `test_background_snapshot_cost_is_independent_of_event_history`,
  `test_background_metric_buckets_expire_without_unbounded_growth`, and
  `test_background_diagnostics_query_deadline_returns_unavailable` cover a
  100,000-event history, 600 bucket rotations and explicit bounded-query failure.
- `test_background_diagnostics_redaction_and_schema_parity`,
  `test_background_public_diagnostics_reject_invalid_counters_and_old_engine_fields`,
  and `test_background_runbook_snapshots_validate_without_private_fields` cover
  public contracts, missing legacy timing and sanitized example snapshots.
- `test_background_metric_outcomes_roll_back_with_their_transaction` and
  `test_postgres_background_counter_flush_follows_domain_writes_and_sorts_locks`
  cover rollback, coalescing and deterministic lock order.

`tests/unit/background-diagnostics-regressions.test.ts` supplies the named
Python/TypeScript schema parity, bounded cache and monotonic engine outcome
regressions. `debug bundle includes only validated aggregate background diagnostics` in `debug-reporting-regressions.test.tsx` protects
export redaction. The normal PostgreSQL `schema_upgrade` scenario executes
`check_postgres_background_diagnostics.py` against a runner-owned database for
real migration/replay/concurrent counters/lease reclaim/rollback and query cost.
These are new instrumentation contracts; there was no prior snapshot endpoint
against which to demonstrate an equivalent failing baseline. Existing scheduling
and callback contract tests remain in the regular suite.

CI #37 packaging regression: `engine Docker image includes every relative worker
module including diagnostic timing` in the regular background diagnostics unit
file fails before copying the new helper into `Dockerfile.engine`. The initial PR
PostgreSQL job reproduced the missing module by exiting at worker startup. The
fixed candidate must pass the real disposable Docker startup and durability gate.

`test_background_postgres_numeric_aggregates_preserve_strict_public_schema`
reproduces PostgreSQL SUM(bigint)'s Decimal values, including estimated-age sums,
before conversion to public integers. It failed before the producer conversion;
strict consumer validation remains unchanged. The disposable ring-retention
assertion reads after commit because PostgreSQL counter deltas flush at that
boundary.

PR #56 review correction: stale-delivery preflight must not bypass admission or
consult lagging replica state. In `backend/tests/test_background_diagnostics.py`,
`test_background_delivery_preflight_is_classified_and_measured_before_handler`,
`test_background_delivery_preflight_uses_primary_and_read_only_transaction`, and
`test_background_delivery_preflight_waits_for_foreground_admission` all failed on
the reviewed head before the fix. Existing stale-handler-skip, duplicate-delivery,
and stale-publication regressions retain the final authoritative fences.

PR #56 reservation correction: `test_background_diagnostic_redis_io_never_runs_under_database_reservation`
checks actual diagnostic Redis calls against both local and shared lease state,
including exception cleanup. `test_background_admission_timing_excludes_diagnostic_publication`
uses a controlled clock to prove telemetry latency is excluded from admission wait.
Both are regular cases in `backend/tests/test_background_diagnostics.py`.

PR #56 event-accounting correction: `test_background_known_kind_lifecycle_has_no_redundant_kind_select`
traces enqueue/replacement/claim/failure/retry/slice/restart/completion/deferral
and fails on extra kind-only reads (12 on the reviewed lifecycle baseline).
`test_background_id_only_event_keeps_single_kind_lookup` preserves exceptional
ID-only accounting. Both run in `backend/tests/test_background_diagnostics.py`;
the disposable PostgreSQL proof also traces the full known-kind event hook.

PR #56 main integration exposed a readiness race in the inherited browser case
`Black-first capture keeps its orientation while typed SAN and real-board moves save one solution`.
CI reproduced a startup-FEN comparison after the actual puzzle had loaded. The
case now waits for the shared board's existing input-ready signal before taking
its baseline; all SAN/orientation/persisted-UCI and unchanged-board assertions
remain. No product code, timeout, CI selection or retry policy changed.

## Issue #33 — globally bounded Discoveries preview work

The regular frontend suite includes the following named regressions:

| Defect/behavior | Named coverage |
| --- | --- |
| Overlapping initial/incremental pools exceed two requests | `discovery_preview_global_concurrency_survives_overlapping_triggers` in `discoveries-tray-regressions.test.tsx` (baseline peak four); `discovery_explicit_demand_precedes_queued_speculation` in `discovery-preview-scheduler-regressions.test.ts` queues 100 entries and elevates the requested item |
| Waiting preview hides an independently usable discovery | `discovery_ready_item_does_not_wait_for_feed_preflight`; existing `shows only ready discoveries in feed order and does not acknowledge hidden items` now verifies immediate saved-card readiness and preservation of the active item as earlier entries become ready |
| Closed training triggers feed/preview churn | `discovery_closed_training_defers_preview_and_feed_work`; `discovery_open_request_elevates_existing_demand_during_training` |
| Hidden/reconnected pages cause overlapping work or partial counts | `discovery_visibility_return_coalesces_refresh_and_drain`; `discovery_offline_recovery_defers_reads_and_preserves_full_pagination`; `discovery_hidden_mid_pagination_never_publishes_a_partial_feed`; scheduler `discovery_visibility_gate_is_checked_at_capacity_release_before_react_reconciliation` |
| Replaced or removed evidence accepts late results | Scheduler `discovery_fingerprint_replacement_fences_late_results`, `discovery_removal_and_return_uses_new_generation`; component `discovery_replaced_and_returned_component_preview_cannot_publish_obsolete_results`; PR #28 browser late-response cases remain |
| Retry wakes create duplicate pools/backlogs | `discovery_due_retries_do_not_duplicate_queue_entries` exercises 100 waiting entries, one earliest wake, repeated updates and paused demand |
| Unmount leaks work into another instance | `discovery_unmount_disposes_work_and_fences_remount`; `discovery_component_unmount_aborts_and_ignores_old_preview` |
| Speculative pause blocks admission confirmation | `discovery_admission_confirmation_is_independent_of_preview_pause` verifies one submit, one confirmation, one queue refresh and a required feed refresh during hidden training |
| Validation repeats, accepts replaced positions/results, or counts React metadata reuse as cache hits | `discovery_validation_cache_reuses_only_current_authoritative_identity`; `discovery_same_fingerprint_position_replacement_invalidates_validation`; `discovery_synthetic_workload_records_request_and_validation_counts` |

Browser: `hidden discovery speculation stays paused and viewer demand starts bounded look-ahead`
in `discovery-viewer.spec.ts` verifies real Home training wiring, visibility wakes,
viewer demand and the concurrency diagnostic. Existing inactive preview, fingerprint,
removal/return, navigation and admission browser tests explicitly open the viewer
before expecting preparation. Held-drag and pinned performance scenarios still
release real in-flight Discoveries responses during dragging; explicit viewer demand
now starts that work before returning to the training board.

Policy and before/after evidence: [Discoveries preview scheduling](../docs/discoveries-preview-scheduling.md).

- Issue #33 viewer look-ahead leaves a newly returned earlier item unnavigable: `discovery_viewer_prepares_new_items_before_current_feed_position`; browser `late removed preview cannot be reused when the discovery returns` preserves the current item while the earlier result prepares. Look-ahead remains limited to two neighbors.
- Issue #33 foreground queue independence with bounded closed preparation: browser `discovery preview backlog leaves a prompt foreground training queue refresh` retains its queue latency and active request cap assertions, checks that closed preparation stops at two, and proves explicit viewer demand resumes work.

- Issue #33 stalled look-ahead starvation: `discovery_waiting_previews_do_not_starve_later_viewer_candidates` opens a saved item, stalls the first two previews (waiting/failed), prepares and navigates to a later ready item before their deadline, preserves the active selection, and proves 30-second retries remain deduplicated across repeated renders/wakes with global concurrency two. It failed before separating retry demand from productive capacity.
- Issue #33 rejected-loader diagnostics: `discovery_rejected_loader_reenqueues_retry_with_consistent_diagnostics` proves rejected loads re-enqueue with consistent `enqueued`/`retriesScheduled` counters, one pending identity and timer, unchanged backoff, released capacity and timer cleanup. It failed before incrementing `enqueued` on rejection.
- Issue #33 / PR #55 integration: `dismissal advances through undecided discoveries and clears the final item` failed after the initial rebase because a deferred ready-item addition could reinsert a removed item. The addition now checks the current authoritative feed object before publishing; PR #55's handled-evidence and PR #59's polling coverage remain intact.
- Issue #33 concurrent ready-result reconciliation: `discovery_unchanged_refresh_preserves_a_concurrent_ready_preview` completes a valid preview and an inactive-item refresh in one React batch. It failed before retaining unchanged item ownership independently of lagging rendered-result refs. The existing browser `inactive discovery preview refreshes once without a retry notification and another discovery loads` reproduced this ordering in CI; its assertions and deadlines remain unchanged.

## PR #66 review — current routes, SQLite parity and preview lifecycle

`backend/tests/test_canonical_repertoire_prefix.py` adds:

- `test_canonical_prefix_current_route_required_after_source_disappears_at_branch_boundary` (delete/change): actual branch admission rejects an expired route, historical continuations cannot self-certify, and a restored rooted line plus a fresh check permits admission. Both variants failed before the source fence.
- `test_sqlite_unrestricted_zero_node_coverage_preserves_complete_empty_run`, paired with the existing `test_canonical_prefix_matching_stub_without_continuation_never_reports_complete`. The unrestricted case failed before the fix.
- `test_sqlite_shared_card_edit_validates_all_memberships_before_study_mutation` (owner/link variants): valid edits succeed; either affected repertoire can reject the whole edit, with unchanged cards, revisions, reviews, queue, annotations and tasks. Both variants failed before the fix.
- `test_sqlite_prefixed_pgn_reimport_validates_all_candidates_atomically`: a mixed matching/off-scope re-import leaves all study tables unchanged; a matching re-import succeeds. Failed before the fix.
- `test_canonical_prefix_identical_previews_reuse_work_and_version_changes_create_new_scan`: duplicate checks reuse one scan; changed source and prefix versions require a new one. Failed before the fix.
- `test_canonical_prefix_preview_retention_is_bounded_restartable_and_preserves_active_certificate`: interrupted leases, bounded child cleanup, replay fencing, active-preview preservation and stale-proof rejection.
- `test_canonical_prefix_card_root_recertifies_connected_lines_but_cannot_resurrect_deleted_anchor`: card roots participate in multipass validation and deletion invalidates their certificates.
- `test_sqlite_integrity_line_rewrite_cannot_escape_canonical_scope`.
- `test_canonical_prefix_unverified_continuation_never_reports_partial_routes_as_complete`.
- `test_canonical_prefix_admission_rechecks_source_version_before_inserting`.
- `test_integrity_shared_card_replacement_belongs_only_to_validated_repertoire`.

`backend/tests/test_postgres_cutover.py` adds
`test_postgres_coverage_unverified_canonical_route_fails_actionably_without_publication`.
The component regression `canonical prefix confirmed save followed by refresh failure remains committed without a stale retry` failed before the UI fix and protects the confirmed receipt and disabled stale save.

The regular PostgreSQL `study_durability` scenario now independently requests the
same prefix while its worker is stopped and proves one persisted scan survives
restart. It also exercises real branch commands: deleting a source rejects a new
arbitrary-FEN continuation; restoring and recertifying the route admits it again.
Existing foreground-contention, interrupted command, history/schedule preservation,
exact-order membership, shortening, Black scope and later transposition regressions
remain in the regular gate.
`test_postgres_coverage_fingerprint_tracks_route_certificates_only_for_scoped_repertoires`
retains unrestricted coverage identity across derived card/link changes while
scoped source mutations invalidate route-dependent calculations.
`test_canonical_prefix_retired_preview_cannot_save_during_bounded_cleanup`
protects the save/retention race: a preview is invalidated before its first child
is removed, so an otherwise-current token cannot become an incomplete active
certificate. `canonical prefix command accepts a reused %s compatibility result`
covers ready/conflicting producer responses (both failed against the previous
checking-only frontend contract). The real browser workflow repeats Check prefix
and asserts that the task identities remain unchanged.
The same PostgreSQL scenario submits ten distinct candidates and waits for durable
retention to settle at at most nine previews while preserving the active pointer.

`Canonical prefix dialog 390` and `Canonical prefix dialog 1280` in the pinned
visual suite also keep the compact preview's shortcut toolbar hidden and its Save
button fully visible. Both failed after merging main's keyboard controls: the new
default toolbar clipped the phone footer and changed the desktop layout. The
preview uses the existing `showShortcutButton` option; shared board controls and
the existing screenshot baselines remain unchanged.

### Training burial until tomorrow

Bury retains today's unfinished queue rows as `buried`, preserving scheduling and completed cycles. Those rows exclude the card from same-day materialization and still consume its new Study admission quota. Active queue reads remain `queued` only; next-day eligibility follows normal admission. PostgreSQL supplies durable command receipts; the SQLite compatibility route intentionally does not provide receipt replay.

- Study quota retention: `backend/tests/test_daily_queue_randomization.py::test_buried_new_study_card_consumes_daily_quota_without_replacement` proves SQLite materialization excludes the buried card, admits no replacement, and retains unrelated order. `tests/fixtures/postgres-study-burial-quota.py`, invoked by the regular `study_durability` scenario, executes real PostgreSQL preparation, locked stale-candidate admission, burial receipts, and queue refresh. Its named proof is `PASS PostgreSQL buried Study admission retains quota through materialization and locked candidate replay`.
- Terminal command cleanup: `terminal 409 burial clears its identity so a later legitimate head attempt gets a new command`; `durable failed burial receipt clears its identity for a fresh independent attempt`; `definitive HTTP %s burial rejection clears its identity` in `tests/unit/training-burial-regressions.test.ts`. These complement retained identities for transport ambiguity, pending/blocked receipts, retriable HTTP 408/429/503, and ambiguous successful bodies.
- Confirmed command with failed refresh: `Home burial retry after failed queue refresh replays the selected entry even if the active card changes` in `tests/unit/training-burial-caller-regressions.test.tsx` exercises the real Home callback and proves it replays the original selected entry and clears identity only after refresh succeeds.
- Browser daily reset: `stale imported-card burial never appends a card to the independently generated next-day queue` proves stale markers clear and never add yesterday's imported card. Existing same-day exclusion and empty-queue coverage remain; `browser burial can empty the queue and clears deleted identities tomorrow` covers deleted identities.
- Real workflow and durability: `training Bury hides the card for today across reload and reports a failed bury` in `tests/browser/workspace-flows.spec.ts`; `PASS PostgreSQL bury until tomorrow survives recreation and idempotent replay without grading` in the regular durability runner. These cover PostgreSQL queue exclusion/order, unchanged scheduling/reviews, service recreation, and receipt replay.

The quota, terminal-ID, stale browser marker, and Home refresh-retry regressions reproduced failures against reviewed commit `5a4163a` before their respective fixes. Validation results belong to the current PR candidate; a previous revision's passes are not evidence for an updated candidate.

- Final retry-state coverage: `direct 500 durable failed burial clears identity for a fresh command`, `direct 500 completed receipt confirms original burial until queue refresh`, `direct 500 with %s receipt retains burial identity`, and `direct 500 mismatched completed receipt rejects and retains identity` resolve server errors only through durable receipts. Unknown, queued, pending, executing, retrying, blocked, and unavailable receipts retain identity.
- `Home %s burial controls block mutations until a definitive outcome` covers lost response, pending receipt, and terminal rejection with the real Training component: board moves, grading, Again, restart, editing, and normal Bury lock while explicit Retry bury stays available. The existing failed-refresh regression also asserts reactive pending state, read-only shared board, replacement-card controls, original-entry replay, and successful cleanup. The real browser burial workflow verifies disabled controls and blocked board input before retry.

- Recovery: `blocked burial retry uses durable endpoint and preserves identity through %s` covers complete, failed, nonterminal, conflicting retry acceptance, and mismatched results. `Home unresolved burial survives remount and resolves %s on its original entry` covers transport/pending/blocked recovery, already-completed and failed receipts, and failed refresh after recovered completion. A single pending-entry marker restores the mutation lock before paint; operation identity clears only after terminal failure or confirmed refresh.
- The browser burial workflow reloads while unresolved and verifies the board/control lock, explicit retry, original entry/operation identity, and cleanup. `PASS PostgreSQL blocked burial resumes original payload through retry endpoint without duplicate effects` uses the production receipt lifecycle and real HTTP retry endpoint in the regular durability scenario, preserving scheduling, reviews, and unrelated queue order.

- Terminal burial recovery cannot leave a Retry control that starts a new burial on the replacement card: `Home unresolved burial survives remount and resolves failed on its original entry` verifies entry 42 cleanup, interactive replacement 43, no Retry bury and no replacement request; `Home terminal burial controls block mutations until a definitive outcome` covers ordinary terminal rejection. The `training Bury hides the card for today across reload and reports a failed bury` browser workflow also verifies terminal recovery removes Retry while restoring normal controls without issuing a new burial.

### PR #69 legacy opening trained-color compatibility (AS-16 / AS-19)

- `test_postgres_legacy_opening_color_queue_checkpoint_round_trip` in the regular disposable PostgreSQL evidence rehearsal covers actual queue transport for raw NULL cards under white/black repertoire scope, manifest construction, authoritative checkpoint preparation/persistence, raw snapshot preservation and retained provenance through recreation/backup restore. The white queue manifest assertion must fail on reviewed head `ec40b0a` before the repair.
- `test_postgres_legacy_color_replay_survives_line_replacement` (white/black) replaces mutable source lines, explicitly rebinds the same queue context, and verifies the original manifest/color, preparation, exact event replay and retained historical provenance after source deletion. `test_postgres_legacy_edit_captures_new_color_without_rewriting_history` exercises presentation-edit triggers: the new revision captures the new color while old evidence remains valid.
- `test_postgres_modern_color_wins_over_repertoire_fallback` preserves explicit card color; `test_postgres_shared_legacy_color_requires_authoritative_admission` (white/black) rejects unbound shared ownership and accepts explicit scope despite the display repertoire choosing the opposite color. `test_postgres_missing_or_invalid_legacy_color_omits_context` rejects missing/invalid source or explicit colors; `test_postgres_legacy_color_line_order_is_deterministic` covers equal-timestamp lines inserted in the opposite ID order.
- Backend contracts `test_scoped_color_adapter_preserves_raw_legacy_presentation`, `test_scoped_color_adapter_rejects_unknown_and_explicit_mismatch`, and `test_evidence_migration_captures_valid_scope_color_without_overwriting_context` keep raw identity separate from captured scope color. The existing close-read-before-traversal regression still covers authoritative preparation. The populated upgrade rehearsal seeds NULL white/black cards and queue rows before migration 30, checks preserved raw snapshots and captured context colors, and applies migrations twice. Readiness remains 30; no inferred observations are created.
- `test_manifest_identity_preserves_captured_color_in_v1_hash` (white/black) proves deterministic producer identity retains trained color in the unchanged v1 hash preimage; no NULL manifest schema or identity special case is introduced.
- `test_postgres_legacy_review_requeue_preserves_validated_color` (white/black) completes a real legacy evidence-bearing review, confirms the reinforcement queue context retains the validated manifest color, and round-trips its emitted manifest through authoritative preparation. The first durability attempt exposed the existing completion-copy INSERT missing the new NOT NULL field; that one statement now copies the validated color without changing reinforcement scheduling.

PR #69 candidate CI fixture sequencing: `late obsolete preview cannot replace a newer evidence version` waits for the second feed to commit before advancing its next interval, preserving held obsolete response coverage. `Settings letter preference takes effect immediately and persists while arrows and help work` confirms the move FEN and rendered pieces before testing navigation. Both remain mandatory regular browser cases; production behavior is unchanged.

### PR #69 reinforcement context authority after legacy source replacement (AS-16 / AS-19)

- `test_postgres_legacy_review_requeue_inherits_color_after_source_replacement` in the regular disposable PostgreSQL rehearsal covers white→black and black→white source replacement after an immutable manifest is issued. The review-generated reinforcement must inherit the validated parent presentation, repertoire and learner color, emit the same manifest, and accept a new repeat checkpoint. Original contexts and checkpoint-only scheduling state remain unchanged. Completed-review receipt replay returns the same result without another queue entry or any committed context row-version update. The case runs first; its assertion reproduced mutable black instead of validated white on reviewed head `19eacdd` before production repair.
- `test_review_requeue_context_correction_only_runs_for_fresh_review` checks accepted completion validation precedes aggregate review and context correction, and only a fresh non-idempotent review with a new requeue writes the validated context. Existing receipts (including cached `idempotent=False`), idempotent completed-queue replay, absent requeues and unspecified freshness cannot write the correction.

### PR #69 current-main integration after PR #70 revert

- `test_evidence_migration_is_unique_additive_and_matches_readiness` preserves the contiguous 001–030 sequence; evidence migration 30 replaces unmerged 31 after current main reverted PR #67. `test_postgres_current_main_upgrade_captures_legacy_evidence_contexts` in the regular populated upgrade rehearsal reaches actual main schema 29 before seeding modern and NULL-colored white/black presentations and queue rows, then applies migration 30 twice and checks raw snapshots, captured colors and no inferred observations. The historical schema-16 receipt/business upgrade remains covered. PR #67-only guided-repair tests and documentation remain reverted.

### PR #69 shared-repertoire repeat evidence scope (AS-16 / AS-19)

- `test_postgres_shared_review_requeue_inherits_authoritative_evidence_scope` (white/black) uses the actual queue and review path for a card linked to two differently colored repertoires. The original admission proves one scope; its fresh review copies an immutable context even though reinforcement admission is NULL. The repeat must emit the exact parent manifest/color without diagnostics, prepare and persist a new checkpoint, preserve scheduling, and replay the parent receipt without duplicate rows or context row-version changes. Retained provenance is checked by regular recreation and backup/restore. The case runs first to reproduce the missing-manifest defect before the scope fix.
- `test_postgres_evidence_context_requires_unique_scope_and_matching_admission` rejects a conflicting context under explicit admission and multiple contexts with no admission. Existing `test_postgres_shared_legacy_color_requires_authoritative_admission` still rejects unbound shared entries without a context. The source-replacement color and fresh-review-only correction regressions remain mandatory.

## Chess.com Puzzle Rush PGN capture

Chess.com puzzle PGNs contain a leading opponent/setup move. Importing the
header FEN directly would train the wrong side and position. The exact supplied
export lives in `tests/fixtures/chesscom-puzzle-rush.pgn`; its PuzzleID header,
not the abbreviated example reference, is authoritative.

- `tests/unit/tactic-capture-pgn.test.ts::Chess.com Puzzle Rush PGN advances the setup move and imports only the solver line` — post-b3 black-to-move FEN, Bd4/Qxd4/Nxd4, canonical c5d4/f6d4/c6d4 and exact provenance. Named parameterized cases reject empty/malformed PGN, missing/invalid FEN, missing solution, illegal setup/continuation and null moves. Ordinary games and lookalike/non-puzzle Links are rejected; either PuzzleID or a valid Chess.com puzzle Link identifies the format. Explicit standard-position FEN, White learners, comments/variations and editable invalid Link are covered.
- `tests/unit/tactic-capture-regressions.test.tsx::Chess.com puzzle PGN populates an editable capture without persisting until Add to training` — no request or pending save on load, inspected/edited provenance, unchanged note and normal canonical-UCI capture command. Failed against original main because the import control did not exist.
- `Chess.com puzzle import rejects invalid input without changing the authored capture`; `Loading another Chess.com puzzle replaces editor state and preserves the note`; `Chess.com puzzle replacement resets both cursors, setup controls, errors and pending FEN` — atomic failure, replacement rather than mixing, cleared unsubmitted SAN, both orientations, navigation and absent provenance cleanup.
- `Chess.com puzzle controls stay locked while saving or awaiting capture confirmation`; `Chess.com puzzle Link errors use existing capture validation and leave imported fields editable` — pending/reopened capture cannot be overwritten, and existing terminal URL rejection permits correction. Existing manual/FEN/SAN and receipt regressions remain required.
- `tests/browser/tactic-capture.spec.ts::Chess.com Puzzle Rush PGN reaches ordinary Training through tactic capture` — real pawn placement on b3, black orientation, complete solution/provenance, no import POST, exact ordinary capture payload, dialog closure, unchanged Solve board and ordinary Training representation.
- `tests/browser/cross-browser.spec.ts::Chess.com puzzle import starts at the solver position across browser engines` — imported start, End/Home navigation and fixed orientation on Chromium, Firefox and WebKit.
- `tests/browser/visual.spec.ts::Capture tactic dialog 390` and `1280` — reviewed collapsed-dialog baselines plus expanded PGN input geometry and loading; existing manual SAN controls remain usable.

No backend business-rule, schema, idempotency or queue path changes accompany
this input adaptation.

### PR #69 foreground and browser-storage isolation

- `test_opening_checkpoint_dispatches_as_background_without_changing_review_dispatch` — standalone checkpoint routing, stable replay identity and foreground review routing (opening evidence backend contracts).
- `AS-15 opening evidence recovery waits for foreground startup readiness` — no mount/reconnect scan before queue readiness (opening-evidence Home lifecycle).
- `AS-16 evidence outbox quota falls back to durable aggregate-only review (QuotaExceededError/NS_ERROR_DOM_QUOTA_REACHED)` — Chromium and Firefox capacity errors use the same compact durable aggregate review/key, preserving the logical review and best-effort evidence retention (review outbox; PR #69 Firefox compatibility repair).
- `AS-16 aggregate-only outbox storage failure remains blocking and retryable (QuotaExceededError/NS_ERROR_DOM_QUOTA_REACHED)` — either capacity alias failing both writes does not send or claim persistence (review outbox).
- `AS-16 restarted opening attempt records displayed guidance as guided` — fresh guided identity and actual displayed assistance category (Home lifecycle).
- `test_postgres_background_opening_checkpoint_preserves_foreground_progress_and_replay` — stopped background worker leaves queue/review foreground paths available; worker restart and exact checkpoint receipt replay (regular Docker study durability).
- `AS-15 recovery cancels idle work during foreground transitions and after unmount`, `AS-15 only the current queue generation can settle recovery readiness`, `AS-15 overlapping recovery shares one scan and failed storage remains retryable` — lifecycle cancellation, empty valid queue, unavailable queue, generations and coalescing.
- `AS-16 wrong-response reveal remains revealed` — real Home move handling keeps post-error assistance distinct from Restart guidance.
- `AS-16 quota fallback reload and ambiguous retries retain the compact payload and key (QuotaExceededError/NS_ERROR_DOM_QUOTA_REACHED)`, `AS-16 denied initial review storage never switches to aggregate-only (SecurityError/InvalidStateError/NotAllowedError)`, `AS-16 unknown initial review storage failure remains blocking` — durable capacity-fallback identity and fail-closed non-quota or non-DOMException storage errors, including an unverified object with the Firefox quota name.
- `AS-15 recovered evidence waits for foreground queue readiness and an idle opportunity`, `AS-16 restarted opening board records guided arrows and retains the prior partial attempt`, `AS-16 local review quota saves the aggregate and retains evidence through a late checkpoint receipt` — regular training-family browser workflows for orphan recovery, rendered Restart guidance, aggregate advancement, retained IndexedDB data and in-flight receipt safety.
- `test_opening_checkpoint_request_is_background_without_a_client_work_class_header` — actual HTTP middleware keeps standalone checkpoint receipt admission out of its own foreground lease; legacy clients need no new header, and review requests stay foreground.

### PR #69 — preserve shadow-only GET admission

- `AS-15 orphan evidence verification uses background HTTP admission` — saved orphan completion verification uses the background HTTP helper; no aggregate review is invented.
- `AS-15 background checkpoint receipt polling remains background after HTTP 202 (pending retry=false/true)` — actual journal delivery retains background admission through receipt confirmation, frozen payload/key, successful acknowledgment and ambiguous pending retry.
- `foreground review completion and operation receipt reads retain foreground admission by default` — the shared operation helper preserves foreground review POST and receipt semantics.
- `test_opening_checkpoint_receipt_read_uses_background_admission` — actual API middleware enters the requested activity class and passes the same background flag to the receipt reader; no header retains foreground defaults.

### PR #69 bounded checkpoint delivery and durable failure parity

- `AS-15 checkpoint receipt polling is bounded by the delivery timeout (POST 202/500)` — actual journal receipt GET observes the POST's abort signal; the delivery settles, clears the active flush, and retries the unchanged frozen key/body/events.
- `AS-15 immediate and deferred durable checkpoint failure both quarantine and advance the evidence queue (immediate/deferred)` — the same failed durable receipt rejects the first journal with retained diagnostics/events and delivers the next journal in the same flush.
- `AS-15 ambiguous checkpoint HTTP failure retains frozen delivery for unchanged retry (unknown/pending/retrying/blocked/missing/network)` — transient HTTP failure is never quarantined without a failed receipt; blocked errors remain actionable; later retry preserves identity.
- `AS-15 durable complete receipt resolves an ambiguous checkpoint HTTP failure` — durable success wins over an ambiguous HTTP response without a duplicate POST.

- `foreground receipt polling forwards an optional caller signal without changing admission` — cancellation remains caller-controlled and does not demote foreground work.

These regressions run in `tests/unit/opening-evidence-background-admission.test.ts`, alongside background admission, pending/complete receipt and foreground review controls. Existing browser and PostgreSQL background contention/restart/replay coverage remains required.

### PR #69 offline quota durability and one-journal recovery slices

- `AS-16 offline review quota falls back to durable aggregate-only phone review` — actual prepared transaction aborts only for the evidence envelope, compact commit preserves aggregate identity/repeat rules, retained journal survives reload/sync, and ambiguous replay retries the frozen aggregate-only key/body.
- `AS-16 offline aggregate-only storage failure remains blocking and retryable`; `AS-16 offline evidence non-quota storage errors remain blocking (SecurityError/InvalidStateError)` — no false durable review, queue advance or network send.
- `AS-16 offline quota retention failure cannot undo a durable compact review` — optional diagnostics cannot block the committed review or duplicate its repeat on retry.
- `AS-15 opening evidence recovery processes one journal per idle slice`; `AS-15 a failed recovery journal consumes one slice and yields before the next journal (immediate/deferred)` — real async journal storage plus coalesced recovery preserves untouched later records and deterministic eventual progress.
- `AS-15 foreground activity pauses remaining opening evidence recovery backlog` — real hook/idle callbacks recheck foreground state between actual journal slices; repeated reconnects serialize work, and unmount cancels it.

Coverage lives in `tests/unit/opening-evidence-offline-quota.test.ts` and `tests/unit/opening-evidence-recovery-slices.test.tsx`, alongside existing live-delivery, Home, outbox, browser and PostgreSQL contracts. Offline aggregate ownership and live flush behavior remain authoritative.

- `AS-16 offline evidence quota saves a compact phone review and retains its journal after sync`; `AS-16 offline compact quota failure blocks advancement until durable retry` — real IndexedDB transactions and phone UI prove compact durability, blocking failure/retry, aggregate-only sync identity and retained evidence across reload. Required in the training browser family and complete matrix.
- `AS-15 reconnect requests share an active recovery slice without concurrent journal work`; `AS-15 a live browser lease yields to later recovery journals without closing its attempt`; `AS-15 recovery leaves completions owned by pending aggregate reviews and retained evidence untouched` — serialized reconnects, lease safety/fairness and aggregate ownership exclusions.
- `AS-16 aggregate phone storage quota without evidence remains blocking` — quota fallback is restricted to optional evidence; the successful quota case covers modern and legacy Firefox quota names.
- `AS-15 live flushing requested during a recovery slice keeps its normal delivery behavior` — shared serialization does not drop a live caller's pending checkpoint work or expand the recovery slice itself.

### Bounded review and recovery lifecycle

- `AS-15 transient opening-evidence recovery failure schedules a paced bounded idle slice` — the actual hook and asynchronous journal retain the exact header/events on a transient verification failure and schedule one paced new idle opportunity without reconnect, readiness changes or lease events; the next slice delivers the original observed work normally. Coverage: `tests/unit/opening-evidence-recovery-lifecycle.test.tsx`.
- `AS-15 foreground activity pauses a retry scheduled after recovery failure`; `AS-15 repeated transient recovery failures each wait before a distinct idle opportunity` — pending retry cancels for opponent replies, coalesces reconnects and resumes once safe; two failures consume two separate idle callbacks after their backoff deadlines, preserve durable evidence and keep the keyed warning deduplicated.
- `AS-15 unmount cancels an idle retry after transient recovery failure`; `AS-15 a transient recovery failure settling after unmount cannot schedule retry`; `AS-15 a throwing warning subscriber cannot strand the next recovery idle slice` — cancellation, mounted-state guard and best-effort diagnostics protect bounded retry scheduling. Existing `AS-15 reconnect requests share an active recovery slice without concurrent journal work` also covers rerenders and shared active promises. Recovery lifecycle/slices files remain in the regular unit inventory.

- `test_standalone_opening_checkpoint_reduces_outside_background_transaction` — AS-15 actual standalone worker/gateway reduction must run after every background read/write section closes; foreground review completion remains atomic.
- `test_standalone_checkpoint_prepares_maximum_events_and_decisions_without_mutating_source`, `test_standalone_checkpoint_preparation_preserves_terminal_gaps_and_completed_replay`, `test_standalone_checkpoint_preparation_retains_immutable_conflicts`, `test_standalone_checkpoint_stale_publication_retries_without_projection_writes` — AS-10/15 bounded pure preparation preserves gaps, seals, conflicts and completed evidence; a changed source retries before projection writes.
- `test_prepared_command_failure_retains_existing_structured_receipt`, `test_prepared_command_database_failure_remains_retryable_without_failed_receipt` — AS-10/16 preparation retains the command error envelope and retryable database failures.
- `test_postgres_opening_checkpoint_reduction_yields_to_foreground_review` — AS-15 regular PostgreSQL durability pauses 256-event/20-decision reduction after its connection closes; verifies idle PostgreSQL sessions, no attempt lock or active background section, a real foreground review before release, publication below the unchanged 250 ms budget, exact replay and unchanged scheduling.
- `test_postgres_opening_checkpoint_stale_preparation_preserves_foreground_completion` — AS-10/15 a foreground atomic completion overtakes paused preparation; exact source revalidation rolls back stale publication and recovery preserves the completed seal, observations, summaries and clean days.
- `test_postgres_opening_checkpoint_restart_recomputes_original_receipt` — AS-15 a real worker process exits after preparation before publication; lease recovery recomputes the original durable payload, publishes once, and survives exact replay, service recreation and backup/restore.

- `AS-16 online review receipt polling remains bounded after HTTP 202`; `AS-16 phone review receipt polling remains bounded after HTTP 202` — actual durable outbox/phone replay, one signal across POST and receipt GET, 15s/5s fake deadlines, frozen retries and exactly one confirmation; includes compact quota identities.
- `AS-15 orphan completion verification timeout yields and retries safely` — a stalled background GET releases the coalesced recovery promise after 15s without changing journal state; a later 404 yields one partial checkpoint.
- `AS-15 lease-blocked opening evidence is retried after the owning tab releases it`; `AS-15 live lease retry does not create an idle recovery loop` — held A and later B/C progress, passive ownership release wakes the actual hook after backlog drains, foreground/idle checks, no polling and coalesced releases.
- `AS-15 unmount cancels passive lease waiters without running recovery`; `AS-15 orphan verification response body shares its bounded deadline` — cancellation and full response-read boundaries.
- `AS-15 a real tab lease releases stranded evidence into a later idle slice` — real two-tab Web Locks, B/C drain while A remains held, tab close wakes idle recovery without reconnect/reload; frozen retry and partial seal remain separate slices. Required in the training browser family and complete matrix.
- `AS-16 guided review failure receipt shares the foreground review deadline` — existing Again/failure command also retains its signal through receipt confirmation without demoting foreground admission.

- `AS-15 blocked opening-evidence operations do not automatically resubmit during idle recovery` — real hook/journal POST 202 and typed blocked receipt preserve the frozen checkpoint, events and operation identity without automatic retry or rejection. Coverage: `tests/unit/opening-evidence-recovery-policy.test.tsx`.
- `AS-15 transient opening-evidence retries use capped backoff before a new safe idle slice` — sustained known fetch failures cannot retry on idle availability alone; one timer follows 1/2/4/8/16/30 seconds and requests a safe idle slice with unchanged data/key.
- `AS-15 connectivity lease and render storms cannot bypass recovery backoff`; `AS-15 foreground activity pauses an eligible recovery retry`; `AS-15 successful recovery resets backoff and healthy backlog has no delay`; `AS-15 post-paint recovery retries obey the same eligibility delay` — deterministic eligibility clock and idle/RAF/MessageChannel callbacks prove pacing, coalescing, foreground priority and healthy drain reset.
- `AS-15 explicit operation status recovery resumes a deferred journal without starving backlog`; `AS-15 unresolved durable operation confirmation is paced` — blocked A stays immutable while B drains; actual explicit retry/status observation releases only the matching identity through readiness/foreground/idle gates; pending/executing/retrying receipts follow backoff.
- `AS-15 persistent browser recovery errors suspend until repaired state is explicitly reloaded`; `AS-15 offline recovery cancels admission and waits for connectivity without clearing blocked operations`; `AS-15 unknown HTTP and application failures are conservative while known service failures retry` — storage denial/quota, malformed data and unknown errors pause without repeated access, repaired-state reload resumes, offline prevents requests, and only known transport/service categories retry.
- `AS-15 retry cleanup cancels delayed and idle work`; `AS-15 a warning subscriber cannot change blocked suspension or transient backoff` — unmount/disable cancel wakeups and diagnostic failures do not alter policy. Existing late-settlement and passive lease cleanup proofs remain. All above: `tests/unit/opening-evidence-recovery-policy.test.tsx`.
- `operation status resume signal preserves receipt semantics`; `explicit operation retry signals only a proven nonblocked receipt despite throwing observers` — unknown/blocked/malformed states cannot resume; successful/pending known states notify observers without changing foreground defaults, result/error types or retry identity. Coverage: `tests/unit/operation-status-events.test.ts`.
- `AS-15 events during an active failing slice cannot create a concurrent or early retry`; `AS-15 disabling and re-enabling recovery preserves the pending retry deadline`; `AS-15 malformed saved journal validation suspends without discarding evidence`; `AS-15 a checkpoint deadline becomes paced transport recovery with unchanged delivery` — in-flight signals, disabled lifecycle, actual journal schema validation and the real abort deadline preserve serialization, pacing and durable evidence. Coverage: `tests/unit/opening-evidence-recovery-policy.test.tsx`.

## Tempo CLI after-merge maintenance

`tests/unit/tempo-cli-regressions.test.ts` runs the named Node cases in
`tests/runner/tempo-cli.test.mjs` through the regular suite:

- Redis restart fails during persisted loading despite a healthy container:
  `CLI Redis readiness waits for saved legacy healthcheck loading before schema migration or application startup`
  reproduces the user's zero-exit LOADING failure and requires actual PONG before
  schema, backup, migration, application startup, and deployment publication.
- `CLI Redis readiness retries strict loading error replies and connection refusal until PONG`
  and `CLI Redis readiness retries interrupted and timed-out probes with bounded remaining deadlines`
  cover nonzero loading replies and temporary connection/probe failures.
- `CLI Redis readiness retries server EOF until PONG with bounded probes`
  and `CLI Redis readiness expires at the configured deadline on persistent server EOF without maintenance startup or publication`
  cover Redis's `Error: Server closed the connection` reply, bounded retries,
  blocked lifecycle work, an unchanged deployment receipt, and saved failure evidence.
- `CLI Redis readiness probes terminal errors before Docker health retries and preserves PostgreSQL readiness`
  prevents Docker's health retry window from hiding terminal Redis replies or
  shortening the CLI loading deadline; PostgreSQL health still gates maintenance.
- `CLI Redis readiness expires at 180 seconds and records the real loading reply without migration startup or publication`
  uses a controlled monotonic clock, preserves the existing receipt, and verifies
  the failed phase and timestamp. The five named `CLI Redis readiness fails ... immediately with the actual reply`
  cases cover authentication, configuration, terminal errors containing transient
  wording, unexpected responses, and nonzero PONG.
- `CLI Redis readiness redacts terminal errors in thrown and saved failure evidence`
  protects credentials. All thirteen Node cases are included by the regular wrapper's
  existing disjoint `CLI` contract group.
- `Tempo CLI waits for persisted Redis loading before migration or application startup`
  in the regular PostgreSQL durability runner exercises strict and saved legacy
  health checks with a small RDB AOF fixture and test-only per-key load delay.
  It observes real LOADING/health results, explicitly releases the delay, and
  verifies all fixture values and authoritative PostgreSQL study history survive.

- `actual CLI interrupted fallback stops uncommitted candidate writers before rejecting an incompatible schema` kills the CLI after its schema-28-to-29 migration verifies H0 and starts all application services, before receipt publication. Blocked-update fallback uses the schema-28 receipt and unchanged dependency identities; application shutdown must precede schema rejection, without application startup, database rollback, guard replacement, or receipt replacement. The failure journal and diagnostics remain available.
- `actual CLI interrupted fallback quiesces a partially started candidate` kills the first workers/API startup group before web/engine startup. `actual CLI interrupted fallback stops mixed application identities` combines a committed API with candidate writers. `actual CLI interrupted fallback rejects application config drift despite matching immutable images` independently requires saved Compose hashes. These four unsafe recovery regressions failed against reviewed head `c89b7cb` before the fix.
- `actual CLI interrupted fallback rejects incompatible schema cleanly with no running applications` ignores stopped candidate containers and rejects without application startup. `actual CLI compatible fallback keeps committed applications running despite a stale rollout journal` preserves the no-stop/no-build/no-migration fallback despite another revision's post-start journal. `failed application identity inspection stops unconfirmed writers before database validation` makes inspection errors fail closed and prevents startup/publication.

- `deployment records fsync file contents before rename and the containing directory afterward` records real filesystem calls and their ordering on the host platform. `deployment records surface directory fsync failure and retain the renamed destination` proves fail-closed behavior and descriptor cleanup without deleting published state.
- `deployment records clean temporary files after writeFileSync failure without replacing existing state`, `deployment records clean temporary files after fsyncSync failure without replacing existing state`, and `deployment records clean temporary files after renameSync failure without replacing existing state` retain the old destination and remove only the writer's temporary file. The existing ordinary atomic replacement regression remains required.
- `CLI migration guard durability failure prevents migrations writer startup and deployment publication` uses the real runtime lifecycle, restore-verified backup, and H0 capture. Injected directory-fsync failure after the guard rename prevents migration invocation and writer startup, leaves the guard pending and writers stopped, and commits no deployment receipt. These regressions failed against PR head `2b6a5dc` before the durability fix.
- The named `CLI worker storage contract rejects ...` cases cover `foreground missing writer URL`, `foreground reader role as writer`, `foreground writer to another database`, `foreground writer to another host`, `background missing read URL`, `background reader role as reader`, `foreground SQLite fallback`, `background empty SQLite fallback variable`, `foreground missing passfile`, `background wrong passfile`, `foreground writer secret unattached`, `background wrong secret at writer passfile`, `foreground writer secret at wrong target`, `background duplicate passfile target`, `writer secret undefined globally`, `foreground public passfile mode`, `foreground wrong broker`, `background wrong broker database`, `scheduler missing broker`, `missing foreground service`, and `missing background service`. All 21 bad configurations passed validation before the fix and failed their new regressions.
- `CLI worker storage contract accepts production identities and resolved secret targets with private or omitted mode` covers relative/absolute effective passfile targets and resolved octal-string/numeric modes. `CLI worker storage contract blocks actual maintenance before any deployment command` invokes the actual CLI and proves invalid worker wiring cannot build, pull, stop, deploy, fast-forward, or replace its prior receipt.
- `CLI worker storage contract rejects foreground PGPASSWORD`, `CLI worker storage contract rejects background PGPASSWORD`, and `CLI worker storage contract rejects empty PGPASSWORD` protect the passfile authentication contract by key presence. `CLI worker storage contract rejects foreground PGHOSTADDR redirect` and `CLI worker storage contract rejects background PGSERVICE credentials` block the two additional confirmed libpq bypasses: a separate network address and service-provided password/passfile parameters. `CLI worker storage contract permits libpq defaults already pinned by the explicit DSN` keeps ordinary host/port/database/user defaults and an inactive service-file path permitted. These rejection cases failed against reviewed head `35e46e8`.
- `CLI worker storage contract rejects PGPASSWORD before mutations and keeps credentials out of output and saved state` invokes `tempo start --no-open`, requires a worker/key/passfile diagnostic without the password, rejects before build/pull/stop/up/migration/source merge, preserves the exact prior receipt, journal, and sanitized failure log, and allows no new release directory or secret-bearing state.
- `restart compares numeric PostgreSQL schema versions rather than Python source or psql formatting` protects the reported restart/schema comparison confusion.
- `CLI rejects mismatched projects volumes ports and writable reader credentials before maintenance`, `CLI rejects a foreign container attached to the registered PostgreSQL volume`, and `CLI refuses missing insecure or checkout-local secret files without exposing their values` protect installation identity, storage isolation, and private credentials.
- `release evidence requires the exact main revision and successful complete quality jobs`, `blocked updates can start only recorded immutable images with the same database schema`, and the actual CLI process cases cover complete revision-specific CI, preserved local changes, immutable fallback, upgrades, repeat starts, read-only plans, sanitized failures, and explicit failed-migration retry.
- `target maintenance lock prevents concurrent commands and recovers a dead owner without deleting another lock` and `deployment records are atomically replaced rather than appended or partially published` protect concurrency and restart bookkeeping.
- `CLI recognizes the old backup image's unused anonymous scratch volume but rejects unknown study mounts` permits the previous backup image's unused anonymous mount only at its known destination; new backup containers use temporary memory there.
- Lifecycle cases require a verified backup before migration, readiness before success, no application interruption on failed builds, and stopped consumers after backup, migration, or readiness failures.
- `actual CLI migration guard partial commits retain H0 through failed and repaired retries` proves committed versions are skipped and the original fingerprint survives retry rather than accepting mutated history.
- `actual CLI migration guard completed schema still verifies H0 before repaired startup` rejects an unresolved historical mutation with no pending migrations, retains the original backup and deployment, then permits startup only after repair.
- `actual CLI migration guard newer candidates cannot bypass H0 or retry authorization` preserves the original invariant across selected revisions and requires explicit continuation.
- `actual CLI migration guard interrupted attempts survive loss of the operation journal` kills the actual CLI immediately after durable guard persistence and before migration commits; reentry requires retry and survives a missing operation journal.
- `CLI migration guard rejects wrong database identity and incompatible continuation schemas` fails closed for another target or an older candidate, and independently blocks startup, guard resolution, and deployment publication while unresolved.
- The six `CLI PostgreSQL image major:` cases cover a standard compatible tag, registry-qualified incompatible tag, digest-pinned incompatible image, compatible tag with incompatible contents, compatible recorded fallback ID, and incompatible recorded fallback ID. Each inspects the actual immutable image without study mounts; mismatches occur before application shutdown or database startup.
- The regular disposable Docker CLI boundary commits the populated schema-16 upgrade, injects a real review mutation before verification, rejects a no-pending retry against original H0, repairs the fixture, then verifies/resolves H0 before readiness and publication. Migration-025 normalization and dependency correction remain covered.
- `actual CLI backup restores the prior running service state and retains the failed backup phase` protects service restoration and truthful diagnostics after an explicit backup failure.
- `actual CLI backup restores stopped state when database validation fails before the backup` restores initially stopped PostgreSQL and Redis even when an earlier role check rejects the operation.
- `actual CLI blocked update rejects an incomplete recorded image set before changing services` rejects damaged fallback receipts before database startup or application shutdown.
- `actual CLI detects checkout edits during image preparation before touching the running application` rechecks source after builds and preserves work edited during preparation.
- `changed dependency preparation stops writers before dependency recreation even on the same revision`, `dependency recreation failure leaves writers stopped and never publishes a deployment`, and `actual CLI dependency startup failure occurs after writer shutdown and keeps writers stopped` protect writer quiescence before PostgreSQL/Redis recreation. Compatible repeat-start cases require `--no-recreate`; failed image/source preparation preserves the running application.
- The five `CLI volume ownership:` regressions reject Redis additionally mounting PostgreSQL data, relocated Redis data, PostgreSQL substituting Redis data, defense-engine mounting PostgreSQL data, and removal of required backup storage. The real Docker stage additionally resolves and validates current product Compose read-only, including secrets/tmpfs separation.
- `actual CLI fallback corrects uncommitted or missing dependency containers before starting recorded applications` covers changed image IDs, changed Compose configuration identity, and absent containers. `actual CLI compatible fallback trusts immutable dependency IDs rather than mutable image tags` preserves the no-stop/no-build fast path. `actual CLI backup rejects mismatched dependencies without starting any recorded application` protects the explicit backup path.
- `source update refuses concurrent dirty changes immediately before fast-forward`, `source update refuses concurrent head changes immediately before fast-forward`, and `source update refuses concurrent branch changes immediately before fast-forward` use deterministic command boundaries. `actual CLI concurrent source changes block fast-forward and retain verified fallback` additionally preserves newly created notes and independently moved HEAD while preventing merge/build.
- `source update accepts a complete successful exact main run after a failed exact run`, `source update rejects exact main revisions when every eligible run fails or lacks required jobs`, `source update never accepts successful CI from another SHA branch or event`, and `source update accepts successful exact main evidence after a pending run and across workflow pages` encode acceptance of any complete successful allowed exact-main run, including pagination, without weakening required-job evidence.

`backend/tests/test_postgres_upgrade_regressions.py` additionally covers
newer/gapped history rejection, failed rollout, and read-only status with
pending migrations or missing initialization/roles. The existing legacy
import/recovery coverage remains required.

`test_postgres_cli_history_allows_migration_025_queue_bucket_normalization`
allows the intended derived-label change. The parameterized
`test_postgres_cli_history_rejects_identity_result_and_provenance_changes`
protects queue identity/order/results/admission, review outcomes/invalidation,
and receipt request/result history through explicit semantic column sets.

The regular PostgreSQL `schema_upgrade` stage invokes
`scripts/check-tempo-cli.mjs` on its own project/ports/volumes with populated
schema 16. It proves a real restore-verified backup and upgrade to current,
including migration 025 normalization of an archived `tactics` card in
`__game_tactics__` and its complete legacy `tactics` queue row to `tactic`,
with unchanged row identity/history and successful readiness/deployment commit,
preserved reviews/queue/receipt history, compatible repeat start without
rebuild/migration, actual command fallback, real container recreation on
restart, and stopped consumers after genuinely rejected PostgreSQL DDL.
It also simulates an uncommitted Redis configuration and missing PostgreSQL
container, then verifies writer shutdown before immutable fallback correction,
the restored dependency image/config identities, and unchanged study history.
`PASS Tempo CLI interrupted uncommitted application rollout quiesces writers before incompatible fallback without database rollback`
reconstructs the recovery boundary with an older schema receipt and the already
migrated, H0-verified fixture. A separate real lifecycle process is killed after
the first workers/API `up` returns with changed application Compose hashes but
matching dependency images/configuration. The actual CLI then rejects fallback,
physically stops every application service before schema checking, leaves
PostgreSQL/Redis running, preserves the old receipt and verified guard, records
failure, and retains the current ledger/history. Only the test receipt is then
restored to the legitimate current version for the remaining lifecycle checks;
no database rollback or second historical image build is involved.
The runner records ownership/diagnostics and verifies its own volume teardown.
Its read-only production Compose check also validates the API/worker PostgreSQL,
worker passfile/secret, and worker/scheduler Redis contracts. The existing
`study_durability` scenario sends foreground review,
teaching, annotation, and queue commands through Celery, then inspects their
PostgreSQL snapshot effects and unchanged identities after service recreation;
confirmed review replay produces no duplicate effect. No additional worker-write
infrastructure scenario is needed for these static configuration checks.
Full and durability gates include this stage; browser-only scopes omit it.

## Issue #77 — read-only structural prefix evaluation

`backend/tests/test_prefix_evaluation.py` runs in the regular backend suite:

- `test_issue77_current_depths_reproduce_production_graph_and_do_not_mutate_input`
- `test_issue77_black_shortening_has_hand_checked_counts_without_alias_inflation`
- `test_issue77_unselected_alias_retains_card_and_qgd_route_unchanged`
- `test_issue77_mixed_depths_and_duplicate_aliases_use_saved_depths`
- `test_issue77_custom_black_root_and_opponent_cues_count_decisions_not_plies`
- `test_issue77_chained_saved_splits_report_effective_depth_and_honor_production`
- `test_issue77_short_routes_empty_selection_and_no_learner_moves_are_distinct`
- `test_issue77_cycles_and_overlapping_roles_preserve_distinct_card_accounting`
- `test_issue77_invalid_selection_and_candidate_depths_are_actionable`
- `test_issue77_missing_or_zero_saved_depth_never_uses_global_default`
- `test_issue77_stale_graph_and_malformed_split_fail_without_partial_results`
- `test_issue77_size_limits_fail_without_truncating_source`
- `test_issue77_512_ply_line_evaluates_current_and_proposed_without_truncation` — legal knight cycles at the diagnostic ceiling reach both production graph builds intact, preserving all 512 plies and 256 learner decisions in graph steps.
- `test_issue77_513_ply_source_is_rejected_before_any_graph_build` — direct snapshots and a fail-if-called graph sentinel prove selected, unselected and empty-selection requests reject oversized source before even a normal line that sorts earlier is built; error identifies the line and 513/512 boundary without truncation.
- `test_issue77_snapshot_binds_depth_source_graph_and_split_revisions`
- `test_issue77_card_revisions_membership_and_decision_versions_fence_snapshot`

`backend/tests/test_prefix_evaluation_api.py` runs in the regular backend suite:

- `test_issue77_http_source_and_evaluation_use_typed_snapshot_contract`
- `test_issue77_invalid_wire_values_have_machine_readable_errors`
- `test_issue77_stale_snapshot_is_distinct_from_no_change_and_empty_selection`
- `test_issue77_source_change_during_calculation_rejects_entire_result`
- `test_issue77_foreground_preemption_and_deadline_return_retryable_errors`
- `test_issue77_non_postgres_product_never_returns_sample_result`
- `test_issue77_temporary_database_failure_is_retryable_without_partial_metrics`
- `test_issue77_endpoints_use_actual_reader_pool_without_writer_credentials` — source and evaluate keep the actual loader, connection helper and pool selection; only low-level pool I/O is stubbed. Reader URL present/writer URL absent failed before the repair with `TEMPO_DATABASE_WRITE_URL is missing`. Both reads retain explicitly read-only repeatable transactions and close before decoding.
- `test_issue77_loader_reads_primary_repeatable_snapshot_and_closes_before_hashing`
- `test_issue77_http_oversized_source_is_413_through_actual_loader_without_payload` — GET source and POST evaluation retain actual loading/shared source validation, with database I/O stubbed at the existing connection seam; validation runs after connection closure and both return only the 413 limit error, with no graph construction or partial source/comparison.
- `test_issue77_http_mid_calculation_preemption_returns_no_partial_metrics`
- `test_issue77_runtime_guard_classifies_only_diagnostics_as_background_query_only`
- `test_issue77_repeatable_reader_and_worker_connections_set_isolation_before_budgets` — reader-based repeats select the reader pool; authoritative worker controls retain the writer pool. Both set isolation/read-only before timeout queries.

Real PostgreSQL proof: `scripts/check_postgres_opening_segmentation.py::test_issue77_readonly_snapshot_and_foreground_concurrency` runs in the regular disposable durability rehearsal. It exercises real query-only HTTP source/current/empty/candidate reads, unchanged cards/reviews/depths/queues/graph/task/receipt state, authoritative repeatable-read transactions, idle source connections and a NOWAIT foreground review during paused traversal, source invalidation during computation, and deterministic retry after a scheduling-only change. Existing graph/split/snapshot and route-contract regressions remain required.

Reader-only deployment proof: `scripts/check_postgres_opening_segmentation.py::test_issue77_reader_only_deployed_api_evaluates_without_product_writes` runs in that same regular durability scenario. Separate maintenance setup seeds published source; real HTTP GET source and POST depth change hit the running API with normal startup guards. The runner verifies a reader URL and absent writer URL in the API container environment. Both return 200 with the expected selected/whole comparison, and the existing complete product-state snapshot remains unchanged. Health probes hold real foreground leases while benchmark consumers are paused: each diagnostic waits for admission within its unchanged ten-second bound and retries only the exact foreground rejection (with Retry-After), never database, deadline or other failures. The in-process concurrency proof remains additional evidence, not a substitute for deployment coverage.

### PR #69 — enforce HTTP evidence database admission

- `test_opening_checkpoint_http_dispatch_does_not_prepare_evidence_in_api` — real HTTP route/middleware rejects API-side source reads and preserves the original checkpoint envelope, operation key and background dispatch, with and without a client header. Both baseline cases failed on the eager source read.
- `test_background_opening_attempt_http_read_waits_for_foreground_admission` — real activity gate blocks the connection and evidence SQL behind a foreground lease; success, 404 and read-error paths release their connections and sections. All three baseline cases failed on SQL preceding admission release.
- `test_foreground_opening_attempt_http_read_does_not_self_deadlock`, `test_opening_attempt_http_closes_read_before_event_decoding` — unmarked diagnostics remain foreground/read-only; response processing occurs after the bounded read closes.
- `test_opening_evidence_http_unavailable_rejects_without_database_or_dispatch`, `test_opening_checkpoint_http_schema_rejection_precedes_dispatch` — unsupported PostgreSQL/SQLite and invalid schema fail explicitly without source reads or broker work; existing 409 detail and 503 are retained.
- `test_opening_checkpoint_payload_identity_ignores_historical_preparation` — old/new checkpoint command fingerprints match; changed checkpoint content retains a distinct fingerprint.
- `test_standalone_opening_checkpoint_reduces_outside_background_transaction`, `test_standalone_checkpoint_preparation_uses_immutable_source_instead_of_historical_manifest` — absent, valid and obsolete historical preparation remain accepted; authority is reconstructed from immutable inputs and traversal/reduction happen after the authoritative read closes.
- `test_foreground_review_http_preparation_keeps_foreground_request_lease` — evidence-aware aggregate review retains its synchronous foreground preparation and foreground command dispatch without self-admission.
- `test_opening_checkpoint_http_semantic_rejection_preserves_terminal_receipt` — real checkpoint route/preparer/gateway reject a schema-valid invalid manifest through immediate and deferred durable receipts; no publication/success receipt occurs and the structured failure replays.
- `AS-15 immediate and deferred durable checkpoint failure both quarantine and advance the evidence queue (immediate/deferred)` — real journal classifies structured immediate HTTP 409 and deferred failed receipts, retaining rejected events and advancing to the next valid journal.
- `test_postgres_opening_checkpoint_http_admission_preserves_saved_payload_replay` — actual HTTP/genuine worker/gateway with real PostgreSQL and Redis denial prove source admission, authoritative read-only 250 ms/25 ms settings, historical payload retention, unchanged-key replay, changed-content conflict, queued/retrying recovery and scheduling invariance.
- `test_postgres_opening_attempt_http_admission_preserves_foreground_diagnostics` — actual GET waits on real Redis foreground admission before PostgreSQL SQL; authoritative limits, ordered 256-event bound, missing/error cleanup, idle released connections and foreground control preserve evidence and scheduling.

Backend names run in the existing contracts, preparation and transport pytest files; the client names remain in `tests/unit/opening-evidence-background-admission.test.ts`. Real PostgreSQL names run in the existing disposable evidence rehearsal through the regular durability runner, with its runner-owned Redis enabled. Existing preparation/stale-source/restart/review atomicity, quota, recovery and recreation/backup proofs remain required.

## Independent defensive analysis pause — related #14, #43

- `test_disabled_defensive_analysis_does_not_claim_exercise_search` reproduces a defensive request being leased with analysis disabled; the original claim failed this regression before the gate.
- `test_paused_defense_keeps_shared_repertoire_recommendation_claimable` covers game-backed and coverage-backed recommendations sharing defensive requests, including stale leases and dismissed recommendations.
- `test_defensive_control_pause_resume_and_read_only_replay`, `test_defensive_pause_setting_survives_legacy_omission_and_restart`, `test_defensive_pause_migrates_existing_sqlite_without_losing_requests`, and `test_postgres_defensive_setting_preserves_omitted_enabled_value` preserve settings, queued evidence, and compatibility.
- `test_defensive_slices_pause_before_claim_and_redispatched_work_replays_once` covers all six defensive task kinds; `test_paused_defensive_celery_delivery_never_computes_or_wakes_retries` protects delivery-time gating without a retry storm.
- `test_postgres_paused_claim_uses_active_recommendation_driver_without_claiming_defensive_backlog` protects sparse recommendation selection while disabled. Existing grading/admission fixtures explicitly enable analysis so their regular coverage remains meaningful.
- `test_defensive_pause_activity_labels_recommendations_and_preserved_work` distinguishes recommendation searches from paused defensive work.
- `tests/unit/defensive-analysis-pause-regressions.test.ts` covers active cancellation/drain, recovered claims, unresolved durable releases, shared recommendation permission, ordinary game progress, single-flight/stale control polls, control outages, fatal drain cleanup, and a pending defensive report surviving restart and delivering before another claim. Existing journal recovery/report-delivery regressions remain required.
- `defensive analysis pause persists independently of defensive cards` in `settings-defense-toggle.spec.ts` protects the actual Settings save/reload workflow.
- `scripts/check_postgres_defensive_pause.py`, executed by the regular PostgreSQL durability runner, proves default-off migration, mixed-purpose claims, foreground contention, snapshot classification, restart persistence, omitted-setting preservation, and idempotent resume using its own disposable database.
- `test_pausing_engine_release_preserves_retry_budget_and_idempotent_resume` protects pause releases from consuming failure retries, including repeated release and resumed claim. The PostgreSQL proof covers the same fenced decrement.
- `test_pending_defensive_report_delivers_while_paused_and_resume_preserves_one_review` preserves completed reports while validation waits, then resumes study with one review across report replay and restart. The worker regression `a paused engine that cannot drain releases its lease without recording failure and cleans every timer` protects the cancellation watchdog from charging a job failure.
- `test_defensive_engine_control_preempts_foreground_without_waiting_for_database` protects immediate foreground preemption before the control endpoint opens any background database connection.

### PR #83 cancellation repair — delayed release accounting

- `test_delayed_defensive_release_refunds_one_claim_after_resume_and_fences_newer_lease` covers lease-only release after re-enable, shared recommendation eligibility, retained evidence/prior failure, duplicate release and old-lease replay after reclaim.
- `test_cancellation_cycles_preserve_genuine_failure_threshold_and_prior_failures` proves four cancellations consume no failure allowance and interleaved genuine failures still exhaust the original three-attempt threshold.
- `test_defensive_release_counter_never_becomes_negative` protects the floor at zero.
- `scripts/check_postgres_defensive_pause.py::verify_cancellation_retry_allowance` runs actual PostgreSQL claim/release/failure handlers in separately committed transactions, including shared recommendations, delayed releases, duplicate/superseded leases, retained reports/durable tasks/reviews, and the unchanged genuine failure threshold. Its named PASS proof is `defensive_cancel_release_after_resume_preserves_failures_and_fences_replay`.

### PR #83 cancellation repair — first stop cause and production callback delivery

- `pause near depth deadline preserves preemption through drain (drains=true/false)` fails on the original 55-second deadline overwriting a pause accepted at 54 seconds; both delayed `bestmove` and the failed-drain watchdog retain preemption with one stop and no report/timer leak.
- `depth deadline first remains timeout when a later outstanding control reports pause` keeps a genuine timeout; `old control response cannot stop the next search after the previous search completed` protects cross-search late controls. Existing synchronous-drain and single-flight tests remain required.
- `production worker routes cancellation and genuine failure outcomes` runs the actual extracted worker cycle and engine search for drained pause, fatal pause, genuine timeout and actual engine fault, checking outbound callbacks and shutdown/reuse protection.
- `fatal cancellation bounds delivery and recovers the unchanged release before claims after restart` uses the actual durable journal, hung delivery/abort and repeated cycle execution. It preserves the same payload/operation ID, forbids conflicting claims, leaves volatile delivery options outside the journal and prevents reuse of the fatal engine.
- `a pending defensive report survives pause and restart and delivers before another claim` now also runs the production cycle: report replay is preserved and never changed into a release/failure command.
- Current-main integration keeps `test_postgres_current_main_upgrade_captures_legacy_evidence_contexts` seeded at schema 29 before evidence migration 30, then applies every migration through current readiness 31 twice. Its exact history, preserved business rows, raw presentations, captured colors and no-inferred-observations assertions remain intact when another additive migration follows evidence capture.
## PR #83: global and individual activity pause provenance

- `backend/tests/test_defensive_analysis_pause.py::test_activity_global_pause_provenance_preserves_individual_pause_and_rejects_stale_resume` covers global-only/both pauses on durable tasks and engine requests, read-only projection, retained individual state after Settings re-enabling, explicit Resume, and unchanged priority.
- `test_stale_activity_resume_returns_conflict_without_clearing_individual_pause` reproduces the former HTTP 200 and lost individual pause; now both shared-control route variants reject Resume with actionable HTTP 409 before mutation.
- `test_activity_shared_recommendation_individual_resume_remains_allowed_while_defense_is_disabled`, `test_activity_unrelated_individual_pause_controls_ignore_defensive_setting`, and `test_activity_terminal_defensive_work_keeps_existing_control_rejection` retain both recommendation relations, non-defensive/game controls, and completed/failed treatment.
- `test_activity_failed_defensive_retry_retains_existing_behavior_during_global_pause` preserves SQLite's explicit Retry behavior, matching the unchanged PostgreSQL retry handler; the settings block still applies afterward. An internal Retry pause-reset option is not exposed by the activity API, whose Resume always enforces admission.
- `tests/unit/service-status-panel-regressions.test.tsx`: `settings pause explains blocking without individual Resume` and `pause provenance-only changes update controls while an individual pause stays effective` protect truthful presentation, priority controls and equality-triggered refresh. Older omitted fields remain compatible; malformed provenance is rejected.
- `tests/browser/settings-defense-toggle.spec.ts::combined defensive pauses retain individual Resume until Settings allows it` exercises actual PostgreSQL-backed backfill/control/Settings APIs and the 320px panel: no ineffective Resume, retained individual pause after enabling, explicit Resume, and no daily-card setting change.
- Disposable PostgreSQL `defensive_activity_global_and_individual_pause_provenance_stale_resume_and_restart` extends `scripts/check_postgres_defensive_pause.py` with actual activity command handlers, committed settings changes, stale-command rollback, reconnect/pause preservation, eligible shared recommendations and unrelated controls. Existing cancellation/refund proofs remain unchanged.

### Phone opening study: repertoire identity beside the board

- `Phone opening study shows repertoire identity above the board` — component and real-browser coverage at 320, 390, and 767px; title appears once, before the full-width board, with side to play. The component case failed on the original layout.
- `Phone study heading follows London-to-Ruy-Lopez card transitions` — local/offline component transition and real first-move/review workflow switch the heading with the active card. The original component case failed because identity remained below the board.
- `Phone study details keep feedback and save retries outside the disclosure`, `Phone opening study preserves the empty-state header without a stale repertoire`, `Phone study actions retain pending-burial locks` — collapsed metadata does not conceal active errors, grading, or pending-operation protection.
- `Phone study More retains restart and edit handlers and closes after selection`, `Phone opening More supports keyboard help, restart, and edit without resetting on resize` — handlers remain usable, menu focus returns, and the shared Chessground instance survives the phone/tablet breakpoint.
- `Long phone repertoire names wrap without hiding the board or overflowing` — narrow-screen identity stays readable with no page overflow.
- The shared heading's resize invalidates Chessground's cached input bounds without recreating/redrawing the board; first moves and the incorrect-move visual case exercise this boundary. Existing workspace geometry comparisons retain dimensions and all non-training vertical-position checks.
- `heading size changes clear hit-test bounds without canceling a held drag`, `phone opening identity and move input work across browser engines` — bounds invalidation leaves the board instance and drag intact, and phone input/restart works in Chromium, Firefox, and WebKit.
- `Phone repair notice retains counts, explanation, and resume outside study details` — truthful paused/issue counts, collapsed explanation, and the original repair callback remain available.
- Existing `phone 225-card offline queue reconciles to the desktop 241-card count and next card after reconnect` now asserts the compact phone count (225 → 241) while retaining the desktop count and card-identity assertions. Required CI reproduced its outdated large-counter selector with the correct 225-card value visible on the new screen.

- **Phone opening study preserves the accessible training page heading** — `tests/unit/phone-opening-study-regressions.test.tsx`: exactly one centralized H1 through normal, service-error/retry, empty/offline, and phone/tablet transitions; no stale repertoire or visible counter wrapper. `tests/browser/phone-opening-study.spec.ts` asserts the accessible H1 and repertoire H2 through 767 → 768 → 767px with the same shared board instance.
## Canonical prefix derived freshness (PR #66 follow-up)

| Regression | Regular coverage | Failure prevented |
| --- | --- | --- |
| `test_canonical_coverage_source_change_hides_complete_run_and_stale_maia` | `backend/tests/test_canonical_repertoire_prefix.py` | Complete coverage and leased Maia results surviving an authoritative source change; current recertification restores analysis. |
| `test_canonical_graph_materialization_preserves_authoritative_source_revision` | Same backend file | Graph-generated cards and links invalidating the route source they materialize. |
| `test_canonical_global_game_scope_hides_other_primary_and_null_comparisons` (newly eligible, newly ineligible, NULL primary, same prefix) | Same backend file | Another repertoire's scope change leaving matches, comparisons, decisions or primary findings current; unchanged saves invalidating game classifications. General game analysis remains visible. |
| `test_canonical_opportunity_compute_source_race_discards_then_rebuilds` | Same backend file | Publishing discovery calculations made before a source edit. |
| `test_canonical_explorer_source_race_cannot_publish_and_priority_ignores_old_run` | Same backend file | Stale Explorer candidates and coverage evidence entering discovery or priority calculations. |
| `test_canonical_coverage_scope_predicate_survives_postgres_compatibility_translation` | Same backend file | Feeding native PostgreSQL JSON operators through the SQLite SQL translator. |
| `test_canonical_explicit_generated_card_edit_promotes_source_and_clearing_revokes_route` | Same backend file | An explicit edit or clearing moves leaving generated provenance and old route certificates valid. |
| `test_canonical_graph_cleanup_preserves_independently_authored_cards` | Same backend file | Graph cleanup removing an independently saved card or its membership. |
| `test_canonical_published_introduction_priorities_hide_after_source_edit` | Same backend file | Already published introduction scores surviving a scoped source change. |
| `test_canonical_discovery_feed_counts_and_foreground_admission_hide_stale_source` | Same backend file | A stale discovery retaining feed counts or accepting a new foreground admission. |
| `test_canonical_preview_checks_authored_membership_of_a_generated_shared_card` | Same backend file | An explicitly saved membership bypassing compatibility checks because the shared card was originally graph-generated. |
| `test_canonical_introduction_scores_hide_after_another_repertoire_scope_changes` | Same backend file | Published scores retaining primary game evidence after another repertoire changes the classification universe. |
| `test_canonical_global_scope_ignores_internal_tactics_and_study_schedule_changes` | Same backend file | Unrelated tactical capture or grading invalidating opening game classification. |
| CF-1 through CF-4 | `scripts/check_postgres_canonical_freshness.py`, invoked by the regular PostgreSQL durability `background_workloads` stage | Real branch → graph stage/link/classify/cleanup → already requested coverage; source invalidation and stale Explorer/Maia heartbeats, failures and submissions; opportunity compute/publication race; full-set game classification including another primary, NULL, newly eligible/ineligible and no-op saves. Pools close between durable slices to prove restartable cursors. |
| `canonical route provenance %s keeps the live Black training card playable` | `tests/unit/desktop-queue-regressions.test.ts` | Strict queue validation dropping both authored and generated cards after adding provenance; both cases failed before the contract correction. |
| `canonical route provenance remains a validated boolean in queue transport` | `tests/unit/domain-boundary-regressions.test.tsx` | Losing numeric/boolean wire compatibility or weakening validation for the new provenance field. |

The complete browser gate exposed the queue-contract mismatch in six existing
workflows: Black training (both board interaction cases), training comparison,
FEN-only Study review, captured tactic review, and training burial. Their existing
real PostgreSQL/browser assertions remain unchanged and must pass on the corrected
candidate.

Existing foreground-contention, task-lease replay, accepted discovery, shared-card,
training-history, prefix idempotency, compatibility migration and browser cases
remain in the regular gate. These fixes do not close the broader scheduling and
fan-out work in issues #40/#41, dismissal lifecycle work in #7, or partial-run
selection policy in #8.

## Canonical prefix mutation boundaries (PR #66 review pass)

All named backend cases run in `backend/tests/test_canonical_repertoire_prefix.py`.

| Named regression | Protection |
| --- | --- |
| `test_canonical_downstream_admission_certifies_final_source_without_renewing_unrelated_routes` (branch, PGN, paste, repair) | A downstream route remains immediately reconstructable after its own authoritative write; unrelated historical anchors remain stale. |
| `test_canonical_batch_admissions_certify_every_verified_route_at_one_final_revision` (PGN, paste, repair) | All admitted routes in a batch use the final source revision, after all writes and before commit. |
| `test_canonical_sqlite_card_scope_mutation_admits_durable_game_refresh` (revise, archive) | Direct compatibility API mutations atomically schedule replacement game publications. |
| `test_canonical_game_refresh_admission_rolls_back_with_mutation_and_fences_prior_sweep` | Interrupted foreground mutations admit no refresh; a subsequent mutation resets the durable cursor and rejects the old sweep lease. |
| `test_canonical_generated_graph_materialization_does_not_admit_global_game_refresh` | Generated materialization changes neither authoritative/global scope nor global refresh admission. |
| `test_canonical_sqlite_existing_generated_replacement_promotes_only_edited_membership` | A → existing generated B becomes authored; an unrelated shared membership stays generated, and B survives cleanup. |
| `test_canonical_integrity_reconciliation_respects_specific_membership_provenance` (all four card/link combinations) | Unsupported generated memberships can be removed; authored links and cards remain available. |
| `test_canonical_unrelated_integrity_repair_preserves_authored_standalone_source` | A real unrelated guided repair preserves an authored card without a supporting saved line. |

Real PostgreSQL checks in `scripts/check_postgres_canonical_freshness.py` run in the regular Docker durability/complete CI gate: **CF-5** invokes downstream branch admission, its original coverage seed, PGN, paste and integrity replacement; **CF-6** invokes authored revise/archive, durable full refresh and real game position/comparison derivation; **CF-7** materializes A/B through graph slices, promotes the existing replacement, verifies unrelated membership provenance, and runs later graph/integrity cleanup. Pools close between every slice to prove durable continuation across process restart. CF-1–4 remain required.

`test_canonical_first_defense_collection_admits_game_refresh_and_replay_preserves_scope` in `backend/tests/test_defensive_threat_persistence.py` covers the first `__defense__` collection membership entering the existing global universe: approval atomically admits game refresh; unchanged approval replay advances neither global scope nor the refresh generation. This does not change the universe model or defensive teaching behavior.

CI's FEN-only phone study workflow failed after UTC midnight when the regular browser inherited UTC and the disposable service used New York time. `regular browser and service share the study day across UTC midnight` in `tests/unit/postgres-browser-day-regressions.test.ts` compares actual fixture configuration and both sides of the calendar boundary. `prepared study queue shares the disposable service calendar day` in `tests/browser/studies.spec.ts` proves the browser's real calendar matches the authoritative prepared queue. Regular browser timezone is pinned to the same service timezone; the existing grading assertion is unchanged.

### PR #66 boundary review: guided-session publication reconciliation

`backend/tests/test_guided_review.py` covers:

- `test_guided_review_hidden_current_get_and_submit_grade_same_finding_without_500`: canonical freshness hides the current item; GET and POST select its successor and the saved attempt references that successor.
- `test_guided_review_hidden_completed_finding_remaps_index_and_preserves_attempts`: removed completed items reduce the index while historical attempts survive.
- `test_guided_review_all_remaining_hidden_post_commits_completion_and_resume_is_not_stranded` and `test_guided_review_all_findings_hidden_get_returns_complete_session`: exhausted sessions complete durably and never dereference missing findings.
- `test_guided_review_scope_change_between_display_and_attempt_rejects_old_target`: stale displayed identities return 409 without grading another finding.

`tests/unit/guided-review-pending-regressions.test.ts` covers finding-based response validation after index remapping, direct/recovered stale rejection, and preservation of unresolved legacy move-only operations. The existing PostgreSQL command locking/receipt tests remain required; real command parity is proved by CF-9 in the disposable durability workflow.

Baseline at `f5044167c9cfbb7eb84f89e848da7f70957aa989`: the new hidden-current HTTP regression reproduced SQLite `TypeError: 'NoneType' object is not subscriptable` before production edits. The four initial boundary cases ran in 1.38s; the corrected generated-split fixture independently reproduced authored child links in 0.62s. These are failing-baseline evidence, not candidate validation.

### PR #66 boundary review: independent card and link provenance

`test_canonical_card_promotion_does_not_invalidate_generated_shared_membership` reproduces Y's unwanted source bump (0 → 1) at the reviewed head and now verifies its source revision, canonical route certificate and completed coverage remain current after X adopts the shared card. `test_canonical_card_owner_fallback_respects_explicit_membership` covers missing, generated and authored owner links. `test_canonical_structural_edit_invalidates_both_authored_shared_memberships` preserves genuine shared-source invalidation. The publication-freshness migration (now 035 after current-main integration) and SQLite compatibility triggers retain separate card/membership flags.

### PR #66 boundary review: graph cleanup uses membership provenance

`test_canonical_graph_cleanup_removes_generated_link_from_authored_shared_card` proves normal SQLite graph publication removes obsolete generated Y membership while retaining authored X/B, its queued card, and X source revision. It failed on the reviewed head because cleanup required global card provenance to be generated. `test_postgres_graph_cleanup_removes_only_obsolete_links_in_bounded_slices` now checks the in-transaction provenance recheck as well as current-graph retention and bounded continuation. CF-7 rebuilds Y through real PostgreSQL graph slices and asserts removal directly, without manual `_reconcile_derived_cards()` calls.

### PR #66 boundary review: prefix splits retain source membership provenance

`test_canonical_generated_prefix_split_preserves_membership_and_scope` splits a real graph-generated prefix with an active canonical rule and verifies both generated child cards/links, unchanged source/global scope, no new game refresh, and current route/coverage certificates. The reviewed head failed with child link provenance 1 instead of 0.

`backend/tests/test_prefix_split.py` adds `test_prefix_split_preserves_each_shared_membership_provenance` (generated, authored and mixed shared sources), `test_prefix_split_owner_without_link_inherits_card_provenance`, and `test_prefix_split_generated_input_preserves_existing_authored_children_without_source_bump`. Authored wins on child collisions and replay retains the existing split result/history.

Real PostgreSQL durability retains CF-1–7 and adds **CF-8**: invoke `cards.prefix_split.accept` through the command gateway, verify unchanged source/global generation and refresh identity, generated children and valid canonical/coverage certificates, then exercise graph cleanup's real prepare/commit boundary and stale-lease replay after foreground membership adoption. **CF-9** invokes actual guided start/attempt commands and persisted operation receipts: stale displayed target rejection, resumed/GET/graded target parity, completed-index remapping, durable exhaustion, and restarting without a stranded active session.

`guided review reloads a stale displayed finding without grading its successor` in `tests/browser/guided-review-board-restoration.spec.ts` verifies the 409 response reloads the successor and restores actual board pieces without resubmitting the prior move. The existing correct/incorrect restoration assertions and timeouts remain unchanged; attempt payload assertions additionally require the displayed finding identity.

The split membership regression also covers a globally authored card with a **generated owner membership**, both with an authored membership elsewhere and with only generated links. These two cases reproduced transient owner source bumps on `b872aa1774575e98f2cc6ef7d60d701409966ee5` (2 failed / 4 passed, 1.84s). Splitting now installs child links before restoring copied card provenance, and archives the source while its original membership flags are still present. This keeps owner fallback from briefly inventing an authoritative route. CF-8 executes both the wholly generated and mixed authored-card/generated-owner cases through the PostgreSQL command gateway; only the explicitly authored repertoire advances source/global scope and admits refresh in the mixed case.

Complete CI run 37110969049 exposed two existing browser fixture races. `Settings letter preference takes effect immediately and persists while arrows and help work` sent Home before Chessground's deferred move callback committed Builder history. The shared `playMove` fixture now waits for the application's FEN to change before returning. `guided repair previews real arrows and pieces, saves durably, and preserves study through reload and confirmation` additionally clicked Correct after its one-move card completed automatically, saving the untouched second card; this reproduced locally (1 failed / 21 passed, 56.8s). Its redundant click is removed, as in the neighboring repair test. All existing board, focus, drag, receipt and persistence assertions and browser timeouts remain unchanged.

That stricter move synchronization exposed stale board hit-test geometry when the repair-status banner translates the persistent board without changing its size. `board layout shifts refresh hit-test bounds before mouse and touch input without resetting a held piece` failed with both hit tests using the old top coordinate (1 failed, 1.01s). The board now clears cached bounds in capture listeners before Chessground's mouse/touch handlers; it does not reset position, redraw, or cancel a held drag. The regression also checks listener removal on unmount. The existing real recovery browser case continues to prove actual piece movement and held-drag preservation through repair confirmation.

Run 37112651569 passed 187 regular browser cases but exposed the complementary banner-collapse boundary at the same recovery test's final drop assertion. Confirmation removed the banner while a piece was held, translating the board underneath the cursor and resolving a different legal but incorrect repertoire move. `repair confirmation removes its message but reserves board layout until the held drop is processed` failed before the fix (1 failed, 1.04s). Repair status now reserves its measured height during held input, removes completed messages immediately, and releases that space on the frame after mouse/touch release or cancellation. The existing real browser assertions continue to require a successful drop through passive confirmation.


### PR #66 current-main integration and truthful coverage status

- `test_canonical_card_mutation_without_coverage_work_reports_actionable_recheck` (revise/archive) reproduces nonexistent queued coverage after actual card mutations; stale statistics and gaps remain hidden, guidance is actionable, and explicit recheck/refresh restores an admitted current run.
- `test_canonical_prefix_without_coverage_work_is_not_started` prevents a saved prefix alone from fabricating queued work.
- `invalidated coverage shows actionable recheck guidance instead of nonexistent queued work` renders the failed summary and guidance in the regular Repertoire component suite.
- The regular PostgreSQL canonical freshness proof checks actual authored revise/archive commands, absence of replacement coverage admission, and recovery; the canonical-prefix browser spec verifies guidance and recheck recovery at phone and desktop widths.
- Current-main integration preserves migrations 030 opening evidence and 031 defensive pause, renumbers canonical migrations to 032–034, and preserves the #70 guided repertoire repair revert. Historical repair-specific validation above remains evidence for the older candidate, not functionality restored by this update.

- `test_canonical_shared_card_owner_cleanup_never_resurrects_generated_source` exercises actual adoption, graph cleanup and subsequent revise/archive. It preserves generated Y's effective source set and revision while retaining authored X; its four cases failed before the predicates/ownership fix.
- PostgreSQL CF-7 now makes B's owner Y before X adoption, checks the actual preview source set before/after, removes Y through real graph slices, and archives B through the real command without invalidating Y. Explicit authored shared-source invalidation remains covered.
- The populated upgrade rehearsal first reaches published main schema 31 and captures its opening-evidence contexts, then upgrades to 34 twice and verifies those contexts and all existing history assertions unchanged.

- `test_canonical_last_generated_membership_cleanup_preserves_history_without_owner_resurrection` covers graph/integrity cleanup after the last generated association: preserve card provenance/reviews, retire the orphaned presentation, prevent owner fallback, and retain a legitimate unlinked authored owner and queue. The orphan graph case failed before the fix; the legitimate fallback control passed.
- PostgreSQL CF-11 uses real X adoption/deletion and Y graph cleanup, then a later archive; it preserves historical reviews without resurrecting Y ownership and retains the genuine unlinked-owner control.

- PR #66 diagnostic freshness budget: `test_background_postgres_snapshot_disables_jit_before_budgeted_classification` ensures operational PostgreSQL snapshots disable compilation in their own transaction. `scripts/check_postgres_defensive_pause.py` proves `defensive_diagnostics_canonical_freshness_classification_within_unchanged_query_budget` on real PostgreSQL, retaining the 100 ms deadline and pause classification.

- PR #66 populated PostgreSQL proof: `scripts/check_postgres_canonical_freshness.py` selects an active explicit generated membership for the clearing/promotion assertion, excluding the archived unlinked CF-2 presentation. The populated disposable gate must pass the same source-promotion assertion as the empty focused fixture.

- PR #66 Builder keyboard setup: `Builder shortcuts cancel a held piece and preserve notes, selection and splitter keys` (`tests/browser/keyboard-context.spec.ts`) waits for the initial active repertoire and editable Builder owner before beginning its real held drag. CI trace37283022925 showed the old fixture beginning at “Choose repertoire” and a later worker publication correctly invalidating the board identity. The keyboard/drag/notes/splitter assertions remain intact.

PR #72 integration browser fixtures: `iphone offline fallback excludes a conflicted card across revision and queue changes` matches main’s prepared-queue query and verifies the exact cached cards before outage. `poisoned online A becomes inspectable while B and C save and conflict retry survives reload` permits unacknowledged HTTP replay only with the same reconciliation key and identical saved result; explicit retry advances the transport sequence once. Both original conflict protections remain asserted.

PR #72/main integration: `test_postgres_opening_checkpoint_reduction_yields_to_foreground_review` and `test_postgres_opening_checkpoint_stale_preparation_preserves_foreground_completion` admit the edited 20-decision fixture in a new queue cycle, retaining the original revision origin. Real PostgreSQL/Redis proofs still require foreground completion before releasing paused reduction, idle preparation connections, bounded transactions, and harmless stale/replayed completion.

PR #72 legacy-marker compatibility (2026-10-05):
- `legacy numeric/object marker blocks clean hydration during delayed replay and reload` and `legacy numeric/object marker blocks clean hydration when replay is deferred behind another review` use actual outbox normalization and queue hydration with server `attempt_failed=false`. Each requires disabled grading inputs while unresolved and unchanged normalized marker data through reload.
- `authoritative legacy marker resolution guides a retained active attempt without replacing its identity` retains logical identity and board context while latching the exact server-confirmed guided state.
- `legacy numeric/object marker cannot guide revised or replacement content after ambiguous validation`, `modern guided markers hydrate only their exact card and revision`, and `unresolved legacy numeric/object marker also blocks a prepared offline attempt` protect replacement identity, definitive ambiguity, and the offline fallback boundary.
- `legacy numeric/object failure replay preserves its key and missing context while independent reviews save` proves original delivery identity through transient replay, no fabricated card/revision, and independent completed-result delivery.
- Browser: `legacy numeric/object guided marker blocks clean grading across reload until authoritative replay` holds real HTTP delivery, reloads, asserts disabled board/Correct/keyboard grading and no review submissions, then confirms guided hydration after authoritative replay. Existing guided marker locking, evidence/savepoint, offline-conflict and operation-status regressions remain.
### Incident: blocked update diagnostics (2026-10-04)

The Node cases in `tests/runner/tempo-cli.test.mjs` remain part of the regular
suite through the explicit disjoint `diagnostics ...` / `diagnostic safety` groups and the
existing `CLI` contract group in `tests/unit/tempo-cli-regressions.test.ts`.
`Every named CLI Node regression has exactly one nonempty regular-suite group`
protects title routing and each wrapper invocation requires a nonzero passing
case count; a successful zero-match command is not regression coverage.

- `actual CLI diagnostics explain pending exact-main verification without a deployment receipt`, `actual CLI diagnostics identify a failed required job without a deployment receipt`, and `actual CLI diagnostics report an eligible candidate without claiming deployment` separate first-deployment eligibility, fallback absence, the exact run/job and the next operator action. All three failed against base `ed654be` before the repair.
- `actual CLI diagnostics distinguish local schema debt from running API HTTP 200` reports pending source migrations and unknown immutable-image revision evidence separately from running API readiness. It also failed against the base.
- `actual CLI diagnostics retain recorded fallback while newer verification is blocked` shows recorded and actual running revisions independently; a receipt is not proof that it runs.
- `CLI verification assessment preserves earlier exact-SHA successes and ignores Pages publication`, `CLI verification assessment rejects unrelated SHA branch event and incomplete required jobs`, and `CLI verification assessment accepts success after unavailable separate-run jobs` retain the existing allowed exact-main evidence policy without demanding successful demo publication.
- `CLI verification assessment bounds a hung client and never calls incomplete evidence missing` uses controlled cancellation and a client which ignores it, proving the diagnostic deadline yields unavailable evidence without timing-threshold assertions.
- `actual CLI diagnostics preserve local facts through GitHub access rate-limit timeout and remote-main failure` preserves local schema/source output and running API health while separating unavailable evidence from failed checks.
- `actual CLI diagnostics report mixed partial and unavailable immutable image evidence` covers different app image labels, stopped/missing services, and failed image inspection without inferred revisions or lost local output.
- `actual CLI diagnostics report migration gaps and database-ahead source independently` prevents a sparse ledger or newer database from being presented as fully source-compatible.
- `actual CLI diagnostic safety preserves source guards and unavailable local ancestry without fetching` covers local edits, a personal branch, divergence, and missing remote objects without changing source or Git refs.
- `actual CLI diagnostic safety leaves source state receipts guards services and database unchanged` snapshots fixture source, registration, immutable receipt, migration guard, journal and container/database state across status/doctor/start-plan/restart-plan/migrate-plan. It rejects deployment/source-mutating commands and non-GET or background HTTP calls, and requires only the bounded, read-only ledger SELECT.
- `actual CLI diagnostic safety logs remain available without contacting GitHub` protects immediate log access when remote verification is unavailable.
- `actual CLI diagnostic safety reports receipt image and configuration drift without changing containers` distinguishes saved receipt identities from actual containers. Known image revision labels contradicting the receipt also prevent a match claim.
- `actual CLI diagnostic safety no-receipt blocked start explains preserved deployment state` preserves the nonzero exit while explaining the absence of fallback and preventing deployment/image/maintenance work.
- `actual CLI diagnostic safety unavailable schema reads retain other diagnostics and zero exit` prevents a failed bounded ledger probe from hiding source/verification/API evidence or inventing an applied schema.
- `actual CLI diagnostics retain partial immutable inspection evidence and separate receipt identity` keeps available image records from a nonzero bounded batch inspection, reports proven mixed revisions among available labels, and compares container IDs/configuration with the receipt even when revision metadata is missing. It permits no per-image deadline multiplication or container changes. It failed against product code `86aef6b` before the partial-evidence repair.

Existing source-race, dirty/diverged checkout, image/schema fallback, interrupted
rollout and migration-guard tests remain required. These injected command tests
prove diagnostic behavior and mutation boundaries; actual deployment/persistence
still requires the disposable PostgreSQL durability stage and final candidate CI.

### PR #87 review: migration-recovery diagnostic parity (2026-10-05)

All cases are in `tests/runner/tempo-cli.test.mjs`, registered through disjoint
`migration diagnostics ...` groups and the existing `CLI` contract group in
`tests/unit/tempo-cli-regressions.test.ts`.

- `actual CLI migration diagnostics applying journal without a durable guard blocks every read-only surface` and `actual CLI migration diagnostics failed migration journal without a durable guard blocks every read-only surface` cover both interrupted/failed journal shapes across status, doctor, start/restart/migrate plans and explicit retry plans. Preserve and inspect original backup/operation/history before recovery. Both reproduced false eligibility against PR head `937ac766`.
- `actual CLI migration diagnostics invalid target and database guards remain blocked even with retry` covers wrong target, database name and volume. `actual CLI migration diagnostics verified guards cannot bypass structural or target validation` covers wrong target, invalid ledger structure and an unverified backup despite a verified-state claim. Both reproduced false eligibility before repair.
- `actual CLI migration diagnostics pending original verification requires an explicit inspected retry` retains the pending guard blocker and original backup reference. `actual CLI migration diagnostics retry plan permits only an attempt while history and startup remain unresolved` distinguishes retry authorization from successful original-history verification and ordinary startup safety; retry never makes an unresolved update eligible. The retry-plan case failed before repair.
- `actual CLI migration diagnostics normal absent or verified guards retain update eligibility` preserves normal eligibility when all other requirements pass.
- Every diagnostic case asserts zero exit and unchanged journal/guard presence and bytes, receipt, original backup/checksum, database/history/services/virtual Git state, registration and source files. Calls prohibit source/image/service/maintenance mutations; HTTP is GET-only and SQL remains the bounded read-only ledger SELECT.
- `CLI migration recovery assessment preserves lifecycle validation for every original guard field` covers all original structural/identity predicates, both journal shapes, pending authorization, valid verified/absent guards and a nonmigration failure. It checks pure input preservation and lifecycle agreement without changing the policy.
- `CLI migration recovery preflight rereads guard and journal under the lock before maintenance` introduces recovery state after initial inspection. The lifecycle rejects before image work or failure-journal replacement and preserves the original evidence.

Scope: shared read-only classification and diagnostic wording only. Authoritative
lifecycle re-reading under the maintenance lock, migrations, backup/history
verification and deployment policy are unchanged. Focused CLI proof precedes the
whole affected file/wrapper; CI owns final candidate durability/complete coverage.

## Incident graph and retention timeout protection

- `test_postgres_graph_cleanup_prepares_bounded_current_pages_before_exhaustion`,
  `test_postgres_graph_cleanup_page_frontier_never_skips_third_obsolete_card`, and
  `test_postgres_graph_cleanup_current_page_checkpoints_without_finalizing_and_fences_replay`
  in `backend/tests/test_postgres_opening_graph.py` protect bounded candidate
  selection, resolved-prefix checkpointing, empty-page termination and stale leases.
- `test_postgres_priority_retention_selects_exact_stale_manifest_and_keeps_locked_rows_pending`
  in `backend/tests/test_postgres_priority_retention.py` protects exact stale
  preparation selection and locked-row eligibility without filtering a large
  protected generation, plus primary-key ordering through actual PostgreSQL SQL
  translation when a large active generation becomes stale.
- `test_postgres_graph_retention_timeout_backoff_preserves_checkpoint_and_stops_at_limit`,
  `test_postgres_other_timeouts_and_target_lock_contention_keep_existing_yield`, and
  `test_postgres_graph_timeout_superseded_generation_does_not_retry_or_fail_replacement`
  in `backend/tests/test_postgres_background_timeouts.py` protect the two targeted
  timeout retry limits, preserved phase/payload, terminal durable failure,
  supersession, and existing behavior for other handlers and lock contention.
- The regular disposable PostgreSQL workload stage runs
  `test_postgres_graph_cleanup_current_pages_restart_and_shared_tail`,
  `test_postgres_graph_cleanup_generation_replacement_rejects_stale_page`,
  `test_postgres_priority_retention_locked_stale_rows_remain_pending`,
  `test_postgres_priority_retention_skips_large_current_generation`,
  `test_postgres_priority_retention_generation_transition_is_serialized`,
  `test_postgres_priority_retention_foreground_job_lock_yields_and_replays`,
  `test_postgres_priority_retention_timeout_rolls_back_and_replays`,
  `test_postgres_transaction_timeout_preserves_checkpoint_and_uses_failure_backoff`,
  and `test_postgres_transaction_timeout_exhaustion_stops_repeated_attempts`
  in `scripts/check_postgres_graph_retention.py`. These protect large current
  generations, bounded graph frontiers, shared cards/history, contention,
  generation replacement, restart, rollback, durable retry eligibility and
  replay through the actual worker entry point. Query plans and complete
  transaction timings are recorded without exact millisecond assertions.
- `test_incident_fixture_refuses_unmarked_database_without_writes_or_cleanup`
  and `test_incident_fixture_refuses_schema_mismatch_without_writes_or_cleanup`
  in `backend/tests/test_incident_fixture_safety.py` require read-only disposable
  metadata verification before helper creation, seeding, reporting or cleanup.
- `background workload prevents scheduler claims and restores dispatch after failure`
  in `tests/runner/postgres-test-speedups.test.mjs` keeps the real scheduler paused
  with background consumers during disposable workload proofs and restores
  dispatch after failure. The regular unit gate runs this file through
  `tests/unit/postgres-test-speedups-regressions.test.ts`.

- PR #66 / #88 integration: `test_postgres_graph_bounded_cleanup_page_excludes_authored_memberships` keeps bounded raw-key progress while excluding authored membership from obsolete-card selection. Real PostgreSQL `test_postgres_graph_bounded_cleanup_preserves_authored_membership_without_step` extends the 64,000-card restart/foreground/retention rehearsal; generated fixture memberships are explicitly marked as derived, while the authored control and retained shared membership stay protected. CF-8 uses the new prepared-page shape and still proves intervening adoption and stale replay.

### PR #66 remaining review: transactional discovery state freshness

- `test_canonical_discovery_state_actions_reject_stale_scope_without_mutation` (dismiss/acknowledge/snooze × prefix/source/global invalidation) compares every stored column after actionable 409 rejection. All nine cases failed before the guard.
- `test_canonical_discovery_state_actions_preserve_current_and_inactive_contracts` retains current actions and existing missing/inactive/wrong-repertoire 404 responses.
- `test_canonical_rejected_stale_dismissal_cannot_suppress_current_republication` retains the same opportunity ID and unchanged supporting-game evidence; stale dismissal cannot be inherited. It failed before the guard.
- `test_postgres_discovery_state_actions_lock_scope_before_opportunity` proves repertoire → global scope → opportunity ordering for all three native commands. All three cases failed before the guard.
- Real PostgreSQL **CF-12**, `prove_discovery_state_action_freshness` in `scripts/check_postgres_canonical_freshness.py`, covers all nine rejection/rollback cases, current actions, unchanged-evidence republication, scope invalidation/action contention in both directions, and completed durable receipt replay after invalidation and pool reconnect. It remains in the mandatory disposable durability/CI gate; lock waits observe actual blocking PIDs.

Portable cases run in `backend/tests/test_canonical_repertoire_prefix.py`. Existing publication, admission, coverage-status and shared-membership regressions remain unchanged. This fixes stale state actions only; #7's broader dismissal transitions, #8's evidence selection and #4's umbrella requirements remain open.

### PR #66 remaining review: selected batch canonical route closure

Portable coverage in `backend/tests/test_canonical_repertoire_prefix.py`:

- `test_canonical_selected_batch_connector_admits_new_fen_continuation_in_either_order` (PGN/paste × both orders): all four cases failed with "No verified route" before production edits.
- `test_canonical_selected_batch_resolves_multi_hop_continuations` (PGN/paste) resolves a reversed three-line dependency chain.
- `test_canonical_selected_batch_rejects_disconnected_or_prefix_conflicting_routes_atomically` (PGN/paste × both failures) compares lines, metadata, annotations, depths, tasks, scope and certificates.
- `test_canonical_selected_batch_never_borrows_another_repertoires_routes` prevents selected paste routes in X from connecting Y.
- `test_canonical_selected_batch_ignores_unselected_connectors_and_stale_certificates` rejects both invalid provenance sources without mutation.
- `test_canonical_selected_batch_preserves_duplicates_and_certifies_final_source_revision` (PGN/paste) retains annotations/duplicate counts, current route reconstruction, final source revisions and stale unrelated certificates.
- `test_canonical_selected_batch_validation_uses_ranked_origins_without_persisting_certificates` (both orders) ranks an in-scope selected route over a current pre-prefix stub reaching the same position; validation itself persists nothing.

Real PostgreSQL **CF-13**, `prove_selected_batch_canonical_routes` in `scripts/check_postgres_canonical_freshness.py`, invokes actual import/paste commands and receipts. It covers both connector orders, multi-hop chains, disconnected/conflicting/stale/unselected/cross-repertoire atomic rejection, annotations and duplicate admission, final-revision certification, and completed receipt replay after later source mutation and pool reconnect. CF-1–12 and existing batch/downstream final-certification tests remain intact. No schema, stable ID, publication or segmentation policy changes.

The state-action invalidation matrix explicitly asserts exactly one stale identity per case. Source-only fixtures keep global generation current after the source write, preventing the global fence from masking a missing source check. CF-12 applies the same isolation to rejection and source-lock contention. These are fixture-only refinements; action/publication policy and production code are unchanged.
`legacy numeric/object marker cannot be cleared by a review of replacement content sharing its queue ID` protects the review acknowledgment boundary: only exact contextual markers can be cleared by that review; queue-only markers retain their original delivery and authoritative replay. Both cases failed before the repair.

`confirmed legacy numeric/object replay accepts a fresh authoritative guided queue in the same hydration` protects prompt recovery after confirmation. Both cases failed with an overly conservative initial-snapshot guard. Existing browser `reloaded prefetched guided card waits for the earlier review before marking failure` exposed this in CI and passes unchanged after the repair; unresolved markers and false/stale queue responses remain blocked.

PR #66 current-main integration (October 5, 2026): published `032_queue_attempt_origins.sql` remains byte-for-byte unchanged. Only unmerged canonical migrations move to 033–035; readiness is 35. `test_queue_origin_migration_follows_current_main_without_renumbering_published_versions` retains contiguous numbering and the exact published queue-migration assertion. The populated PostgreSQL upgrade proves 31 → 32 queue backfill before 32 → 35 canonical admission, preserving both branches’ evidence-context and history assertions. Both regression inventories, queue recovery, Redis readiness and the guided-repair revert remain intact.

The current-main queue-recovery fixtures now name `repertoire_cards(repertoire_id,card_id)` explicitly so canonical membership provenance retains its authored default. The first integrated affected-file run reproduced 39 setup errors from the older two-column INSERT; production behavior and all recovery assertions are unchanged. The same correction applies to the real PostgreSQL queue-recovery rehearsal.
# PR #66 canonical-prefix contracts

These contracts are proved by committed domain state and product reads. The
SQLite cases live in `backend/tests/test_canonical_repertoire_prefix.py`; real
PostgreSQL proofs run in the mandatory canonical freshness rehearsal inside
`make docker-durability` and the CI PostgreSQL layer.

| Contract | Named regular SQLite regression | Real PostgreSQL proof |
| --- | --- | --- |
| CP-1 deterministic route certification | `test_canonical_route_certification_preserves_best_verified_origin_for_each_source_revision` | CF-14 `prove_deterministic_route_certification` |
| CP-2 selected-batch closure | `test_canonical_selected_batch_connector_admits_new_fen_continuation_in_either_order`; `test_canonical_selected_batch_resolves_multi_hop_continuations` | CF-13 `prove_selected_batch_canonical_routes` |
| CP-3 atomic rejection | `test_canonical_selected_batch_rejects_disconnected_or_prefix_conflicting_routes_atomically` | CF-13 full rollback snapshots |
| CP-4 fail-closed freshness | `test_canonical_scope_lifecycle_hides_stale_publications_rejects_actions_and_recovers`; `test_canonical_publication_identities_advance_independently_through_product_commands` and existing isolated freshness cases | CF-1/3/4, CF-12 isolated fences, CF-15 `prove_canonical_scope_lifecycle` |
| CP-5 stale-action immutability | `test_canonical_discovery_state_actions_reject_stale_scope_without_mutation`; connected lifecycle | CF-12 nine-case matrix and both real lock-race directions; CF-15 stale rendered actions |
| CP-6 recoverability | Connected lifecycle and product-command identity matrix | CF-15 normal coverage, opportunity and game workers after recheck |
| CP-7 backend parity | Certificate persistence matrix using `tests/fixtures/canonical-prefix-routes.json` | CF-14 compares committed `fen_key`, `route_json`, `ply`, `in_scope`, `source_revision` directly against SQLite for identical inputs |

CP-1 first failed on SQLite for repeated forward visits, a later longer origin,
equal-ply arrival order and an older source write. Real PostgreSQL failed with
`ON CONFLICT DO UPDATE command cannot affect row a second time`. Storage-only
repair left four SQLite connector-order cases failing: an already accepted FEN
continuation kept origin11 despite a selected connector proving origin7. CF-13
reproduced that remaining error after CF-14 passed.

The strengthened CP-2/3 tests use the repeated connector in both PGN/paste input
orders, preserve saved starts/moves, compare durable certificates, observe
uncommitted writes from a separate reader, and snapshot rollback of lines,
annotations, depths, certificates, source/global identities and tasks. Duplicate
replay leaves certificate rows unchanged; selecting an existing independent
duplicate does not recertify its stale unique endpoint.

The lifecycle stubs only external Explorer transport/token inputs. Coverage and
opportunity status come from normal bounded workers, never manual completion.
Product-command identity tests preserve the intentional global-generation bump
on prefix/source changes; existing isolated-fence cases still prove each
individual publication identity. Same-prefix recheck preserves revisions and
normal refresh restores public coverage, discoveries and game-derived reads.

## CLI guided startup recovery (2026-10-05 user-reported diagnostic confusion)

### PR #90 local source-inspection recovery boundary

The preliminary Git observation is independent of candidate-update eligibility
and recorded immutable fallback validation. Failed probes retain unknown fields;
they cannot authorize fetching, updating source, or building candidate images.
All cases below run in the normal CLI wrapper with unchanged group deadlines.

- `actual CLI source inspection fallback survives ${probe} probe failure without candidate mutation`
  table-drives branch, HEAD, status, origin and actual process launch failures.
  Every case failed on production head `022060b` before repair. They prove
  independently checked fallback readiness, explicit previous-version/update-deferred
  wording, unchanged deployment receipt and local work, and no candidate mutation.
- `actual CLI source inspection fallback stays deferred when the failed probe recovers`
  prevents adopting newly available source during a fallback-only invocation.
- `actual CLI source inspection no-receipt startup fails closed without changing services data or source`
  failed before repair: it now explains both unavailable inspection and absent
  verified fallback without creating a receipt or changing services/data/source.
- `actual CLI source inspection migrate never substitutes the recorded fallback`
  preserves migrate's strict candidate requirement.
- `actual CLI source inspection diagnostics remain read-only for ${surface}` covers
  concise/verbose doctor, status, start/restart/migrate plans, one repair action,
  redacted Git evidence, unknown working-tree status and independent deployment,
  running identity, schema, maintenance and release evidence. Doctor failed before
  repair. `actual CLI source inspection diagnostics retain independent facts when Git cannot launch or local schema is unreadable`
  also failed before repair and protects unknown branch/HEAD/schema comparisons.
- `actual CLI source inspection safety still rejects unsafe fallback ${unsafeFallback}`
  combines source failure with unavailable immutable images, incompatible database
  schema and invalid migration guards; none may produce false readiness.
- `CLI automatic start cancels when source inspection becomes unavailable after waiting begins`
  failed before repair. Established source fencing fails closed, preserving the
  receipt and starting neither a candidate nor fallback. Existing source-drift,
  stop, installation-state and relaunch regressions remain unchanged.
- `CLI automatic start cancels on programming errors during source inspection without falling back`
  injects a one-shot internal exception into the actual waiting command and proves
  that recovery catches cannot relabel it or start the previous deployment.
- `CLI source inspection retains known facts and classifies only operational Git failures`,
  `CLI source inspection preserves programming errors and cancellation instead of permitting fallback`,
  and `CLI source inspection executor retains native process launch provenance and legacy exit behavior`
  protect partial observations and narrow failure classification without hiding bugs.
- `CLI source inspection evidence bounds probe failures and redacts repository URL credentials`
  keeps detailed probe evidence bounded and secret-safe.
- `CLI source fingerprint compares deliberate Git state and ignores diagnostic problem metadata`
  proves equality depends only on branch/HEAD/status/origin; unknown state cannot
  establish a fence. `CLI source inspection prevents candidate assessment waiting and selection mutations with unavailable probes`
  proves all preliminary failures block waiting and selection before fetch, and
  a stale expected fingerprint also blocks fetch.

`tests/runner/tempo-cli.test.mjs` runs through the regular
`tests/unit/tempo-cli-regressions.test.ts` wrapper. New groups remain disjoint,
nonempty and within their existing deadlines; no real 30-minute wait is required.

- `actual CLI diagnostics explain current blocker before historical Redis failure`
  and `actual CLI diagnostics keep technical evidence in verbose output with legacy timestamps identified`
  failed before the repair (missing direct recovery action and rejected verbose
  flag). They protect a concise answer, historical/current separation and missing
  legacy fields without inventing failure timestamps.
- `actual CLI diagnostics explain one primary next action when source and verification are blocked`,
  `actual CLI diagnostics explain safe recovery instead of ordinary start for an unfinished migration`,
  `actual CLI diagnostics explain current terminal Redis errors with one repair action and redaction`,
  and `actual CLI diagnostic safety concise and verbose modes preserve source receipts guards and services`
  and `actual CLI diagnostics explain active maintenance before treating its migration guard as a failure`
  protect blocker priority, explicit guarded retry, active maintenance, actual Redis errors, secrecy
  and read-only default/detailed diagnostics.
- `CLI pending verification` cases protect minute-spaced bounded waiting,
  pending-to-success/failed/missing/unavailable transitions, a single deadline
  when main advances, no-wait behavior and cancellation during request/sleep.
- `CLI waiting installation fence` cases protect concurrent journal, receipt,
  guard and registration changes and duplicate deployment cancellation under the
  reacquired maintenance lock.
- `CLI automatic start` / `CLI automatic migrate` cases execute the real command
  against isolated fake processes: successful waiting; timeout with/without
  compatible recorded fallback; no fallback for migrate; actual concurrent stop;
  source drift; actual SIGINT; and original state/deadline fencing through updated
  CLI relaunch. Timeout integration cases jump a controlled clock to the deadline;
  separate unit cases prove every minute boundary without expensive process loops.
- `CLI automatic start cancels a concurrent stop during initial target inspection`
  failed before the repair: startup accepted a new stop journal as its baseline
  and restarted services. The installation fence now precedes Docker and target
  inspection, preserving a concurrent stop from the beginning of startup.
- `CLI failure evidence selects Redis logs and redacts replies without unrelated API logs`
  protects actual failing-phase log selection and saved redacted evidence.
- `actual CLI blocked update reports specific GitHub causes without a fallback or leaked credentials`,
  `actual CLI diagnostics preserve actionable GitHub rate limit and access causes in default output`,
  `actual CLI diagnostics identify the missing release job in default output`,
  and `actual CLI diagnostics explain the named branch requiring preservation`
  protect specific causes and actions without requiring verbose mode. These and
  the changed-file assertion failed before the repair (5 named failures).
- `actual CLI diagnostics keep complete changed-file evidence beyond the concise preview`
  protects a bounded default file list and the complete evidence in verbose mode.

The relaunch deadline fixture controls both process clocks explicitly; it does
not assume a host uptime above thirty minutes. Interrupted fallback cases use
separate regular-suite groups with unchanged 60-second limits and full coverage.
The existing PostgreSQL upgrade plan regression retains its read-only guarantee
and recognizable plan message.
`test_tempo_cli_postgres_fallback_reports_previous_version_ready_without_applying_update`
in the regular disposable PostgreSQL durability runner checks the real installed
command's explicit fallback wording, unchanged receipt and previous-version journal.
Its prior wording assertion failed in CI before this consumer was updated.

CI also exposed a setup race in existing browser regression
`poisoned online A becomes inspectable while B and C save and conflict retry survives reload`:
the initial helper mount began flushing before review handlers were installed;
the next reload interrupted B and replayed its identical request/key. Its outbox
is now seeded only after the handlers are ready, with assertions that setup sent
no controlled reviews. Exact B/C saves and conflict reload/retry protection remain
unchanged; no application review behavior or timeout was altered.

`actual CLI read-only container inspection refreshes a disappeared transient container once`,
`actual CLI read-only container inspection refuses an unsafe survivor after a transient disappears`,
and `actual CLI read-only container inspection never hides access malformed or repeated-disappearance failures`
failed before the repair (3 named failures). A real local PostgreSQL rehearsal
exposed auto-removal between Docker's global list and inspect. Only a matching
missing-container response permits one immediate inventory refresh; complete
metadata, surviving ownership checks, read-only behavior and bounded failure
remain required. No sleep, ignored access error or empty-success substitute is used.
`CLI target safety errors name conflicting ports and service mounts without circular doctor advice`
failed before the repair and protects the exact port/service item and direct
configuration correction, without a circular instruction to run doctor again.
`CLI verification failure guidance distinguishes cancelled and timed-out jobs from software failures`
failed before the repair. It names the actual job conclusion and release-workflow
action without claiming cancellation, timeout or missing execution proves a
software defect; an ordinary failure may require software or workflow repair.

Existing exact-main CI, source preservation, Redis persisted loading,
PostgreSQL original-history/backup/migration/restart/receipt and incompatible
fallback regressions remain required. The concurrent-source regression now
cancels without restarting fallback rather than silently accepting changed work.
No live study fixture, background audit, release bypass or schema change is used.

Disposable PostgreSQL backup shutdown (October 7, 2026; related performance work #45):

- `disposable PostgreSQL backup handles INT and TERM while waiting on its sleeper`
  in `tests/runner/postgres-test-speedups.test.mjs` failed against the original
  idle command. It protects the disposable service's shell contract: install
  explicit SIGINT/SIGTERM exit traps before starting the background sleeper and
  waiting, with valid shell syntax. The existing Vitest wrapper runs it in the
  regular gate alongside unchanged runner-plan and cleanup regressions.
- The real PostgreSQL durability runner retains every lifecycle, recovery,
  restart, backup/restore, and resource-ownership assertion. Production's
  60-second shutdown allowance and recurring backup loop are unchanged.

## Ordinary PR browser selection

Browser selection only; the existing CI reliability wrapper runs these Node regressions in the regular frontend gate. Product cases and PostgreSQL durability remain unchanged.

`CI reliability planning and browser selection regressions pass in the regular suite` — `tests/unit/ci-reliability-regressions.test.ts` executes the complete Node suite and confirms the demotion and documented-count checks ran.

The retained `FEN-only study square exercise is authored enrolled and reviewed through the real workspace` case also guards small-smoke startup independence: a six-case run exposed a real `refreshing` initial projection before phone preparation, which correctly refused incomplete data. The case now waits for the real ready projection before opening its browser workflow, within the existing 60-second test budget. Its authoring, prepared storage, real grading and export assertions remain intact; all eight demoted cases are unchanged. The failed run and trace are retained as diagnosis, not timing evidence.

`repertoire limits update today's queue, persist after reload, and reset to default` — the complete repertoire-family run exposed its dependence on an unrelated product test clearing prior imports. The trace showed `segmentation-1280` still owned the shared queued cards, so filtering by the new import's repertoire yielded zero. This spec now uses the existing disposable-product fixture before its unchanged limit, failed-save/retry, reload and reset assertions. The failed family run/trace is retained; no product behavior or shared fixture was changed.

- `every current browser spec belongs to exactly one complete family`
- `reviewed source mappings reject missing families and duplicate or ambiguous paths`
- `demoted critical cases remain required by their complete browser families`
- `ordinary prose and standalone core tests select only six global browser smoke cases`
- `mapped leaf changes retain critical plus their family with regression additions`
- `shared subsystem sources select complete consumer families without unrelated families`
- `cross-cutting browser infrastructure and uncertain inputs require the complete matrix`
- `renamed and copied subsystem paths union both complete family selections`
- `complete verification and non-PR boundaries retain the full collected browser matrix`
- `browser quality rejects missing extra duplicate wrong-project and stale results`
- `documented global browser smoke count and titles match inventory and real collection`

The result-identity regression includes replacement with an unplanned ID in the same browser project, preserving result count, project totals and uniqueness. Count-only or per-project-count validation cannot satisfy this case.

- Issue #79: `test_issue79_postgres_rehearsal_coordinates_only_explicit_foreground_rejection` keeps real foreground health admission distinct from deterministic plan replay and never retries database/service failures.

## Issue #82 — Read-only decision-level prefix difficulty diagnostics

Backend regular-suite coverage (`backend/tests/test_prefix_diagnostics.py` and `test_prefix_diagnostics_api.py`):

- `test_prefix_diagnostics_matches_shadow_reducer_representative_attempts` — exact persisted observation parity with PR #69, including wrong response/reveal/correction and original timestamps.
- `test_prefix_diagnostics_assistance_and_correction_never_create_clean_recall` — all five assistance kinds and manual-failure correction retain zero clean credit; `test_prefix_diagnostics_unverified_responses_do_not_establish_coverage` excludes illegal/unverified responses from coverage.
- `test_prefix_diagnostics_partial_attempt_preserves_reached_predecessors` — earlier clean recall survives later failure; unreached successors stay unknown.
- `test_prefix_diagnostics_distinct_days_use_frozen_study_day` — repeated same-day retries count once using the original frozen timezone; `test_prefix_diagnostics_unknown_weak_and_strong_measure_coverage` distinguishes unknown from weak and strong response-day coverage even when every response fails.
- `test_prefix_diagnostics_later_assistance_preserves_reducer_clean_evidence` — later assistance does not rewrite an already clean first response, matching PR #69 exactly.
- `test_prefix_diagnostics_isolates_repertoire_color_revision_and_occurrence`; `test_prefix_diagnostics_repeated_decision_identity_keeps_occurrences_separate` — immutable presentation predicates and separate occurrence indices prevent scope/history mixing.
- `test_prefix_diagnostics_legacy_reviews_create_no_observations` — aggregate review history is never queried or decomposed.
- `test_prefix_diagnostics_bounds_history_and_closes_reads_before_projection`; `test_prefix_diagnostics_history_window_and_recent_outcomes_are_bounded`; `test_prefix_diagnostics_recent_outcomes_order_instants_not_timezone_strings` — 101-header lookahead, 100 selected attempts, at most 2,000 observation rows, 20 recent outcomes per decision, and computation after connection closure.
- PR #100: `test_prefix_diagnostics_manifest_validation_only_checks_reporting_window` — a different manifest on the excluded 101st attempt permits HTTP 200 with the newest 100-attempt window and `older_attempts_excluded=true`; a mismatch on the included 100th attempt retains HTTP 409 and its existing error. The excluded case reproduced HTTP 409 before the fix; observation reads remain limited to included attempts.
- `test_prefix_diagnostics_pagination_and_detail_reject_changed_context`; `test_prefix_diagnostics_background_admission_preserves_foreground_progress`; `test_prefix_diagnostics_connection_is_authoritative_bounded_and_admitted`; `test_prefix_diagnostics_index_migration_only_adds_read_indexes` — stale context rejection, header-independent secondary classification, PostgreSQL reader-role read-only bounded connections, and additive indexing only.

Real PostgreSQL/Redis coverage (`scripts/check_postgres_prefix_diagnostics.py`, called by the existing opening-evidence rehearsal in regular durability):

- `test_postgres_prefix_diagnostics_reducer_scope_bounds_and_foreground_admission` — 102 reducer-persisted attempts with a 100-attempt read window; exact reducer-derived expected projection; assistance, failure, correction, partial/unreached and day-based coverage; shared repertoire isolation; legacy history; original-key replay; indexed access; real Redis foreground denial before SQL; read-only repeatable-read 250ms/25ms limits; unchanged cards/queue/reviews/splits and complete shadow digest; new-revision unknown state with historical evidence preserved.

UI coverage (`tests/unit/prefix-diagnostics-regressions.test.tsx`): named `PD-82` tests protect on-demand background GETs, Unknown/Weak/Strong coverage, window disclosure, generation-bound pagination, stale payload rejection, cancellation, actionable errors, and bounded read-only contracts. `tests/browser/prefix-diagnostics.spec.ts` adds `PD-82 PostgreSQL prefix diagnostics stay read-only and show unknown evidence` at phone/desktop sizes, proving the real menu/GET workflow, no startup reads, no horizontal overflow, and unchanged queue cards.

This is a new read-only feature; there was no existing diagnostic implementation to reproduce as a failing defect. Whole-card scheduling and PR #69 capture/recovery remain unchanged. Counts are explicitly windowed, and no latency or difficulty/depth classifier is introduced.

- `test_prefix_diagnostics_reader_credentials_are_sufficient` — failed before the reader-pool repair (writer credentials are absent in the deployed API); passes with the configured PostgreSQL reader and explicit repeatable-read/read-only transaction. The initial real browser run reproduced the same missing-writer-URL error. No writer credentials were added to the API.

- Updated `test_queue_origin_migration_follows_current_main_without_renumbering_published_versions` for exact readiness version 36. Initial PR CI caught its obsolete version-35 assertion; continuity and the exact published queue-origin migration assertions remain enforced, with the new diagnostics index migration separately protected.

- The PostgreSQL foreground-admission fixture uses an actual foreground `/api/settings` database read while the diagnostic waits on a held Redis foreground lease. Its initial full-health probe correctly failed because the durability harness intentionally stops background consumers. The focused real PostgreSQL/Redis scenario passes after this fixture repair; health semantics and required durability stages are unchanged. A standalone invocation also refuses databases lacking the runner-owned disposable marker.

- `PD-82 prefix selectors distinguish identical learner moves across opponent branches` and `test_prefix_diagnostics_full_saved_route_distinguishes_identical_learner_moves` protect full saved-presentation SAN labels (including opponent replies). The missing label/contract regression failed before the display repair. Labels derive from the saved presentation after connection closure and do not reconstruct study attempts.

- `test_postgres_prefix_diagnostics_fixture_does_not_leave_eligible_routes` — diagnostics rehearsal removes only its owned active cards/repertoires so later global comparisons remain isolated; retained shadow evidence stays durable. Covered by `scripts/check_postgres_prefix_diagnostics.py` in the regular PostgreSQL evidence rehearsal.
