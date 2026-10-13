# Prefix diagnostics qualification ownership — issue #240

## Result and limits

The diagnostics spec now owns a separate browser family. Reviewed statistics and
game-comparison consumers omit it from targeted family plans; evidence producers
select it alongside training/repertoire consumers. Shared dependencies retain broad
qualification. FAST/STUDY cannot yet execute diagnostics, so their prefix-dependent
paths escalate to FULL. This change adds no tier and changes no product behavior.

#239 owns general test/fixture classification and FAST/STUDY redesign. #240 owns
this prefix-specific inventory and guard. Coordination was recorded in
[issue #239](https://github.com/aaweaver-actuary/tempo/issues/239#issuecomment-6145327483)
and [issue #240](https://github.com/aaweaver-actuary/tempo/issues/240#issuecomment-6145327819).
Neither issue authorizes relaxing unknown, migration, persistence or browser boundaries.

## Confirmed dependencies

| Boundary | Confirmed connection | Ownership |
| --- | --- | --- |
| UI | `repertoire_view.tsx` mounts `PrefixDiagnostics` on demand; diagnostics imports its schemas, `background-fetch`, Button and Notice | Direct sources select diagnostics; shared UI/workspace sources broaden |
| Background reads | `readDiagnostics` uses `backgroundReadWhenAdmitted` with a 15,000 ms abort deadline; only exact foreground-wait 503 responses retry automatically | Shared fetch/admission changes broaden |
| Foreground admission | Home's capture-phase pointer/keyboard handlers POST browser activity; HTTP middleware classifies diagnostics as background; `activity_gate` checks Redis foreground presence before database access | Home/activity/Redis/middleware changes broaden |
| PostgreSQL | `diagnostic_read` requests reader-role, background, read-only, repeatable-read connections; pool/query/lock/transaction failures yield actionable 503s | Store, migrations and shared persistence retain FULL |
| Graph publication | `current_generation` reads `opening_graph_publications` and `background_tasks`; prefixes join published steps, cards and immutable presentation snapshots | Publication/segmentation sources select diagnostics or broaden |
| Evidence | `decision_manifest` depends on segmentation occurrences; `_manifest_snapshot` and durable attempts/observations supply projection inputs; frontend schema imports opening-evidence contracts | Manifest/contracts/reducer/journal/recovery/delivery producers select diagnostics and existing consumers |
| Queue state | Fixture settings/import/deletion schedule publication; the proof snapshots one ready response with zero pending refreshes and compares queue cards after inspection | Queue refresh/settings/import/deletion/shared workspace changes broaden |
| Browser infrastructure | Regular product fixture resets require disposable PostgreSQL; navigation, observers, configuration and runner resources control event timing and isolation | Shared harness/runtime changes retain FULL |
| Isolated consumers | Statistics presentation/query service reads data without changing diagnostics manifests; game comparison reads its own workspace/model. Read-only prefix-comparison UI/model does not publish diagnostics data | Existing consumer families; no diagnostics requirement |

This is a reviewed dependency ledger, not a certified automatic reverse-dependency
closure. Unmapped graph/task/schema/infrastructure sources still select broad coverage.
Changes to generic background work are not assumed isolated merely because their
primary feature is unrelated: shared admission or state keeps them broad.

## Timing contract findings

[Run 38380391965](https://github.com/aaweaver-actuary/tempo/actions/runs/38380391965)
(PR #228, head `ffc1454d08ec08d101e8683e02921ff50b8027fc`) failed in initial-list
foreground recovery with 843 ms left in `page.waitForResponse`.
[Run 38383767435](https://github.com/aaweaver-actuary/tempo/actions/runs/38383767435)
(PR #229) failed in detail recovery with 903 ms left. Their failed browser shard jobs
lasted 12m49s and 9m28s respectively. Both whole PRs contain shared changes: FULL
was justified, and this PR does not retroactively classify them as isolated.
PR #229 merged before this checkout was created.

The old five-second budget counted supported admission deferral and registered the
next response wait only after UI assertions. The deadline mismatch is confirmed;
a missed-response race is also possible from that ordering, but is not claimed as
the proven cause of either historical failure.

The new real-browser six-deferral detail regression failed against the old contract
with a 963 ms response timeout (19.8 s case, 21.29 s browser stage). It passed after
repair (25.9 s case). This demonstrates supported recovery, not a speedup.

Responses are now journaled before opening diagnostics. Each list/detail sequence
has one nonrenewing 15-second admission deadline; automatic deferrals cannot extend
it. This test driver also keeps explicit retries within that sequence budget, which
is conservative relative to a new product request's fresh deadline. Success starts
an independent five-second render assertion measured from response receipt. Current
loading/disabled state or an already-recorded retry satisfies synchronization; an
obsolete transient UI state is not demanded after the response arrives.

Only exact foreground-wait bodies retry automatically. Database-section/temporary
read failures require visible errors, no fabricated Unknown evidence, an enabled
button and explicit user retry. Unexpected statuses or payloads fail. Phone/desktop
read-only headers, no startup reads, unknown/no-observation labels, layout and
unchanged settled queue assertions remain. No global timeout or sleep was added.

## Before/after selection work

[Exact selected identities](issue-240-selection.json) include the pinned base and
both original and normalized comparisons. The original regular collection has 284
cases; this candidate adds four diagnostics regressions, producing 288. The normalized
columns below hold the expanded collection constant to isolate policy effects.
These are collection/planning counts, not executed-time savings.

| Changed source | Tier before → after | Targeted browser cases before → after (normalized) | Diagnostics before → after |
| --- | --- | --- | --- |
| Diagnostics component/schema | FAST → FULL | 288 → 14 | 8 → 8 |
| Statistics component | FAST → FAST | 31 → 23 | 8 → 0 |
| Game comparison helper | FAST → FAST | 19 → 19 | 0 → 0 |
| Opening-evidence schema/store | STUDY → FULL | 95 → 103 | 0 → 8 |
| Background fetch | FAST → FULL | 288 → 288 | 8 → 8 |
| Queue publication | STUDY → FULL | 288 → 288 | 8 → 8 |

FULL's actual workflow still requests the complete matrix. The 14-case direct-owner
plan includes eight diagnostics cases plus six global critical cases; it does not
mean FAST/STUDY now executes that plan. Statistics already used FAST without browser
work, so no ordinary-PR wall-time reduction is claimed for that control. This patch
closes underqualification and removes family coupling where reviewed isolation is
established. Complete nightly/release/manual/explicit FULL coverage is preserved.

## Regression and development evidence

All new names are registered in `tests/REGRESSIONS.md`. Planner cases exercise direct
owners and producers, isolated omission, union/rename/unknown/shared paths, missing
ownership/collection, exact real collection, and missing/skipped/failed/retried/stale
results. The merge gate rejects missing complete qualification for diagnostics FULL.
Four initial ownership cases failed before correction; result validation already
failed closed and now has explicit diagnostics coverage.

Environment: isolated checkout at `.dev-copies/issue-240-prefix-browser-ownership`,
base `c5f773b18c4a5b30a3abda84ecac01ab625a3902`, macOS arm64, Node v26.10.0,
Python 3.14.8. Node/Python versions are local development evidence, not pinned CI
runtime qualification. Runtime/test overlays were dirty when tested; their exact
source hashes and baseline spec are preserved outside the checkout. Committed code
is unchanged from these tests apart from documentation/evidence.

| Command | Result and observed duration |
| --- | --- |
| `node --test --test-name-pattern='PD-240' tests/runner/ci-reliability.test.mjs tests/runner/ci-fast-lane.test.mjs` | 9 passed, 1.084 s before final shared-status additions; regular bridges below verify settled inputs |
| `make unit-file FILE=tests/unit/ci-fast-lane-regressions.test.ts` | 1 bridge passed; 29 underlying Node cases; 0.936 s Vitest |
| `make unit-file FILE=tests/unit/ci-reliability-regressions.test.ts` | 4 bridges passed; reliability, draft/evidence, shard and conflict suites; 18.87 s Vitest |
| `npm run test:unit -- tests/unit/prefix-diagnostics-regressions.test.tsx tests/unit/background-read-admission-regressions.test.ts` | 12 passed, 1.10 s |
| `npm run typecheck` | Passed; standalone elapsed time not separately captured |
| `npm run lint` | Passed with 10 preexisting unrelated warnings; standalone elapsed time not separately captured |
| `node scripts/check-merge-conflicts.mjs`, `node scripts/check-migration-inventory.mjs`, `git diff --check` | Passed static checks |
| `make ui-file FILE=prefix-diagnostics.spec.ts` | 8 Chromium cases passed; browser stage 190.72 s; image build 18.46 s; startup 16.29 s; health 0.12 s; cleanup 4.21 s |

The bridge initially failed because a synthetic collection lacked diagnostics, then
because the selected real pytest collector needed an isolated Python environment.
Fixtures were updated and prescribed backend setup completed; no test was skipped.
No local full, visual, or separate complete durability run was performed: production
rendering/persistence is unchanged and CI owns the complete candidate gate.

## Preservation and cleanup

Logs, traces/screenshots, baseline spec, source overlays, selection matrix and timing
files are retained under root `test-results/issue-240-20261013/`. Disposable projects
`tempo-pg-regressions-15246-bc3cf025` (baseline) and
`tempo-pg-regressions-40533-7cdf4b3a` (passing owning spec) used fresh database state.
Their exact teardown was the runner's project-scoped Compose down with volumes and
local images; both passed. Readback found no containers, images or volumes for either
project. No live resources, other-task checkouts or shared caches were removed.

After remote preservation and final qualification, remove this task's checkout using
the deletion gate. The root evidence remains owned by #240 for diagnosis/reproduction.
Candidate CI links, verified head and final checkout cleanup belong in the PR handoff;
this report does not assert merge readiness before those checks.
