# Queue adapter native proof and window contract

This is a four-case source-adapter proof on actual PostgreSQL. It is not an
installed-artifact, full durable-worker, PR311-hook or full-system qualification.
The coordinator owns the resource lifecycle. This directory contains no Docker
controller. One explicitly approved window on `269ce9e1` ran on 2026-10-20 from
11:14:06 to 11:14:10 UTC: three cases passed, including shared-membership parity;
the requeue case failed at fixture transaction control (`SAVEPOINT AS caller_repeat`
from the legacy translator). It now uses native savepoint/rollback/release calls.
The parent-coordinated corrected rerun at `bd672bd6` passed all four cases from
11:18:23 to 11:18:27 UTC, including the PostgreSQL parity comparison and the
requeue rollback proof. Independent collection/execution/JUnit inventories agree;
all setup/call/teardown phases passed with no skips or deselections. Actual caps,
cgroup limits, tmpfs and loopback binding were verified. Exact container, process
group, staging and volume absence was verified before slot release. The first
failed window remains separately preserved and does not contribute passing credit.

## Safety prerequisites before any fixture write

1. For any future run, parent grants a fresh serial slot after inspecting the guard
   and source rejection evidence. The successful run above has released its slot.
2. Use one fresh coordinator-owned PostgreSQL container and a fresh database named
   `tempo_queue_admission_<32 lowercase hex digits>`. Existing live/task databases
   are forbidden. Pin the already-present `postgres:18.6-trixie` image by observed
   image ID; no pull, build, dependency install or live credential reuse.
3. Apply this tested tree's backend migrations through the existing
   `scripts/apply_postgres_migrations.py`, provision the existing `tempo_reader`
   and `tempo_writer` roles through `scripts/provision_postgres_roles.py`, and
   insert default `settings(id=1)` in this new database only.
4. Parent records the exact container ID, observed loopback mapping, database
   name/OID and PostgreSQL system identifier in its target receipt. Get the latter
   two from the owned admin connection with `pg_control_system()` and `pg_database`.
   Inspect actual mounts/limits and ownership before issuing the receipt.
5. Clear inherited `PG*`, test/plugin injection and all prior database URLs before
   injecting the new values. Both native pytest processes must be fresh; no prior
   application pools. Keep credential-bearing values out of logs and command lines.

Inject these variables privately:

- `TEMPO_QUEUE_ADMISSION_FIXTURE`: exact new database name.
- `TEMPO_QUEUE_ADMISSION_TARGET_FILE`: absolute path to the parent-issued receipt.
- `TEMPO_QUEUE_ADMISSION_ADMIN`: explicit admin DSN, role `postgres`.
- `TEMPO_DATABASE_READ_URL`, `TEMPO_DATABASE_WRITE_URL`: explicit DSNs, roles
  `tempo_reader` and `tempo_writer` respectively.
- `TEMPO_TEST_INSTANCE=disposable`, `TEMPO_POSTGRES_POOL_SIZE=4`.

All DSNs must explicitly contain the receipt's `host=127.0.0.1`, matching ephemeral
port, matching database, user, nonempty disposable password, `sslmode=disable`, and
`connect_timeout=2`. Missing defaults, other endpoints/databases/roles, hostaddr,
service/options indirection, multiple hosts and ambient libpq configuration fail
before connection. This includes all three DSNs being stale together.

The receipt is private fixture metadata, not a credential file. Parent writes it
atomically with mode 0600. Its format is:

```json
{
  "version": 1,
  "database": "tempo_queue_admission_<32 lowercase hex digits>",
  "host": "127.0.0.1",
  "port": 49173,
  "container_id": "<actual 64 lowercase hex container ID>",
  "system_identifier": "<observed decimal string>",
  "database_oid": "<observed decimal string>",
  "created_at": 0,
  "deadline": 300
}
```

Times are actual Unix seconds; use a five-minute lifetime, never more than ten.
The example values are not executable fixture identities. Receipt validation is
followed by a two-second, read-only admin connection checking `current_database`,
`current_schema=public`, `current_user=postgres`, system identifier and database
OID. Any mismatch stops before configured-pool fixture writes. The environment is
validated again after that probe. The guard trusts the parent's ownership receipt;
the coordinator remains responsible for deriving it from its exact owned resource.

## Proposed fixed resource/time budget

- One PostgreSQL container: **1 CPU, 384 MiB memory, memory+swap limit 384 MiB**
  (no additional swap), **128 PIDs**. `shared_buffers=32MB`, `max_connections=16`.
- Database storage: bounded **512 MiB tmpfs at `/var/lib/postgresql`**, default
  bounded shared memory; actual mount inventory must show no persistent named or
  anonymous data volume. No source bind mounts into this container.
- One ephemeral **127.0.0.1-only** PostgreSQL host port recorded in the receipt;
  no externally exposed ports. One Python pytest process at a time, at most two
  test threads. Reader/writer pools cap at four connections each.
- No Redis, Celery worker, HTTP app server, browser, engine or helper container.
- **Five minutes work plus two minutes cleanup**: startup/migrations at most
  90 seconds, native execution at most 180 seconds, remaining 30 seconds for
  inventory/receipt checks. Parent's watchdog must terminate the exact owned test
  process group on timeout and enter cleanup; a thread future timeout alone is
  not a process deadline. No automatic retry or budget extension.

These are proposed caps for parent approval, not permission to allocate resources.
If an existing coordinator controller cannot enforce them or requires broader
mount/port scope, return that difference for review before starting.

## Exact four-case commands after grant

From the exact tested checkout, use its existing backend-capable Python runtime
without building/installing anything. The retained task interpreter is `.venv/bin/python`;
its source-package links qualify this source/native adapter seam only. Let
`QUEUE_NATIVE_EVIDENCE` be a new absolute task-owned evidence directory. With the
private validated fixture environment injected, run these sequentially under the
parent watchdog:

```sh
PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -B services/learning/application/qualification.py \
  "$QUEUE_NATIVE_EVIDENCE/native-collected.json" \
  -c services/learning/application/pyproject.toml -o addopts= \
  -p no:cacheprovider --rootdir=. \
  backend/integration/queue_admission/test_postgres.py --collect-only -q

PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -B services/learning/application/qualification.py \
  "$QUEUE_NATIVE_EVIDENCE/native-executed.json" \
  -c services/learning/application/pyproject.toml -o addopts= \
  -p no:cacheprovider --rootdir=. \
  backend/integration/queue_admission/test_postgres.py \
  -v --tb=short --junitxml="$QUEUE_NATIVE_EVIDENCE/native.xml"
```

Require exactly these four node names to collect and execute, with all setup/call/
teardown phases passing and no skips/deselections:

- `test_native_adapter_failure_rolls_back_card_queue_and_unissued_origin`
- `test_native_existing_card_day_locks_serialize_admission_without_moving_issued_head`
- `test_native_capacity_witness_is_rechecked_after_publication_change`
- `test_native_requeue_retains_historical_origin_cycle_and_caller_rollback`

Use the existing `qualification.validate_inventory` to compare the collection,
execution and JUnit receipts, additionally comparing the four names above. Preserve
stdout/stderr, exact source head/input hashes, Python/dependency provenance,
nonsecret fixture receipt and resource inventory. The concurrency case compares
legacy-complete-query and paged shared-card admission on actual PostgreSQL; a
difference blocks parity and requires an explicit ownership-policy decision.
It then exercises two callers that explicitly hold the established card/day
locks. That does not prove every production caller holds them.

## Cleanup and truthful outcome

Always stop the exact owned pytest process group first, close its pools by process
exit, then inspect the recorded container ID and verify both
`tempo.owner=<invocation identity>` and `tempo.disposable=true`. Remove only that
exact verified container. Record actual mounts/volume IDs before removal and
verify exact container absence and no surviving owned volume afterward. Never
prune Docker, remove a shared image, or touch live/another task's resource. An
inspection failure or ownership mismatch is an unverified cleanup outcome, not
absence or permission to force removal.

Remove only the disposable fixture secret files after resource absence is verified;
retain nonsecret receipts, failed assertions and exact remaining resource IDs.
Successful tests with missing cleanup or receipt validation still fail the window.
Four passes alone do not qualify feedback accepted-scope exclusion, PR311's
first-issuance/settlement hooks, or future durable-worker adoption; those need
their owning tests on the parent-controlled combined tree.
