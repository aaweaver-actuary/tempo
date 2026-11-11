# Aggressive deletion pass 2 — source audit

Starting current main: `7456e29a9c2164cff881074dde8cac7ea120ff1c`. Ending implementation commit: `65e61ea675110bc729d1bb385edc49968824831d`. Branch: `codex/tempo-aggressive-reduction-pass2`. The subsequent audit-only commit adds these evidence files; its tip is reported with delivery. No live installation or database was changed.

## Measured source change

| Category | Before: files / physical lines | After: files / physical lines | Line change |
| --- | ---: | ---: | ---: |
| Runtime | 1,887 / 182,082 | 1,617 / 134,735 | -47,347 |
| Tests | 759 / 200,600 | 624 / 133,104 | -67,496 |
| Docs | 420 / 93,488 | 344 / 84,971 | -8,517 |

Runtime/library source falls **26.0%**. The selected source extensions total **476,170 → 352,810 physical lines**, a reduction of **123,360**. Counts include tracked Python, TypeScript/JavaScript/MJS/CJS, Rust, HTML, SQL, CSS, shell, TOML/YAML/JSON and Markdown; binary assets and other extensions are outside this census. Files under this audit directory are measured separately and excluded from source-comparison totals. Git reports **1,041 changed paths, 14,466 additions and 139,426 deletions** for the implementation commit; rename detection and excluded extensions explain differences from the physical-line census.

The old legacy directory held **308 files /33,433 lines**, including **173 runtime Python modules /21,422 lines**. The selected native package holds **192 files /10,575 lines**, including **121 runtime Python modules /7,332 lines**; its totals include preserved SQL history. Historical table documentation moved outside runtime. This is a substantial operation/feature reduction and native SQL port, rather than a package-name replacement.

## Dependency graph

| Runtime Python graph | Before | After |
| --- | ---: | ---: |
| Modules | 569 | 352 |
| Static import edges | 1,794 | 857 |
| Cyclic components | 8 | 2 |
| Largest cyclic component | 32 | 10 |
| Storage → application edges | 65 | 3 |
| Imports of retired legacy namespace | 404 | 0 |

The graph includes function-local imports and literal dynamic imports and normalizes package `src` roots. Computed imports and the JavaScript dependency graph are not resolved. It is a source-coupling measure, not a runtime deadlock claim.

The remaining largest component is entirely application-owned: card deletion, queue commands/settings, position admission, queue selection/application, tactical catalog, position checkpoint, grade effects and immutable issuance. No SQL storage module remains in that component. The three remaining storage reverse imports are pure frozen-order facts, scheduling facts and first-issuance receipt DTOs; storage calls no high-level application orchestrator.

## Exact removed closures and replacement behavior

- **Recombination:** application/policy/models/ports, the 1,451-line adapter, generated split/join preparation, queue projection/currentness and review hooks, and exclusive tests. Existing generated exercises become frozen ordinary exercises; content, FSRS state, review/attempt identities and lineage rows remain. Historical membership excludes them from reset/fresh introduction accounting.
- **Automatic Games-to-Study:** feedback coordinator, qualification, producers, delivery/retry/maintenance, first-boundary/priority composition, providers/acquisition/sync/refresh, Study receiver/remediation adapters, collector service/operator, and exclusive wire contracts/libraries/tests. Existing effects and operations are paused; applied grades/history remain stored.
- **Legacy database runtime:** complete `tempo_legacy_database` tree, generic capability protocols, alternate-driver branches, SQLite transaction/UDF compatibility, `sqlglot`, translator warming, and database-to-application workflow calls. Necessary SQL executes natively in selected concrete `tempo_storage` modules. Application owners now prepare position sources, issue immutable plans and decide grade effects.
- **Optional framework:** dynamic worker resolvers, feedback startup hooks, multi-class scheduler-turn framework, dead next-card preparation callbacks/endpoint/client protocol and semaphore, old annotated-Studies authoring, advanced repertoire proposal/audit UI and storage, unused diagnostics/evaluation/asset tooling and dependency registrations.
- **Minimal Game Review replacement:** one pasted PGN or existing saved game; legal replay; current authored alternatives; first departure, coverage end or unavailable source. The screen has no Study effects, provider refresh or engine. Pasted PGNs are compared without storing new game records.

Existing repertoire editing/import, integrity/graph preparation, current position scheduling, Tactics capture/catalog/solve, human review receipts, durable first-response events and ordinary queue recovery remain. The original checkpoint POST/command and a narrow saved-attempt GET remain for browser recovery. Retired provider/analysis/discovery/defense settings are omitted from ordinary writes; their stored values are untouched.

The old position-owner seeding/operator tooling is removed along with obsolete cutover machinery. Existing owner modes, schedules and seed evidence remain stored. Fresh owner activation/roll-forward is outside this candidate's verified path and must be addressed explicitly before a rollout that requires it.

## State and migration disposition

**Schema 64 is required before installation.** Migration 064 adds `tempo_retired_work`, an inspection ledger containing original pending operation/task/job/delivery/effect rows and generated presentation records. It does not update/delete those source rows, reset progress, drop tables, or run a replacement queue. Retired commands are excluded from automatic recovery and pending receipt reads return a truthful non-retryable paused disposition.

Historical 001–063 migration files remain byte-identical to starting main. Schema 050's formerly installed-distribution SQL is bundled separately with its original checksum. The guarded CLI admits 63→64 and rejects a still-running retired collector. Receipts requiring an old optional Games overlay fail closed for recovery through their original release. Backup and destructive historical migration guards remain intact.

No production migration, deployment, merge, new data deletion or historical-state cleanup was performed.

## Verification and limits

Passed: Python compilation and API/worker imports; internal source-link audit(**0 unresolved links**); **94 focused Python tests**; separate retained extraction checks(**20 passed,4 native cases skipped**); **53 frontend tests**; TypeScript; local frontend build; migration inventory/checksums; static/injected native SQL inspection; **7 selected guarded CLI tests**; whitespace checks. The build leaves 12 Merida SVG paths for runtime resolution; actual browser availability was not checked.

Broad backend collection is **not passing**:1,246 tests collected before stopping at10 errors, including missing `grimp` and remaining suites for retired source/shape contracts. Earlier stale imports and retired protocol expectations were repaired or removed only where their feature had been deleted. Native PostgreSQL query/lock/rollback/concurrency/migration proof, the assembled saved-review→playable-next-card journey, real browser/offline failure injection, full CI and Docker lifecycle qualification remain unperformed. Source/component results do not establish merge qualification, installation or activation.

Removing round-robin task turns trades fairness for direct bounded priority claims: continuously higher-priority work can delay graph/integrity tasks. This is a real simplification tradeoff. Required leases, retry identities, per-work pause/promotion, Study-only admission and explicit-import scope remain.

## Remaining coupling ranked by expected deletion value

1. **Retained queue/review application cycle(10 modules):** highest remaining consolidation value, but each member serves retained transactions. Further surgery requires native saved-review/attempt race proof, rather than another optional-feature closure deletion.
2. **Authored graph/position-source preparation and physical presentation mapping:** substantial retained complexity; removing it requires a decision about how existing cards and immutable attempts map to authored positions and owner activation.
3. **Frozen introduction-order storage and pure policy facades:** smaller, low-risk consolidation opportunity. Existing frozen ranks and exposure accounting must survive.
4. **Old test/harness and historical documentation cleanup:** lower product value; broad collection debt is explicit. No remaining retired runtime family comparable to recombination or automatic feedback was identified.

## Inspect and reproduce

- `baseline.json` and `final.json`: every counted source path, physical line count, graph edges and component membership, pinned to the commits above.
- `paths.csv`: every deleted, added, renamed or materially changed implementation path, with a reason classification and both sides of renames. Audit-only additions are listed separately in this file.
- `verification.json`: scoped check results, failures, unavailable proofs and publication rejection.
- `summary.json`: compact before/after metrics.
- `tools/reduction/audit.py` and `path_audit.py`: reproduce measurements and path dispositions from Git refs; `check_links.py` validates current internal source links.

## Publication status

The branch and implementation commits are complete locally. A normal Git push was rejected by automatic approval review because it judged authorization to transmit the potentially private repository and create the GitHub branch insufficient, and did not consider the destination verified. No alternate transport or indirect publication was attempted after rejection. No PR has been created by this task. The concrete branch is ready for approval of publication to `aaweaver-actuary/tempo`.
