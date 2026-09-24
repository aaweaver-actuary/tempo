# Reported issues and regression coverage

| Issue | Required regression |
| --- | --- |
| Browser Stockfish timeout loses whole game scan and labels engine failure as save failure | `test_stockfish_timeout_resumes_at_unfinished_position_without_saving_partial_game`; `test_docker_game_scan_finalizes_complete_positions_once_with_actual_network`; `test_docker_game_report_rejects_wrong_history_and_depth_without_advancing`; `test_browser_activity_preempts_docker_search_without_database_access`; `test_legacy_browser_network_is_requeued_one_game_at_a_time_without_erasing_analysis` |
| An already-open browser tab can keep claiming full-game scans after the Docker worker is deployed | `test_predeployment_browser_tab_cannot_claim_new_game_analysis_after_docker_rollout` |
| Defensive recognition asks for four unmarked squares at once and hides useful feedback until the move | `guided defensive recognition reveals board arrows only after the assessment`; `validated false alarm ends after explanation without requesting a move`; `test_guided_recognition_reveals_route_after_assessment_and_sound_defense_pass` |
| Discoveries cram several positions into a narrow tray and a coverage gap shows the opponent's board | `discoveries badge waits for a safe break and does not interrupt twice`; `discoveries tray loads later pages without losing the first page`; `missing-response viewer shows the learner board after the reply and selects one move`; `discovery viewer compares one learner decision and returns from Builder` (phone and desktop); `test_coverage_discovery_prepares_learner_decision_after_uncovered_reply` |
| GitHub issue #4: repertoire opportunities must promote a proven weak descendant without fabricating study, surface practical missing replies, and retain post-gap evidence | `test_issue4_strong_route_weak_target_promotes_without_reviews_or_parent_maturity`; `test_issue4_one_game_and_successful_unstudied_decision_do_not_promote`; `test_issue4_transposed_game_routes_aggregate_at_one_canonical_target`; `test_issue4_dismissed_unchanged_opportunity_stays_dismissed_until_material_games`; `test_issue4_personal_common_move_surfaces_without_masters_or_cohort_data`; `test_issue4_post_gap_finding_reuses_existing_card_and_api_lists_it`; `test_issue4_game_finding_records_canonical_opponent_gap_and_engine_consequence`; `test_issue4_covered_and_low_probability_replies_do_not_create_noise`; `test_issue4_background_scan_yields_to_foreground_and_replays_without_duplication`; `issue 4 opportunities explain promotion, degraded sources, and explicit actions` |
| Played captures/checks sound differently by actor, or the checked king has no persistent board cue | `played moves use distinct move, capture, and check recordings and respect persisted sound settings`; `tactic Show Move and Restart retain the guided attempt and classify the opponent reply sound`; `checked king gets a persistent translucent red cue that clears when the position changes` |
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
| Later reviews reveal teaching arrows | `tomorrow and later opening reviews remain unassisted even when teaching storage is empty`; `test_study_reinforces_today_reviews_tomorrow_and_persists_unassisted_later_reviews` |
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
| Defensive card grades a future rook square while showing the earlier board (reported 36.Rc4? Ne3+) | `test_reported_rc4_fork_requires_preview_with_rook_on_c4`; `test_defensive_recognition_holds_forks_beyond_the_immediate_reply`; `test_reported_rc4_preview_grades_c4_only_after_proposed_move`; `test_reported_rc4_audit_preserves_attempts_and_removes_invalid_review_effect`; `test_defensive_preview_queue_blocks_unteachable_card_without_erasing_history`; `test_defensive_rubric_audit_yields_to_foreground_and_replays_after_restart`; `test_defensive_auto_admission_holds_legacy_fork_without_immediate_preview`; `reported Rc4 recognition uses the after-move rook square with keyboard and touch`; `reported Rc4 defensive preview stays readable and returns to the decision at 390px` and `1440px` |
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
- Browser cache growth remains bounded without losing static offline recovery — `workspace persistence rejects oversized API responses and bounds total bytes`; `Pages service worker caches scoped static assets and bypasses API and external GETs`.
- Newly encountered replies lose to generic forecasts, or likely unseen replies are ignored — `test_recently_encountered_reply_can_outrank_public_forecast_for_tomorrow`; `test_unseen_likely_reply_outranks_unseen_rare_reply_for_tomorrow`.
- An in-progress coverage refresh replaces reliable evidence with partial values — `test_priority_refresh_uses_last_completed_coverage_until_replacement_is_complete`.
- Tomorrow's admission waits on pending scoring, ignores the last publication, or displaces due reviews — `test_next_day_admits_from_last_published_scores_without_delaying_due_review`.
- Stockfish timeout erases opponent-move evidence or implies successful gap analysis — `test_stockfish_timeout_preserves_personal_priority_evidence_and_remains_retryable`.
- Recurring SQLite contention and false-empty workspace projections — `test_concurrent_queue_progress_repertoire_and_settings_reads_never_return_busy`; `test_workspace_reads_complete_under_one_second_during_full_background_backlog`; `test_get_endpoints_are_query_only`.
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

- Adaptive cohort fields make “Check coverage” reject a valid response — `test_coverage_summary_accepts_adaptive_cohort_settings_without_diagnostic`; `check coverage loads adaptive settings without a validation alert`.
- Fractional weighted personal-game evidence invalidates repertoire records — `test_fractional_personal_game_evidence_is_valid_repertoire_data`; `test_workspace_validation_does_not_drop_fractional_priority_records`; `fractional priority evidence loads both repertoires without a diagnostic`.
- Whole-repertoire repair status quarantines safe cards — `test_integrity_scan_blocks_only_cards_crossing_unresolved_positions`; `test_unaffected_due_reviews_remain_playable_when_repertoire_needs_repair`; `unaffected opening reviews remain mixed with tactics during repertoire repair`.
- Blocked cards are hidden inside playable due totals — `test_blocked_opening_cards_are_reported_but_not_counted_as_playable`.
- Queue refresh rewrites history or duplicates recovered work — `test_queue_refresh_never_resurrects_completed_attempts`; `test_same_day_repair_requeues_newly_unblocked_due_cards_once`.
- An obsolete integrity scan overwrites newer published blocks — `test_integrity_block_publication_is_generation_guarded`.
- Guided repair chooses a move without explicit user input — `test_guided_repair_never_auto_selects_a_move`.
- Repair traversal holds SQLite or migrates mastery to changed content — `test_repair_worker_holds_no_sqlite_connection_during_chess_traversal`; `test_repair_preserves_unchanged_card_reviews_and_archives_changed_history`.
