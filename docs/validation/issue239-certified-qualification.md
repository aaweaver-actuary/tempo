# Issue 239: bounded test and fixture qualification

## Decision and boundaries

Baseline: `c5f773b18c4a5b30a3abda84ecac01ab625a3902`. No production or migration changes; existing assertions, STUDY browser selection, and full-boundary coverage remain intact. This qualification-infrastructure PR itself selects FULL.

The certificate in `scripts/ci-certified-tests.json` records six exact test/fixture paths, their reviewed import/dynamic dependency lines, SQL relation/identifier dependencies, and literal consumers in tracked and nonignored new Python, JS/TS, shell, Rust and configuration source. Changed dependencies, added consumers, missing source, ambiguous ownership, or unsupported native collection expand to FULL. There is no directory exemption. This is a bounded certificate, not a general dependency resolver. Security-sensitive paths and explicit FULL paths retain precedence. The reviewed exceptions bypass only broad directory classification.

| Candidate | Behavior and callers | Production impact / shared dependency | Required evidence / decision |
| --- | --- | --- | --- |
| Cleanup regression alone | Callback ordering, original-error preservation, independently attempted cleanup; imports only Vitest and the test-specific helper | No production caller; callback mocks cannot prove fixture SQL | Execute its 21 cases directly: FAST |
| Cleanup helper / Redis browser spec | Redis restart/unpause obligations, page/listener/fixture cleanup; helper imported by its unit file and owning browser spec | Test-only; real browser uses shared observability, board fixtures, Docker, migrated PostgreSQL/Redis and workers | Unit file plus existing STUDY durability/ten cases and owning Redis case: STUDY |
| Redis fixture / focused fixture tests / native isolation suite | Snapshot ownership, rollback, conflict detection, exact receipt cleanup, settings/queue restoration and replay; fixture imported by focused Python and native suite, invoked by browser | No production imports; mutations touch shared settings, queue/projection, cards/reviews/receipts. Native locking/triggers/FKs cannot be proved by mocks | 12 focused Python cases, cleanup unit file, 17 native isolation cases, existing STUDY plus Redis browser: STUDY |
| Prepared continuation regression files | Audit existing issuance, invalidation and replay behavior; Python fixture imports queue-attempt setup | No production edits; SQLite adapters do not prove PostgreSQL locking | Changed files execute directly; preserve STUDY protection |
| Prose and registry | Evidence and named-test documentation | No executable registration | Existing conflict/migration/diff guards: FAST |
| Executable registration / runner/configuration / new unknown fixture | Test scheduling, shared isolation, startup/build/teardown or qualification trust | Shared runner, inventory, schema and browser setup | FULL, including mixed changes with certified paths |

Shared browser fixtures/observability, PostgreSQL runner/plan/options, qualification validators and inventories, test registration, production `main.py`, persistence, schema, dependency/build configuration and unknown sources are not certified exceptions. New SQL relations or identifier literals (including dynamic table/column lists), import/caller dependencies invalidate the certificate until reviewed in a FULL-qualified change. Added or changed browser registrations invalidate its fixed single-case certificate. Native method identities are extracted from the sole supported `RedisFixtureIsolation` class into the immutable plan; runtime collection must match them exactly. Native check names and modes are allowlisted.

## Historical ledger

- **PR 229 whole PR:** production HTTP admission changes in `backend/app/main.py`, fixture and browser work, verification inventory and runner registration. FULL is justified. Fixture narrowing cannot remove the other FULL triggers.
- **PR 229 fixture repair `c28499813fe35d8b1cd19c2e2f42e869de86829f`:** seven paths, consisting of fixture implementation/native suite/focused Python/owning browser and prose/registry. Those boundaries motivate the bounded native certificate. The path replay below uses current source, not retrospective qualification of that old patch.
- **PR 233 original helper `ae9b621523eddbe748aa2b48a40a48cbec218dff`:** independent cleanup helper and fault matrix; helper/browser unknown ownership forced FULL, while admission-named unit tests forced STUDY. Its incorporated implementation is now certified as described above.
- **PR 233 final stacked comparison:** base `ef26a6d9c19969baa3f1b1974dd58bcb27a66032`, head `88a25adbeb2faaf3e14f0ccdeab97ebaa1db3769`; only validation prose and registry remained. Existing FAST behavior is already sufficient. Do not classify the original runtime patch using that final diff.
- **PR 234 final independent delivery:** head `ab63bfb437b63af126b2038c0b8ece514d2717b0`, two regression files, audit report and registry. Run [38388236781](https://github.com/aaweaver-actuary/tempo/actions/runs/38388236781) actually selected **STUDY**, despite the initial #238 description saying FAST. Its immutable `fast-lane-plan` artifact proves this; the job named `fast` supports both tiers. No narrowing is proposed for these critical-study regressions.

## Selected work and expected savings

Reproduce the source/path replay with `node scripts/measure-certified-qualification.mjs c5f773b18c4a5b30a3abda84ecac01ab625a3902`. It imports the pinned old planner/inventory, uses the same current checkout and path sets on both sides, and performs read-only browser collection. Collection is not test execution.

| Path set | Before | After | Selected work after |
| --- | --- | --- | --- |
| Cleanup unit alone | STUDY | FAST | 1 frontend file / 21 cases; zero backend/native/browser |
| Cleanup helper or owning browser | FULL | STUDY | 1 frontend file / 21 cases; 11 browser cases; 10 PostgreSQL stages |
| Fixture implementation or native suite | FULL | STUDY | 1 frontend / 21 cases, 1 Python / 12 cases, 17 native cases, 11 browser cases; 11 PostgreSQL stages |
| Focused fixture Python test | STUDY | STUDY | Adds owning unit/native/Redis browser evidence to the former generic smoke selection |
| Prepared regression pair | STUDY | STUDY | Same 1 frontend, 1 backend file and ten browser cases |
| Prose / registry | FAST | FAST | Same static guards |
| Shared runner / registration | FULL | FULL | Same complete qualification |

The complete current collection contains 164 frontend unit files, 145 backend test files and 284 regular selected browser cases, plus 69 pinned cases. File counts exclude auxiliary unit directories. FULL also retains build/Rust/engine, lifecycle and complete PostgreSQL durability. Narrow plans omit unrelated provider/backend tests and browser families; they retain every existing STUDY check. These counts quantify selected work, not a runtime speedup.

Successful main run [38394000278](https://github.com/aaweaver-actuary/tempo/actions/runs/38394000278), source `7a68242f233b2e5e5181294a86950b6e63471b9b`, supplies a fresh complete-boundary reference: 77.8 successful job-minutes and 14.42 minutes from first job start through merge-gate. Parallel jobs are summed only for runner consumption, never elapsed time; skipped jobs are excluded. Successful PR 234 STUDY supplies a 537-second execution job, with 385.58 seconds in the disposable STUDY runner, 25.25 in capability checks and 1.25 in browser collection.

Expected cleanup-unit savings are approximately **6.87 runner-minutes per invocation** from omitting those three STUDY-only commands (412.08 seconds), assuming the same smoke runtime. A fixture/helper STUDY run is modeled at roughly **9–11 runner-minutes**, versus the 77.8-minute full reference: approximately **65–69 fewer runner-minutes** and **3–5 minutes less workflow elapsed time**, with unchanged setup/cache/queue conditions. Its extra Redis case and native restoration overhead were locally 25.9 and 9.63 seconds; these are scenario estimates, not Linux CI benchmarks. Different revisions, fixture contents, local/macOS versus CI runtime, scheduling and cache state prevent an A/B speedup claim. Failed runs and same-candidate reuse wall times are excluded from these estimates. #238 has not yet published a completed 20–30 PR baseline; the reproduced controls and timing references above are the available reconciliation.

## Validation and provenance

Selected scope: planner/evidence selection and failure contracts; affected runner boundaries; actual fixture PostgreSQL semantics; Redis browser workflow; complete PostgreSQL durability. CI owns complete qualification for this PR. No local `make full` was run.

Initial failing baseline: the first two named issue239 tests reproduced STUDY instead of FAST for callback-only cleanup and FULL instead of STUDY for the certified fixture. Existing assertions and native/browser tests were not weakened.

Environment: isolated macOS ARM64 checkout, Node 26.10.0/npm 11.19.1/Python 3.14.8; locked Node dependencies and repository-prescribed backend environment; migrated PostgreSQL 18.6/Redis 7, Docker Python 3.12, pinned browser image.

- `node --test tests/runner/ci-fast-lane.test.mjs tests/runner/postgres-test-speedups.test.mjs`: 102 cases passed after the JavaScript-consumer correction (exact final duration in preserved log). Added planner tests run through the regular Vitest bridge.
- `node --test tests/runner/ci-reliability.test.mjs tests/runner/ci-development.test.mjs`: 78 passed, 6.78 seconds.
- `make unit-file FILE=tests/unit/ci-fast-lane-regressions.test.ts`: bridge passed, 2.60 seconds after certificate hardening.
- `make unit-file FILE=tests/unit/redis-admission-cleanup-regressions.test.ts`: 21 passed, 0.318 seconds Vitest.
- `make python-file FILE=backend/tests/test_ci_native_unittest.py`: 7 passed, 0.05 seconds.
- `make python-file FILE=backend/tests/test_redis_admission_fixture.py`: 12 passed, 1.91 seconds.
- `npm run typecheck`, `npm run lint`, conflict/migration guards and diff checks passed; lint had zero errors and ten existing unrelated warnings.
- At clean executable commit `ec6325cf4a3399e334748ffdf7c82376c29e6e9b`: `node scripts/test-postgres-docker.mjs --mode browser --browser-file redis-admission-recovery.spec.ts --native-check redis-fixture-isolation` passed 17 native cases (2.681 seconds), native stage 9.63 seconds including consumer restoration, 1 browser case (25.2 seconds Playwright / 25.9 seconds stage). Build 9.35, startup 12.53 and cleanup 3.22 seconds. No full pass is implied. The later identity hardening changes planner/gate collection binding; the native runner and test assertions remain identical.

- At executable commit `94aa99348ea067f16855ac1ecc6ac80abdd67283`, `make docker-durability` **failed**, after 17 native fixture cases passed (2.754 seconds), during `backup_restore` (17.11 seconds). `recover_retained_applications -> publish -> integrity_scan -> claim_task` received PostgreSQL `TransactionTimeout` on the original database after verifying the restored copy. Preceding background workloads (187.04 seconds) and container recreation (47.94 seconds) passed; cleanup passed (6.69 seconds). This is not a complete durability pass.
- Focused diagnosis reused the unchanged runner actions with only `compose_config,image_build,startup,service_health,backup_restore,cleanup` selected, and `--mode browser` (no browser action in that diagnostic overlay). On a fresh disposable database the exact backup/recovery assertions passed: backup 18.34 seconds, cleanup 3.72 seconds. The source transform is a single replacement of `const stages = postgresTestStages(options);` with that explicit array in a temporary sibling script; the original file stayed untouched and the temporary file was removed. This does not reproduce the full run's preceding database state, establish a root cause, or constitute full durability qualification. CI must supply the fresh complete candidate result; no timeout or assertion was changed.

Evidence is preserved outside the task checkout under root `test-results/issue239-20261013/`, including historical plans/results, selection replay, native results, timings, ownership and runtime logs. Disposable resources are owned by each runner and its exact teardown is recorded there. Live study services and other checkouts are untouched. The runtime evidence and documentation changes have separate revision provenance; current-head CI must qualify the published candidate.

## Required-CI fixture repair

Run [38400712633](https://github.com/aaweaver-actuary/tempo/actions/runs/38400712633) on head `5e2d56ec84a4f1a8d9044877a297e66008733e1e` passed frontend, backend, PostgreSQL, lifecycle, build, visual and three browser shards. Shard 4 failed the existing named Bury workflow: its expected `still pending` text was replaced briefly by `Burial is unresolved`, then disappeared. The retained service log shows a successful `queue.bury` at 13:06:54.703 UTC, despite the test's page-level 503 route; its page trace contains no intercepted burial request. This demonstrates missing fault injection, not a valid burial-refusal proof. No aggregate success is claimed for that run.

The separate fixture repair waits for actual service-worker control and uses context-level routing for the real outgoing request, as supported by [Playwright's worker routing contract](https://playwright.dev/docs/service-workers). It also asserts interception occurred and its operation ID equals the retained/retried ID. Service workers and all original assertions remain active; no product source, timeout or selection policy changes. This shared browser workflow still selects FULL. Fresh candidate CI must demonstrate the repair, including all original native queue assertions. Local native reproduction is unavailable because Docker Desktop now reports `unable to start`; it was not restarted or reconfigured.

The additional integrated narrow diagnostic on 5e2d56ec executed frontend collection/results (21 cases), backend collection/results (12 cases), lint (11.28s), typecheck (5.93s) and static guards, then failed capability preflight (0.13s) before creating resources. Its report is explicitly failed, with diagnostic run ID 0 and draft state; it cannot qualify this PR. No native/browser results from that attempt are claimed.
