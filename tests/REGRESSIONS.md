# Reported issues and regression coverage

| Issue | Required regression |
| --- | --- |
| Failed tactic advances before correction | `tactic failure remains interactive until the full guided solution is complete` |
| Puzzle setup omitted / final move snaps back | `tactic setup is applied and the final mate remains during feedback` |
| Motifs share counters or stale timers | `motif progress is independent and stale completion cannot advance another deck` |
| Repeated clean solves unlock a stage prematurely | `clean progress counts distinct puzzle IDs` |
| Unseen tactical review reveals its answer | `unseen tactic review has no automatic teaching arrow` |
| Training repeats a completed entry / duplicate submissions | `completed queue entry is idempotent and reinforcement schedules into the future` |
| Import bypasses the new-card limit | `legacy introduced-but-unreviewed queue is capped without losing reviews` |
| One sample deletion removes both | `deletion isolates records sharing a source filename` |
| Flipping exits Black repertoire | `builder flip preserves repertoire identity and history across remounts` |
| Local app shows sample games on failure | `test_local_sync_persists_errors_and_success_without_sample_fallback`; `Docker Games shows actual empty records and actionable sync errors, never sample success` |
| Sync status invisible | `automatic game sync has a visible spinner and reports provider failure` |
| Board clipped at narrow viewport | `local import respects the daily limit; Black prompts and Builder flip survive Settings and refresh`; `wrong tactic immediately shows X, requires guided continuation, and leaves final mate during the pause` |
| Repair board / keyboard navigation inconsistent | `repair uses the shared board and arrows navigate the complete solution; Escape closes` |
| Random endgames start illegally | `random endgames never leave the nonmoving king in check` |
| Engine not usable in production | `production Stockfish returns playable engine moves without clipping the board` |
| Maia runtime/module loading errors, including production JavaScript MIME type | `Maia initializes matching runtime assets and returns legal playable probabilities` |
| Board controls fall below the viewport | Board-and-controls bounds assertions in every browser workspace regression |
| Unfinished new cards accumulate beyond tomorrow's allowance | `test_unfinished_unreviewed_cards_do_not_bypass_tomorrows_new_card_limit` |
| Explorer/Masters practical results missing from source rows | `source rows show frequency, WDL, practical score and support keyboard preview` |
| Help/reload permits a false clean solve | `test_help_failure_survives_reload_and_cannot_be_graded_as_a_clean_solve`; `help remains Again after browser reload and the returned attempt is unassisted` |
| Light admission ignores settings or reverses a lapse | `test_light_discovery_uses_settings_and_never_returns_to_light_after_a_lapse` |
| Rust prefix differs from Python | `prefixes_match_shared_python_golden_fixtures`; `test_prefixes_match_shared_rust_golden_fixtures` |

Append every new reported issue and its test names here. All listed tests belong to the regular suites.

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
| Already studied unassisted cards freeze | `readable renamed store fields retain local authority, failure, and sound through Home selectors`; `previously studied unassisted card is playable after atomic queue hydration and refresh` |
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
| Show Move/Restart abandon a failed tactic or retain stale X | `tactic Show Move and Restart retain one guided attempt then clear X and unlock the next puzzle`; `tactic help and restart preserve one failed attempt until guided completion and playable next puzzle` |
| Comparison sorting or missing values are incorrect | `comparison headers sort ascending then descending with missing values last and preserve playable hover rows`; `Builder source comparison is immediately reachable beside the board` |
| Explorer/Masters details overwhelm comparison | `compact database cells reveal WDL frequency and practical score only on demand` |
| Exact transpositions do not offer the played route | `exact transposition banner adds the played route as a persisted branch without waiting for Maia`; `transposition dismissal survives Builder remount and existing routes never prompt`; `Builder exact transposition saves the played route and does not prompt for a covered route` |
| Navigation lacks active-page semantics or visible headers waste space | `Builder source comparison is immediately reachable beside the board`; `high-DPI board geometry stays aligned through narrow resize and orientation flips` |
| Red X stays over the board during guided completion | `red board feedback disappears after one second even while the failed attempt remains active`; `tactic help and restart preserve one failed attempt until guided completion and playable next puzzle` |
| Incomplete Black opening cards auto-play the only move then freeze Train | `test_incomplete_black_prefix_is_not_created_or_queued`; `test_legacy_incomplete_black_prefix_is_quarantined_from_queue` |
| One-column Train layout collapses the board after a resize | `Black Train prompt remains playable with a fully visible narrow board` |
