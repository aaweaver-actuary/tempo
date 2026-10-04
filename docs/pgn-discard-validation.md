# PGN discard validation plan

Changed behavior: a retained PGN identity can be deliberately discarded without
the source file, and a failed/discarded upload cannot prevent a later upload.
Risks: delayed delivery restores payload or imports data, discard races admission,
lost responses clear unconfirmed state, stale responses erase a newer identity,
and disposal affects another command or a completed repertoire.

Smallest proof: named command/component regressions, focused backend command and
route contracts, then the real recovery browser file and PostgreSQL durability.
Pinned visuals cover the new recovery control. Typecheck, lint and diff checks
cover the affected frontend/API boundaries. CI owns final required candidate and
current-base validation; no overlapping local full gate is planned.

Development uses `.dev-copies/pgn-import-discard`, branch
`codex/pgn-import-discard`, based on main
`eb42d8781fc3466db9dfea234490cd74e616fd26`. The main study checkout's existing
changes and all other development checkouts are preserved. No other checkout
was confirmed released, so none was repurposed.

The separately authorized live repair creates a terminal, payload-free marker for
`070d9023-70a8-403b-82ec-b851f25b1170` only. It is operational repair, not test
evidence; application regression tests use disposable instances.

## Focused execution evidence

Local execution used macOS ARM64, Node 26.10.0, Python 3.14.8, PostgreSQL
18.6 in disposable Docker stacks, and Playwright 1.63.0. Runs used the working
patch on base `eb42d878`; they are not a clean-HEAD full-gate claim. CI will
validate the final committed candidate and current-base merge revision.

| Exact command | Observed result / wall time |
| --- | --- |
| `npm run test:unit -- tests/unit/pgn-import-dialog-regressions.test.tsx -t 'discarding an unknown PGN import permits a different file after reload'` | Failed before implementation: missing discard control, 1 failed / 3.36 s |
| `npm run test:unit -- tests/unit/pgn-import-pending-regressions.test.ts tests/unit/pgn-import-dialog-regressions.test.tsx` | 58 passed / 3.35 s |
| `make python-file FILE=backend/tests/test_postgres_pgn_discard.py` | 11 passed / 4.21 s |
| `make python-file FILE=backend/tests/test_postgres_cutover.py` | 196 passed / 12.96 s |
| `make python-file FILE=backend/tests/test_postgres_route_contract.py` | 3 passed / 5.25 s |
| `node --test tests/runner/postgres-test-speedups.test.mjs` | 37 passed / 2.44 s |
| `npm run typecheck` | Passed / 18.70 s |
| `npm run lint` | Passed: 0 errors, 8 existing warnings outside changed files / 33.90 s |
| `make ui-file FILE=recovery.spec.ts` | 21 passed; 29.4 s browser execution / 92.99 s wall |
| `make plan`, `git diff --check` | Inventory inspected; diff check passed |

The first browser file run passed the new discard case but failed two older
reload assertions expecting the initial button label. Reopened recovery now
shows **Check again**. The corrected assertions retain the original file,
settings, receipt, same-ID replay, no-replay diagnostic and deduplication proof.
The rerun passed all 21 cases. A later stale-completion guard is proved by the
final command unit file; final-candidate browser coverage belongs to CI.
The new invalid-route fixture initially invoked application startup instead of
testing only the route. It now uses a request-only TestClient, avoiding storage
initialization; all 11 backend cases pass without starting a study service.

The live browser record identified `taimonov2.pgn`, Black, depth 5. The exact
operation had no receipt immediately before repair. A payload-free discarded
marker was committed for that operation, then the existing dialog consumed its
failed receipt without submitting the selected replacement file. Safari's
pending key was verified `null`; only the task-created tab was closed. The
original study tab, saved repertoires and files on disk were preserved.

Fresh GitHub issue and PR review found no matching open PGN defect or competing
implementation. This extends merged PR #68's same-file/legacy recovery with
explicit discard. Open performance issues #29, #38 and #42 retain their separate
scope; no issue was closed or relabeled.

`make docker-durability` passed every one of its 14 planned stages in 733.37 s
wall time. The discard/locking proof took 4.53 s; schema/CLI upgrade recovery
accounted for 523.61 s; study durability, including replay across recreation,
took 63.4 s. These are nested stage times, not extra suite durations.

Only the new `pending-import-dialog-phone.png` baseline was generated and
visually reviewed. The filtered pinned generation passed one case in 31.49 s
wall time; existing baselines were unchanged. The full `make visual` run was
started after the control was stable. Its final result and final committed
candidate CI are recorded in the PR delivery evidence, with raw logs retained
outside this unmerged checkout.

Docker ownership reports under `test-results/tempo-cli/` record checkout,
revision, project, exact container/image IDs, creation/start times and teardown
commands. Browser projects were `tempo-pg-regressions-53524-20758347` (initial
assertion failure) and `tempo-pg-regressions-54094-9a71e76f` (successful rerun).
Durability owned `tempo-pg-regressions-54633-0bb76703` and its `-cli` child.
Their runners removed owned containers, images, networks and disposable volumes;
post-run verification found no remaining durability project resources. Live
study resources, base images and shared dependency/build caches were retained.
The task checkout remains while its PR is unmerged.
