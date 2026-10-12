# Issue #204 A1 implementation handoff

[PR #226](https://github.com/aaweaver-actuary/tempo/pull/226) is ready for review, not merged. [Ready-state FULL CI](https://github.com/aaweaver-actuary/tempo/actions/runs/38284546174) succeeded; GitHub reports CLEAN / MERGEABLE. The captured plan has draft=false and scope=complete. All mandatory layers and quality/merge-gate passed. Optional fast, quarantine, development and deployment jobs were excluded as planned. Superseded runs are not qualification evidence.

## Exact revisions

- Initial source main: `3f7332399796366eb32f9fcb5e1b5c678a5b1562`.
- Final base/current main: `25363aa0bc7073646ff8a00b94857d4f3127f694` (#191 merged).
- Final head: `44955a4bce6ccb2a5ac914fc03cd05a5aa220063`.
- Captured ready integration: `76b66d0ed3bbb3b5c2c5fb9b6d8d952343826e91`.
- A1 commits: `d26ef448dd5b4791a37d0d1f4c765574ebad9061`, `8200e096d6a2b3475dba34f55c7ff381785be0a0`; final merge preserves both regression registries.
- Evidence ref: `codex/issue-204-scheduling-evidence`. This branch contains diagnostics only and is not the feature PR.

## Implementation and preserved behavior

`SchedulingReviewStore(Protocol)` provides exactly source_review_exists, load_card_state, append_review, load_maturity_facts, save_card_state. Frozen internal records and explicit completion clock/date, source, history and seed facts live in `scheduling_review_application.py`. `OwnedConnectionSchedulingReviewStore` in `scheduling_review_store.py` translates the existing SQL over the caller's owned connection without opening, closing, committing, rolling back or managing transactions.

The existing wrapper signature and dictionary response are unchanged. Source replay reads the current card (including inactive replay behavior), skips history/save/follow-ups and stays distinct from logical and operation receipts. Fresh execution retains schedule → history → valid maturity/seed → save → same-connection follow-ups. Invalid-input/missing-card behavior, history fields, timestamp/ID ordering and FSRS results remain compatible.

Gateway transaction/savepoint and lock order, immutable identity, evidence completion, prepared continuation, post-commit publication/wakes and queue effects remain with their original owners. No API/schema/receipt store changes. `main.py`, gateway, review commands, queue helpers and pure scheduler are unchanged by A1. #220 keeps queue ownership. **#224 remains separate and incomplete**; review_commands → main._apply_review remains. #146 is still open/conflicting at 6ce1622d6974be99278d6fe230c8790b1e81d45b; no code transplanted. #191 is merged and included in final qualification.

## Tests and results

Six requested named regressions plus the fresh-interpreter infrastructure import guard are registered in tests/REGRESSIONS.md. Fake-store and SQLite cases cover order, original clocks, complete results, seed/history combinations, archived/missing cards, errors and borrowed owner rollback. Two native cases run through the regular queue-attempt durability hook.

- Original wrapper characterization: 5 passed / 0.55s pytest before product replacement. Complete original/new SQLite parity: 24 cases / 0.84s wall, including full card FSRS JSON, history and results.
- New files: 23 passed / 0.88s. Affected Python selection: 218 passed / 61.08s; final import-guard/caller selection: 81 passed / 12.91s (13.61s wall). Selections overlap; counts are not a distinct-total claim.
- Runner tests: 67 passed / 2.655s. Compilation and diff check passed.
- Focused native final head before main integration: both new rollback/concurrent proofs and existing continuation contention proofs passed / 31.22s wall. Real PostgreSQL logical/final receipt failure rollback, visible lock contention, identical/conflicting delivery, connection restart, uncertain-response replay and exactly-once history/queue/snapshot effects verified.
- Local elevated browsers: post-review-progression 3 passed / 256.42s wall; training-critical-path 1 passed / 128.38s; phone-offline-training 15 passed / 74.29s.
- Ready FULL CI: backend 2586 passed / 334.61s command; frontend 1643 passed / 284.34s unit command plus lint/typecheck; browser 283 passed across four fresh shards and aggregate; pinned visual/performance 69 passed / 277.13s command. Build/Rust/WASM and lifecycle passed (lifecycle 301.91s command).
- Complete native durability passed / 621.13s command, explicitly executing both new A1 proofs. Existing command recreation (45.88s), backup/restore (31.11s), study durability (91.69s), evidence and prepared-continuation/reconciliation proofs remain in the regular gate. Scenario durations are nested, not additional wall time. Full workflow wall including queue: 1016s.

Commands, inventories, logs, per-layer results, resource IDs/teardown and environments are in COMMANDS.md, qualification.json, ci-ready-plan/, ci-ready-results/ and native-provenance files. Local: macOS arm64 / Node26.10.0 / CPython3.14.8. Native: PG18.6/Redis7/Python3.12. CI: Ubuntu24.04arm / Node22.23.3 / Python3.12.14 / Rust1.99.0 / wasm-pack0.15.0.

## Limits and failure diagnostics

The first local complete durability run failed before A1 in unchanged refresh-state recovery with a priority-source lock timeout; the isolated pinned-main case passed. Cause remains unproven; it is not labeled flaky or a baseline failure. Both subsequent complete CI candidates passed native durability. The first new native test had an incorrect absent-operation expectation, corrected to the existing unknown response plus direct no-row verification; product behavior and existing test expectations were unchanged. All failures are preserved.

No full local gate was run concurrently with CI. SQLite/no-service tests do not prove PostgreSQL locking; native/complete CI do. Focused restart reopens connections; complete CI supplies process recreation/restore. No universal deadlock or performance-improvement claim. No live deployment or merge performed.

## Rollback and cleanup

Revert the A1 extraction commits while preserving the #191 main integration; no schema or data conversion is required. Existing history, receipts and unresolved intents remain readable.

All seven local task-owned projects have no remaining containers, volumes, networks or tagged images, including maintenance images. Shared bases/caches and live/other task resources were retained. Both isolated clones passed preservation/deletion gates and were removed; 1396796 KiB (about 1.33 GiB) of checkout storage reclaimed after removing the temporary Git-only evidence publisher. Complete diagnostics remain under `/Users/andy/tempo/test-results/issue-204-scheduling-store-2026-10-12` and on the dedicated evidence ref. No task checkout/resource retention blocker remains.

#204 remains open until reviewed merge; this handoff does not close #224 or claim A2 complete.
