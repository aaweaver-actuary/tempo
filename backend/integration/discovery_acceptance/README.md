# Discovery acceptance ownership and native proof

`backend/app/services/discovery_acceptance_application.py` owns freshness/choice
validation, replay, terminal readmission and the post-lock duplicate recheck.
`DiscoveryAcceptanceStore` is a semantic protocol following Learning's existing
`ReviewStore` pattern; it does not create another database service or process.
Its capabilities are:

- `has_matching_intent(choice) -> bool` for the pre-transaction preparation read.
- `lock_matching_intent(choice) -> MatchingDiscoveryIntent | None` for replay and
  the duplicate recheck after locking scope and the parent opportunity.
- `lock_opportunity_scope(opportunity_id) -> LockedDiscoveryOpportunity | None`
  returns observed and current scope facts. The PostgreSQL adapter locks
  repertoire metadata, game scope, then opportunity in the existing order.
- `task_state(intent_id) -> str | None` and `requeue_intent(intent_id)` support the
  application's existing terminal-readmission decision.
- `persist_intent_and_task(intent)` persists the intent, opportunity preparation
  marker and durable task/event on the borrowed connection.

The adapter retains the existing canonical prefix/game-scope readers and compact
durable-task writer. The command keeps the existing `admission_intent_id` helper,
registration, HTTP error translation and costly preparation outside its write.
The command gateway remains sole owner of the receipt/savepoint/transaction.
The existing Discovery worker remains sole owner of downstream queue admission.
No schema, queue, feedback, gateway or connection infrastructure changes.

## Lightweight source checks

Run these with the backend test interpreter and existing declared dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend \
  python -B -m pytest backend/tests/test_postgres_discovery_acceptance.py \
  backend/tests/test_discovery_acceptance_boundary.py -q -p no:cacheprovider --rootdir=.
```

The historical `test_postgres_discovery_acceptance.py` filename is retained, but
these are semantic policy/command-composition tests, not PostgreSQL proof. The
stateful fake stores domain choices, intents and task states. The boundary suite
rejects SQL, driver imports/calls and dynamic loaders, and runs all 29 policy
cases in a subprocess that rejects backend infrastructure and service connections.
Route/composition/preparation cases have their separate legacy assembly imports.

## Parent-owned serial PostgreSQL window required

`test_postgres.py` contains 13 native cases: simultaneous distinct operation IDs,
unique revision/choice enforcement, receipt/logical replay, terminal readmission,
five independent stale-scope fences, failures after intent/task persistence and
at completed-receipt persistence, two real lock-order probes, and caller rollback.
The receipt fault is a real fixture-only PostgreSQL trigger; policy tests do not
emulate any SQL. The lock probes observe actual PostgreSQL blocking and NOWAIT
behavior. SQL/binding/transaction claims remain unqualified until these cases run.

This directory deliberately contains no Docker controller. The existing
coordinator owns all resource scheduling and must grant/provision a serial window
before execution. Proposed budget: one existing-image disposable PostgreSQL
container plus one Python process, no Redis/Celery/web/engine, no dependency build
or install; allow 5 minutes execution and 2 minutes cleanup under the
coordinator's normal caps. No Docker probe or native case ran during source work.

The coordinator must provision a fresh database named
`tempo_discovery_acceptance_<32 lowercase hex digits>` on its owned loopback
endpoint, apply the current backend migrations through the existing migration
script, provision the existing reader/writer roles through the existing role
script, and insert default `settings(id=1)`. Use disposable fixture secrets only.
Do not repurpose any live or another task's database.

Inject the resulting connection strings and exact database identity into:

- `TEMPO_DISCOVERY_ACCEPTANCE_FIXTURE`: the database name above.
- `TEMPO_DISCOVERY_ACCEPTANCE_ADMIN`: its fixture admin connection string.
- `TEMPO_DATABASE_READ_URL` and `TEMPO_DATABASE_WRITE_URL`: reader/writer
  connections to that same database, host and port.
- `TEMPO_TEST_INSTANCE=disposable`.

The suite validates these targets before opening a fixture connection; missing
markers or a different database/endpoint fail rather than selecting defaults.
It creates no database, role, migration, cluster, worker or container itself.
The PostgreSQL pool may use at most eight connections; run pytest serially.

With the fixture granted and environment injected, collect and execute exactly:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend \
  python -B -m pytest backend/integration/discovery_acceptance/test_postgres.py \
  --collect-only -q -p no:cacheprovider --rootdir=.
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend \
  python -B -m pytest backend/integration/discovery_acceptance/test_postgres.py \
  -v -p no:cacheprovider --rootdir=. --junitxml=/absolute/task-evidence/native.xml
```

Require all 13 collected identities to execute and pass without skips. Retain
collection/execution output, JUnit, source revision and fixture identity, then
close connections and destroy only the exact parent-owned disposable resource.
Rows survive individual tests for failure inspection; the coordinator destroys
the whole fixture. No native success or merge qualification is claimed by a
successful collection alone.
