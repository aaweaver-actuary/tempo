# Repertoire repair save queue

## Selected scope

The user-approved change removes the modal's submission/validation wait and lets the user queue each visible choice. The existing resolve API, atomic PostgreSQL repair command, graph publication and integrity scan remain authoritative. Server preparation time is unchanged. Engine recommendations and the pipeline reverted by PR #70 are not restored.

Plausible failures are lost responses/reloads, damaged or full browser storage, stale subsequent conflicts, asynchronous operation/task retries, premature clean-state reporting, and passive completion interrupting active study. The smallest proof is a delayed-request dialog regression, followed by bounded outbox tests and Home attempt continuity. Real PostgreSQL browser proof covers receipt/reload behavior; real held-drag proof covers board continuity. Pinned screenshots cover the added status surface. CI owns complete candidate validation, including durability and selected browser/pinned families. No local full gate is required for this scope.

## Behavior and recovery

Choices persist as independent `tempo-pending-integrity-repairs-v3:<operationId>` records before the dialog advances. Legacy v1 fingerprints encode the original payload; v1 and v2 records migrate only after all destination writes succeed. An unreadable journal remains intact and reports an actionable storage error.

Home owns recovery independently of the dialog. Each flush serves at most two repertoire heads, with one active flush and a cross-tab Web Lock where available. A request, including its response body, times out after 15 seconds; failed delivery uses 3-second exponential backoff capped at 30 seconds. A repertoire's later choices wait for its current repair to finish validation. Other repertoires continue independently. Explicit retry preserves original operation identity through ambiguous delivery and preserves the retry task command identity. Old failed/blocked observations remain pollable until the retry receipt or attempt counters advance.

Before submission, current idle evidence must still match the saved signature. Stale choices pause that repertoire and expose Review and Discard obsolete choice. A newly reviewed choice replaces only a confirmed stale choice and keeps its position in the queue. No uncertain save is discarded.

Confirmation reads use background admission; explicit writes retain their foreground command classification. Completion requires the confirmed repair task, ready current graph publication on PostgreSQL, and an idle successful integrity scan with the repaired issue absent. Other remaining issues remain visible. Completion refreshes counts and invalidates queue caches without refreshing or replacing the active training attempt, reopening the dialog or moving focus.

## Validation evidence

Base inspected and cloned: `eb42d8781fc3466db9dfea234490cd74e616fd26` (latest remote main at checkout creation). Isolated checkout `.dev-copies/repertoire-repair-queue`, branch `codex/repertoire-repair-queue`, macOS ARM64 with locked Node dependencies. Local evidence is from the dirty implementation candidate, not clean base HEAD. Named coverage is recorded in `tests/REGRESSIONS.md`.

- Baseline: the delayed-save dialog assertion failed on the unchanged implementation at the expected next-conflict assertion, 6.76 seconds Vitest. Retained in `test-results/repertoire-repair-queue/baseline.log`.
- Initial focused units: 58 passed across outbox, pending compatibility, dialog, study and board preservation files in 10.52 seconds. After adding Home attempt/focus/reply coverage, 44 passed across the four affected files in 10.70 seconds. These are iteration results, not final-candidate validation.
- Initial `make ui-file FILE=recovery.spec.ts`: 22 passed, 37.9 seconds Playwright (38.9 seconds browser stage), including real PostgreSQL receipt/reload and held-drag cases. This preceded the final selection-state/lint corrections; a fresh candidate browser run is required. Diagnostics/timings retain the owned project `tempo-pg-regressions-66260-5d0c8765`; its runner removed its containers, images and volumes in 5.33 seconds. Live study resources were untouched.
- Final focused checks, pinned visual review and current-head CI evidence are recorded in the PR delivery evidence after completion.
