# PR #229 database fixture isolation

## Review and ownership

Reviewed PR #229 head `d49efd20d595c5c288426c82a4ee6338afcaf29c`, based on main `d82f78e9b764fbba5b48e9ea6de8abcca5d133e7`. The repair changes test fixtures, regression coverage, runner registration, and documentation only. Production admission, review, issuance and serialization code is unchanged by this repair.

The regular runner already guarantees isolation **between runner invocations**, not restoration between tests in one invocation:

- `scripts/test-postgres-docker.mjs` creates `tempo-pg-regressions-<pid>-<random>` and fresh random credentials. `verifyProjectIsUnused()` rejects existing containers, volumes and networks for the project.
- Its Compose configuration assertions require nonexternal project-prefixed volumes/networks and project-local PostgreSQL/Redis URLs, with no live SQLite mount. PostgreSQL data is a fresh project volume.
- `playwright.config.ts` disables full parallelism and sets one worker; this regression additionally rejects a worker override. Independent CI shards invoke independent runners/stacks.
- The schema fixture uses the same project-local PostgreSQL service as the API. The browser test no longer supports native Python, custom database URLs, custom API URLs, or custom Compose definitions.
- Runner failure handling always attempts owned project teardown and checks for remaining containers/volumes. A disposable environment flag or a project-like name alone must never be used to designate a live database.

Seed/cleanup use a short PostgreSQL advisory transaction lock and an existing synthetic repertoire as the persistent ownership marker. A second active Redis fixture on the same database is rejected. No transaction or lock remains held across browser or network work. The committed, versioned restoration snapshot lives in the repertoire's `source_name`; cleanup needs only the identity, even if seed's committed response was lost. CLI success is emitted after commit.

Before destructive cleanup, locked settings, every displaced queue position, full installed projection, and synthetic card ownership/links are checked. A mismatch aborts the cleanup transaction with a named restoration conflict and retains the snapshot. PostgreSQL rollback prevents partial restoration or deletion; cleanup can be retried after the cause is resolved. Successful repeated cleanup is a no-op.

## Mutation inventory

| Fixture mutation | Cleanup / conflict behavior |
| --- | --- |
| Insert default settings if absent; set `study_only_mode=1` | Restore original mode only. Other preexisting settings fields are preserved. Remove a fixture-created settings row only if its full contents still match. Missing or changed mode conflicts. |
| Shift all preexisting queue positions for the captured day | Restore every original position, after requiring every saved entry to exist at its installed position. Missing/reordered entries conflict. |
| Insert three owned queue entries | Exact card deletion cascades remove these entries. The third card is an unreviewed successor, preventing browser refresh/issuance from reaching unrelated entries after the two reviews. |
| Insert/update `queue_projections` | Snapshot the full original row or absence. Require the full installed row, then restore all original fields or remove the inserted row. Conflicting publication fails visibly. |
| Insert synthetic repertoire, cards and membership links | Delete exact owned identities. Reassigned cards, extra cards under the owner, and links into unrelated repertoires conflict before cascading deletion. |
| Browser review updates owned scheduling state and inserts reviews | Owned cards and reviews are removed. No unrelated card is reviewed; the browser asserts exact expected synthetic identities. |
| Review scheduling snapshots and logical receipts | Delete snapshots through exact owned review IDs, then explicit `review_attempt_receipts` rows by exact card identities. They have no cascading FK. |
| Card-bound operation receipts, including head issuance and replay | Parse payload `card_id` and delete only exact synthetic card identities. Irrelevant text containing the identity does not match. |
| Queue origin retention triggers | Explicitly delete owned `queue_attempt_origins` **after** queue/card deletion, since DELETE triggers retain origins. |
| Retained card revisions | Explicit exact-card deletion. |
| Canonical source triggers | Affect only the synthetic repertoire; it is deleted. External membership links are rejected before deletion. |
| Opening evidence presentation/context triggers | Tactic fixture cards do not generate opening evidence. |
| PostgreSQL identity sequence allocation | Sequence increments are intentionally not reversed; attempting to rewind them would violate concurrent allocation safety. |

No general fixture framework or production schema was added. The fixture-specific teardown helper and its tests are reused from [PR #233](https://github.com/aaweaver-actuary/tempo/pull/233), pinned at `ae9b621523eddbe748aa2b48a40a48cbec218dff`. The caller now always attempts identity-addressable SQL cleanup. Redis obligations are registered before fault commands; listener removal, unpause/restart, page closure, and SQL cleanup are independent. Original failures and labelled teardown failures are retained. PR #233's status is not changed by this work.

## Regression evidence

Tests are registered in `tests/REGRESSIONS.md`. Real PostgreSQL cases run in the existing `background_workloads` durability stage, with its workload consumers stopped and restored through the existing runner. They use migrated PostgreSQL, actual foreign keys/triggers, transaction rollback, and two independent connections; no SQLite or mocked persistence is claimed as PostgreSQL proof.

Local evidence was captured on the dirty fixture overlay of `d49efd20`, then committed unchanged apart from prose evidence. Environment: macOS ARM64, Node v26.10.0, Python 3.14.8 for pytest; PostgreSQL 18.6, Redis 7, Python 3.12 Docker runtime. Current-candidate CI remains the qualification owner; its result is recorded in the PR handoff.

| Command / scope | Observed result |
| --- | --- |
| `make python-file FILE=backend/tests/test_redis_admission_fixture.py` | 12 passed, 0.46 s pytest. The named omitted-receipt regression failed against the original fixture (`review_attempt_receipts` cleanup missing; 1 failure, 5.37 s). |
| `make unit-file FILE=tests/unit/redis-admission-cleanup-regressions.test.ts` | 21 passed, 0.631 s Vitest. Includes seed-output loss and the reused cleanup failure matrix. |
| `make unit-file FILE=tests/unit/postgres-test-speedups-regressions.test.ts` | 1 bridge passed, 3.19 s Vitest; invokes the 68-case bounded runner suite, including native fixture registration. |
| `schema python /source/scripts/check_postgres_redis_fixture_isolation.py` in a fresh project-owned migrated PostgreSQL stack | 10 passed, 2.473 s unittest. Covers seed failure, cleanup failure/retry, absent rows, settings/queue/projection conflicts, ownership changes, exact receipt preservation, committed output loss and two-connection ownership/lock contention. |
| `make ui-file FILE=redis-admission-recovery.spec.ts` | Final regular Chromium run: 1 passed, 24.7 s case / 25.5 s Playwright / 26.25 s browser stage. Setup/build/teardown are separate runner timings. |
| `npm run typecheck`; `npm run lint`; `git diff --check` | Passed. Lint: zero errors, 10 preexisting unrelated warnings. Command wall durations were not captured. |
| Focused `node --test --test-name-pattern 'foreground_redis_admission_native_regression_is_registered_and_selected\|every current browser spec belongs\|new unclassified specs fail planning' tests/runner/ci-reliability.test.mjs` | 3 passed, 1.698 s Node runner. |
| `make docker-durability` | Failed in existing `background_workloads` / opening-progression queue-refresh advancement with `TransactionTimeout`, before the new fixture proof executed. The failed stage took 61.25 s; preceding stages passed and owned cleanup passed in 5.79 s. No full durability pass is claimed. |
| Focused `schema python /source/scripts/check_postgres_opening_progression.py` with disposable PostgreSQL/Redis | Passed at unchanged production/test deadlines. The broad timeout was not reproduced; its cause remains unestablished. |

The standalone native fixture proof used a cached Python runtime image after verifying every pinned requirement version, and mounted the exact checkout's scripts/backend read-only. It proves database fixture behavior, not product image/startup qualification. Earlier harness mistakes (unsupported Compose run flag and missing Redis environment for the unrelated reproduction) were preserved separately and cleaned up; they are not reported as passing command runs. The full regular browser runner separately built current-source product images and passed.

## Preservation and remaining risks

Evidence, source overlays, ownership and scenario timings are retained outside the development checkout under `test-results/pr229-fixture-isolation-20261013/`. Task-owned disposable resources were torn down and checked; live study services, other task resources, shared base images and safe build caches were preserved. Final cleanup and current candidate CI status are recorded at handoff.

A visible restoration conflict intentionally leaves the fixture snapshot for diagnosis, and fails the test; runner teardown is the final containment boundary. Identical-value intervening writes cannot be distinguished by snapshot comparison; serial runner test ownership is the supported concurrency boundary. Abrupt host termination can interrupt teardown, requiring subsequent verified ownership-based cleanup. The local broad durability failure remains a validation limitation until fresh required candidate CI succeeds. No merge or deployment is authorized.
