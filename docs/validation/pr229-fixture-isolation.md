# PR #229 database fixture isolation

## Initial repair evidence (historical)

This section records the earlier repair. The final correctness review below adds
queue ownership v2 and narrows the concurrency wording; earlier run results are
not fresh candidate qualification.

### Review and ownership

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
| Insert three owned queue entries | Version 2 snapshots exact returned ID/card/date/cycle/admission repertoire; cleanup locks and requires their exact ownership union before card cascades. Missing, reassigned or additional relationships conflict; mutable review fields are permitted. The third card is an unreviewed successor, preventing browser refresh/issuance from reaching unrelated entries after the two reviews. |
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

A visible restoration conflict intentionally leaves the fixture snapshot for diagnosis, and fails the test; runner teardown is the final containment boundary. Identical-value intervening writes cannot be distinguished by snapshot comparison. Serial browser execution excludes other tests, not application writers; the separate concurrency boundaries and final evidence below supersede any broader reading. Abrupt host termination can interrupt teardown, requiring subsequent verified ownership-based cleanup. The local broad durability failure remains a validation limitation until fresh required candidate CI succeeds. No merge or deployment is authorized.


## Final correctness review — 2026-10-13

Reference fixture: `ef26a6d9c19969baa3f1b1974dd58bcb27a66032`.
Implementation starts from current PR head
`edc07ad980637500050e88b6f0338e29e040742a`, incorporating its documentation-only
merge of #233; runtime fixture inputs still match the reference. Latest fetched
main was `d82f78e9b764fbba5b48e9ea6de8abcca5d133e7`. The task branch was created
from main in an isolated `.dev-copies` checkout and fast-forward incorporated the
PR. #233, production behavior and unrelated prefix diagnostics remain untouched.
The final implementation SHA and fresh CI candidate are recorded in the PR
handoff, avoiding a self-referential source commit hash.

### A — operation receipts: VERIFIED SAFE for this browser scenario

The raw real-browser inventory in `receipt-inventory-before-cleanup.json` and
`receipt-cleanup.json` contains 12 operations, all complete. Four `cards.review`
receipts (two retained review-attempt keys and two explicit replay keys) have
`{card_id, review:{queue_entry_id, attempt_id, prepared_next,...}}`. One
`queue.issue_head` receipt has
`{queue_date,entry_id,card_id,expected_revision}`. All five exact owned operation
IDs disappeared after cleanup. Seven unrelated IDs survived: two each of
`games.analysis.repair_timeout`, `games.analysis.repair_provenance` and
`threat.analysis.claim` with `{}`, plus `queue.ensure_current` with `{queue_date}`.
Prepared continuation issuance is part of the review, not a separate receipt in
this run. Raw inventory is separate from the unchanged fault-state comparisons.

Source trace: `backend/app/main.py:1898` constructs the head payload and
`:2971` constructs review payload; `backend/app/tasks.py:198` records operation
attempts and `backend/app/command_gateway.py:80` persists the original JSON.
`backend/migrations/001_initial.sql:310` and
`backend/migrations/017_operation_recovery.sql:4` show receipts have no card FK or
card-deletion cascade. Thus explicit cleanup is required. The existing
`delete_owned_rows` exact top-level `payload_json::jsonb->>'card_id'` predicate is
unchanged; no substring matching or broader deletion was added.

The fresh Playwright context submits only correct, unguided reviews. Modern
failure outbox dispatch includes `card_id` (`app/lib/training-failure-outbox.ts`),
but this scenario generates no attempt-failure receipt. Legacy queue-only
attempt-failure/burial payloads are theoretically possible elsewhere, not produced
here. The audit rejects observed top-level owned queue IDs without owned
`card_id` before deletion; future unexpected shapes require investigation rather
than speculative expansion. Native receipt preservation coverage also includes
unrelated reviews, logical receipts, queue origins and identity text in unrelated
operation payloads.

### B — synthetic queue ownership: CONFIRMED DEFECT, repaired

Against the exact `ef26a6d9` fixture source, two named native regression methods
failed in six subcases (0.074 s, no setup errors): changing card/date/cycle/admission
repertoire or adding an owned-card queue cycle/unrelated-card admission
relationship did not raise the expected conflict. The baseline errors from early
test-setup mistakes are retained separately and excluded from this evidence.

Snapshot v2 persists each returned queue ID with card ID, queue date, cycle and
admission repertoire in the same seed transaction. Cleanup locks the union of
saved queue IDs, owned card IDs and owned admission repertoire, and requires
exactly that identity set before destructive cleanup. Missing/reassigned/additional
rows abort atomically and retain the snapshot. Unsupported versions fail visibly.
Status, review result, attempt flags, position and other mutable fields are not
compared; the browser and native mutable-state test prove legitimate changes are
allowed. The same two methods pass after repair (0.066 s). The new missing-row,
manifest-finalization rollback and version-rejection cases complement existing
setup/cleanup rollback, retry, lost-output and idempotence coverage.

`backend/migrations/001_initial.sql:145` gives queue cards their cascading FK;
`:159` gives membership both parent FKs. Without this guard, deleting a synthetic
card could cascade through an unexpected relationship. `admission_repertoire_id`
is text, not an FK. The stable fields are ownership evidence, not a full snapshot
of mutable queue presentation. All fixture mutations and restoration behavior
remain in the inventory above; sequence allocation is intentionally not reversed.

### C — concurrency: VERIFIED SAFE within the supported contract

Separate runner invocations exclusively own separate stacks, volumes, credentials
and project-local DSNs (`scripts/test-postgres-docker.mjs`, project generation,
`verifyProjectIsUnused`, and Compose assertions). Playwright's one worker and the
fixture worker assertion exclude simultaneous browser tests, not application
processes. The advisory transaction lock serializes cooperating fixture operations
only; the durable marker rejects a second active fixture. No lock spans browser or
network work.

The native `background_workloads` proof stops defense-engine, background-worker
and background-scheduler (`scripts/postgres-test-plan.mjs:25`,
`scripts/test-postgres-docker.mjs:976`). Its foreground worker stays running.
The browser runner starts the application stack normally and does not perform
that suspension. `backend/app/services/study_mode.py:9` permits `daily_queue`:
study-only mode is not complete quiescence.

| Process | Supported writes relevant to this fixture |
| --- | --- |
| API | Uses reader credentials; dispatches foreground commands, including head issuance and reviews. It does not write these rows directly. |
| Foreground Celery worker, concurrency 2 | Executes API commands; writes review/scheduling/attempt/issuance state and operation receipts. Recovery/replays complete before the page is closed and cleanup audit runs. |
| Background worker, concurrency 1 | Executes admitted background commands and essential queue slices. Can publish shared projections or reorder queue presentation; visible restoration conflicts are detected. It does not invent nonce repertoire membership. |
| Background scheduler | Enqueues periodic tasks; no PostgreSQL writer DSN. Can cause essential queue work through workers. |
| Defense-engine and Maia worker | Submit API claims/results; do not hold direct PostgreSQL writer credentials. Synthetic cards are tactics without defense jobs or coverage runs; their callbacks do not create ownership relationships to these nonce identities. |
| Schema one-shot containers | Apply migrations or explicitly execute seed/audit/cleanup/native proofs; cooperating fixture transactions serialize. No recurring schema writer. |
| Web, Redis, PostgreSQL | Web has no database writer; Redis brokers commands; PostgreSQL enforces locks/FKs/triggers. |

The two-connection regression enters real cleanup while its repertoire/card/queue
locks are held. Concurrent extra queue and external membership INSERTs referencing
owned cards time out at their FK parent lock; after cleanup commits the queue
INSERT fails its FK. Restoration still matches the original state. Existing queue
rows and shared settings/projection rows are locked through validation and cleanup.

These are row/FK protections under READ COMMITTED, not predicate locks.
`daily_queue.admission_repertoire_id` has no FK: arbitrary concurrent SQL could
insert an unrelated card naming only this repertoire after validation. No supported
writer can choose an unrelated card for this nonce repertoire, whose membership
is exactly the owned cards. New operation receipts also have no parent FK; the
supported scenario closes its producer page and observes completed owned commands,
then verifies exact receipt deletion. No deterministic reachable race beyond these
protections was established. General arbitrary writers, externally injected
commands, complete application quiescence and equal-value intervening writes are
not claimed safe. Restoration conflicts remain visible and rollback atomically;
owned runner teardown contains residual disposable state.

### Focused final-overlay validation

Environment: macOS ARM64, Node 26.10.0, Python 3.14.8/pytest 9.1.1;
PostgreSQL 18.6, Redis 7, Docker Python 3.12. All runtime tests below used the
unchanged final executable overlay on `edc07ad9`; final documentation edits do not
relabel older executable evidence. Logs, raw receipts and project timings are
preserved under `test-results/pr229-final-fixture-correctness-20261013/` outside
the task checkout. CI owns final required candidate validation.

| Command | Result / execution duration |
| --- | --- |
| `make python-file FILE=backend/tests/test_redis_admission_fixture.py` | 12 passed, 0.12 s pytest |
| `make unit-file FILE=tests/unit/redis-admission-cleanup-regressions.test.ts` | 21 passed, 0.459 s Vitest |
| `make unit-file FILE=tests/unit/postgres-test-speedups-regressions.test.ts` | 1 bridge passed (68 Node cases), 2.81 s Vitest |
| `schema python /source/scripts/check_postgres_redis_fixture_isolation.py` in fresh disposable native proof | 17 passed, 2.660 s; two independent connections, real migrated FKs/triggers |
| `make ui-file FILE=redis-admission-recovery.spec.ts` | 1 passed, 22.3 s Playwright / 23.2 s browser stage; build 9.93 s, startup 13.47 s, cleanup 3.14 s |
| `make docker-durability` | Passed all 16 planned stages; fixture 17 passed in 2.681 s inside background-workloads 176.2 s; recreation 42.6 s, backup/restore 20.25 s, study durability 99.98 s, owned cleanup 3.63 s |
| `npm run typecheck`; `npm run lint`; `git diff --check` | Passed; lint zero errors, 10 unchanged warnings; wall duration not captured |
| `make plan` | Inspected configured full-stage inventory; no execution/pass claimed |

The native standalone proof verified all pinned requirements against a cached
shared Python runtime, with fresh project-local PostgreSQL/Redis and current
source mounted. The regular browser and durability runners build current-source
product images independently. Existing deadlines remain unchanged. No local
`make full` was requested or run; selected development evidence is not complete
PR qualification. Remaining durability/CI evidence and cleanup are recorded in
the final handoff.
