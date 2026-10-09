# Training position handoff to Builder

Training's position actions carry the displayed FEN and its view cursor. This is
separate from the authoritative attempt step: browsing backward must not advance,
grade or rewind the attempt. Builder and Analysis replay the complete saved card
route; the current cursor determines the board and the Builder draft anchor.
Games and Compare use the same displayed context.

Opening cards can begin inside an authored line. A version-1 Builder session may
therefore contain optional `trainingRouteToResolve` provenance (repertoire, card
and revision). Builder uses the existing study worker and repertoire-lines API to
find exact occurrences of the card's starting position whose continuation matches
all card moves. It deduplicates identical earlier routes, restores a unique prefix,
or asks the user to choose between distinct earlier move orders. Approximate
position matches cannot establish route identity. Original custom-FEN roots are
retained. FEN move counters follow the selected authored route; board placement,
turn, castling and en-passant state stay the same.

Restoration includes only the complete card continuation, not extra downstream
branches beyond the training card. The resolved root and history replace pending
context atomically in the saved session. Cursor, keyboard reset anchor and draft
anchor gain the prefix length. Stale/unmounted lookups cannot publish. Pending
sessions survive reload and restart the read-only lookup. Failure or no exact
route retains the card board/history and an actionable retry, with branch writes
and board edits blocked until route identity is established.

Branch submission uses moves through the current cursor and the restored root.
Future saved moves do not count as played draft moves; saving at or before the
draft anchor cannot submit. Playing earlier than the launch anchor starts the
draft at that earlier cursor. The existing command, receipt and persistence
contracts are unchanged. No database migrations or training grading changes.

## Development validation

Isolated checkout: `.dev-copies/training-builder-line-context`; branch
`codex/training-builder-line-context`; initial main
`d5394b1fa29a00c104efb946fbbc46f2975c4079`. macOS ARM64, Node 26.10.0.
All initial focused executions used that base plus the documented source/test
patch, not clean main. CI owns required final current-candidate verification.

- Failing baseline: `npm run test:unit -- tests/unit/training-builder-handoff-regressions.test.tsx`, 2 failed, 6.55 s Vitest duration. The real Home handoff produced empty history/cursor zero and used the live rather than historical board. No product changes were present.
- Focused final patch: `npm run test:unit -- tests/unit/training-builder-handoff-regressions.test.tsx tests/unit/training-builder-route-regressions.test.ts tests/unit/training-builder-context-regressions.test.tsx tests/unit/builder-regressions.test.tsx tests/unit/builder-performance-regressions.test.tsx tests/unit/study-position-store-regressions.test.ts tests/unit/shared-board-shell-training-regressions.test.tsx tests/unit/study-worker-regressions.test.ts tests/unit/study-worker-coalescing-regressions.test.ts`: 40 passed / 9 files, 14.37 s Vitest, 15.64 s wall. Named coverage is in `tests/REGRESSIONS.md`.
- `npm run typecheck`: passed after adding browser scenarios, 32.76 s wall.
- `npm run lint`: passed, zero errors / 10 existing warnings, 44.49 s wall. No new warning suppressions.
- `make plan`: full coverage inspected without running the complete gate locally. `git diff --check`: clean.

- `npm run test:unit -- tests/unit/validated-data-regressions.test.ts tests/unit/study-position-index-regressions.test.tsx`: 18 passed / 2 files, 1.13 s Vitest / 1.60 s wall on clean `bcebdbf4`.
- `make ui-file FILE=workspace-flows.spec.ts` (elevated): 8 passed, 49.1 s browser / 94.33 s total wall on clean `b4fd4eab`. This includes real piece geometry, navigation, explicit Bg4 branch persistence/reload and original-line preservation, plus the partial-card chooser at 390/1470 px. Both new chooser screenshots were inspected. Project `tempo-pg-regressions-55041-c55b8187` cleaned up successfully in 8.47 s; exact container/image identifiers, creation/start times and teardown are retained in `test-results/tempo-cli/tempo-pg-regressions-55041-c55b8187/ownership.json`.
- Additional handoff assertions: `npm run test:unit -- tests/unit/training-builder-handoff-regressions.test.tsx`: 4 passed, 3.13 s Vitest / 3.65 s wall on `b4fd4eab` plus only the new unit assertions. Games now proves the historical filter; comparison proves its complete source history and unchanged attempt.

PostgreSQL durability and pinned rendering evidence are pending. Existing
other-task disposable Docker runs are inspected before scheduling validation;
the live study stack and those task resources are never reused or modified.
Raw focused logs and the test plan are retained in this checkout under
`test-results/training-builder/`. These executions are focused evidence, not a
full gate or comparative performance measurement. PR/CI evidence will identify
the actual candidate revision; no old-run result is attributed to a new candidate.
