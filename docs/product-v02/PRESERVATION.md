# Retained Study and Tactics checks

T23 / [#504](https://github.com/aaweaver-actuary/tempo/issues/504), for
[#488](https://github.com/aaweaver-actuary/tempo/issues/488) and later integrations.
Source baseline: `24ec4c06f666f50104c979840c403826fb1ce280`; product authority:
[SPEC v0.2](https://github.com/aaweaver-actuary/tempo/blob/6885b9420320517ac74df47e631ed796da96c94a/SPEC.md)
R01/R08, C04 and §8. This is an affected-boundary selection, not a new global
gate or a prerequisite for pure component work. Source coverage does not certify
installed or deployed behavior. Existing required checks remain in force.

## Capability and actual owner

Paths below are relative to the repository root; titles are literal test names.

| Retained capability | Current interface / owner | Small existing proof | Limit or missing proof |
| --- | --- | --- | --- |
| Opening completion survives uncertain save; retry uses one logical attempt | `TrainingView.rateCard` / `retryReviewSave`; `review-outbox` owns retained delivery, Study owns persistence | `tests/unit/review-outbox-regressions.test.ts`: `retries one phone review identity after an uncertain save and clears it only on confirmation`; `tests/unit/pending-review-recovery-regressions.test.tsx`: `phone review recovery confirms the original receipt after reload without another POST` | Fake transport and fresh test IndexedDB; not PostgreSQL exactly-once proof. |
| Recovery preserves an unrelated active attempt and board target | Current Home review lifecycle through TrainingView public actions | `tests/unit/review-attempt-confirmation-regressions.test.tsx`: `issue186_ordinary_reconnection_replays_saved_reviews_without_replacing_active_attempt`; `PR105 earlier idle conflict preserves an unrelated active board logical attempt and progress` | Optional-producer process/resource isolation is #488/#491's integration proof. |
| Unrelated optional load failure retains mounted Study and pending work | Full current Home catches `preloadView("tactics")` rejection; Study retains its attempt and outbox | `tests/unit/opening-evidence-home-lifecycle.test.tsx`: `optional tactics preload failure preserves the mounted Study attempt and retained review` | Deferred fake preload failure; asserts same mounted board, target, attempt, FEN/step and original retained review. Does not prove process/database resource isolation. |
| Normal board and Tactics share one presentation; phone and desktop remain usable | `TrainingView.onMove`, `TacticsView` and the shared board shell | `tests/unit/shared-board-shell-tactics-regressions.test.tsx`: `Tactics shared board publishes shell ownership and hides local board instance`; `tactics shortcuts stop before the unanswered solution and restore the live attempt`; `tests/browser/layout.spec.ts`: `application navigation retains one Chessground instance`; `tests/browser/phone-opening-study.spec.ts`: `Phone opening More supports keyboard help, restart, and edit without resetting on resize` | Unit board rendering is mocked. Browser geometry/resizing must be selected when that surface changes. |
| Legal PGN/FEN capture, setup move, retry identity | `parseChessComPuzzlePgn`; `TacticCaptureDialog` → `saveTacticCapture`; backend `capture_tactic` / `validate_tactic_line` | `tests/unit/tactic-capture-pgn.test.ts`: `Chess.com Puzzle Rush PGN advances the setup move and imports only the solver line`; `tests/unit/tactic-capture-regressions.test.tsx`: `capture transport retry and reopened dialog retain exact identity and bytes and lock edits`; `backend/tests/test_tactic_capture.py::test_captured_tactic_create_is_idempotent` and `::test_captured_tactic_rejects_invalid_fen_and_illegal_solution` | Backend fixture uses disposable SQLite, not a PostgreSQL transaction contract. |
| Personally captured tactic remains trainable with history intact | Existing capture API → current Study queue/review owner | `tests/browser/tactic-capture.spec.ts`: `FEN capture records a real-board solution and is reviewed through ordinary Training` (extended with original-ID duplicate submission, singleton queue target, ordinary solve, receipt and reload readback); `Chess.com Puzzle Rush PGN reaches ordinary Training through tactic capture`; `backend/tests/test_tactic_capture.py::test_captured_tactic_reuses_existing_tactic_without_erasing_history` | PGN case proves admission; FEN case covers the complete solve boundary. Stock puzzle availability alone does not prove captured-content parity. |
| Pack selection/activation, solve progress and Study admission | `TacticsView.onQueueChanged`; `setPackActivation`; current backend tactical catalog/admission owner | `tests/unit/tactic-activation-pending-regressions.test.ts`: `tactic activation pending command reuses its key across retry`; `tests/unit/background-workspace-regressions.test.tsx`: `real hanging-piece packs contain 100 validated playable cards after their setup move`; `backend/tests/test_tactical_catalog.py::test_deactivating_a_tactical_pack_preserves_scheduled_reviews`, `::test_practice_in_an_inactive_pack_still_admits_the_puzzle_to_reviews`, `::test_tactical_completion_counts_distinct_clean_solves_across_practice_and_training` | Static assets prove pack availability. SQLite owner semantics and actual browser/PostgreSQL behavior require their distinct checks. |
| Accepted repeated-failure prefix split stays reachable | `TrainingView.onAcceptPrefixSplit` / `onRejectPrefixSplit`; current prefix-split commands own mutation | `tests/unit/prefix-split-training-regressions.test.tsx`: `prefix split offer accepts immediately without opening a preview editor`; `tests/browser/prefix-split-training.spec.ts`: `accepting a prefix split skips preview and advances to the next queued card`; `backend/tests/test_prefix_split.py::test_prefix_split_preserves_the_complete_repertoire_line_and_original_reviews` | Offered split shortens the prefix and isolates the final learner decision; it does not target an arbitrary failed decision or prove R11 joins. |

## Runtime distinction

The `independentStudy` compile-time capability switch is defined in
`app/lib/study-connection.ts`. In that mode `training_view.tsx` hides edit/split
controls, while `tactics_view.tsx` hides capture and passes no pack-activation
callback. The independent shell can solve packaged puzzles but does not establish
R08 parity with the full current product. `study-authoring-capability-regressions`
explicitly proves unsupported authoring rejection and read-only pack selection;
those are capability restrictions, not replacement preservation evidence. Select
the actual serving owner explicitly in #488. Do not infer installation from the
existence of either source entry point.

## Minimum command selection

Run from the candidate checkout. These existing commands are defined in
`package.json`, `Makefile` and `database/tests/run-focused-postgres-browser.mjs`.
Use one Make verification target per invocation. Select only the affected rows;
there is no instruction to run this entire document on every PR.

For #488's Study review boundary, the reusable unit selection is:

```sh
npm run test:unit -- tests/unit/review-outbox-regressions.test.ts -t 'retries one phone review identity after an uncertain save and clears it only on confirmation'
npm run test:unit -- tests/unit/pending-review-recovery-regressions.test.tsx -t 'phone review recovery confirms the original receipt after reload without another POST'
npm run test:unit -- tests/unit/review-attempt-confirmation-regressions.test.tsx -t 'issue186_ordinary_reconnection_replays_saved_reviews_without_replacing_active_attempt|PR105 earlier idle conflict preserves an unrelated active board logical attempt and progress'
npm run test:unit -- tests/unit/opening-evidence-home-lifecycle.test.tsx -t 'optional tactics preload failure preserves the mounted Study attempt and retained review'
```

If touching Tactics capture/pack/shared-board wiring, select the corresponding
existing owner file, rather than copying its tests into a preservation mega-suite:

```sh
make unit-file FILE=tests/unit/shared-board-shell-tactics-regressions.test.tsx
make unit-file FILE=tests/unit/tactic-capture-pgn.test.ts
npm run test:unit -- tests/unit/tactic-capture-regressions.test.tsx -t 'capture transport retry and reopened dialog retain exact identity and bytes and lock edits'
make unit-file FILE=tests/unit/tactic-activation-pending-regressions.test.ts
npm run test:unit -- tests/unit/background-workspace-regressions.test.tsx -t 'real hanging-piece packs contain 100 validated playable cards after their setup move'
make python-file FILE=backend/tests/test_tactic_capture.py::test_captured_tactic_create_is_idempotent
make python-file FILE=backend/tests/test_tactic_capture.py::test_captured_tactic_reuses_existing_tactic_without_erasing_history
make python-file FILE=backend/tests/test_tactical_catalog.py::test_deactivating_a_tactical_pack_preserves_scheduled_reviews
```

For split changes, use `make unit-file FILE=tests/unit/prefix-split-training-regressions.test.tsx`
and the affected named backend prefix-split cases. For browser behavior, reuse:

```sh
make ui-file FILE=tactic-capture.spec.ts
make ui-file FILE=prefix-split-training.spec.ts
make view VIEW='application navigation retains one Chessground instance|Phone opening More supports keyboard help, restart, and edit without resetting on resize'
```

When only the complete capture workflow changed, `make view VIEW='FEN capture records a real-board solution and is reviewed through ordinary Training'`
selects that one case rather than the other capture-editor workflows.

`ui-file` and `view` use the disposable PostgreSQL browser runner. Product fixtures
first assert `health.test_instance === true` and an owning disposable Compose
project; their setup resets only that synthetic workspace. Retention assertions
must span the action/retry/reload within one fixture; a reset between those steps
would invalidate the proof. Never point these at live Study or clear retained
browser/server data to obtain a pass. Never use `verify:phone-deployment` as a
passive probe: queue reads can issue work.

#488 still owns its actual game/card mapping, lapse/deadline, settlement, producer
absence and unrelated active-attempt integration cases. These unit checks do not
replace them. Browser/database capability failures, skipped or zero-selected
tests are unverified, not passes. Reuse earlier evidence only with matching
executable source, contracts, fixtures, dependencies and toolchain.

## Evidence and gaps

[PR #507 validation receipt](https://github.com/aaweaver-actuary/tempo/pull/507#issuecomment-6427048504)
records exact commands and outcomes at runtime-tested revision
`c118d2e9199dcf7de94b8f489a81efc533369e27`: 6 changed-owner unit cases,
10 named existing cases, 1 real-pack availability case, 1 pure backend
corpus-preservation case, and the single PostgreSQL capture/replay/solve/reload
browser case passed. All disposable runner stages, including cleanup, passed.
The later edits to this document change no executable test inputs.

Six additional capture/history/pack/split backend cases did not collect because
the existing Python environment lacks `redis`; they remain unverified, not
passed. No dependencies were installed to repair that environment. No live
probe, deployment or live product-state change is part of this work.

T23 adds only the two uncovered boundaries named above to their existing suites:
optional-tactics-preload failure with an active Study attempt/retained review,
and duplicate capture/ordinary solve/reload readback within one disposable
browser fixture. It adds no aggregate runner, registration or runtime changes.
Actual product evidence, including #488's optional-producer shutdown and real
game/card correspondence, remains with its integration owner. Runtime fixes, if
discovered, stay with the owning issue rather than broadening this task.
