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
| Rust prefix differs from Python | `prefixes_match_shared_python_golden_fixtures`; `test_prefixes_match_shared_rust_golden_fixtures` |

Append every new reported issue and its test names here. All listed tests belong to the regular suites.
