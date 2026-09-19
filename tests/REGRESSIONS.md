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
| Chess.com mixed-case usernames and shared Site headers collapse games | `test_chesscom_mixed_case_username_and_shared_site_header_import_every_distinct_game` |
| One provider failure blocks the other account | `test_one_provider_failure_does_not_discard_other_provider_success` |
| Incremental sync misses late games or creates duplicates | `test_incremental_overlap_catches_late_games_without_duplicates` |
| Sync silently hides filtered, rejected, and duplicate records | `test_sync_reports_filtered_rejected_and_duplicate_counts` |
| Game analysis stops on navigation or submits twice after reload | `test_background_game_analysis_resumes_after_reload_and_submits_once` |
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
| Exact transpositions do not offer the played route | `exact transposition banner adds the played route as a persisted branch without waiting for Maia`; `transposition dismissal survives Builder remount and existing routes never prompt`; `Builder exact transposition saves the played route and does not prompt for a covered route` |
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
