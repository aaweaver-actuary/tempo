# Installed Learning review application

`tempo-learning-review` 0.1.0 exposes `tempo_learning_review`, requires Python
>=3.12,<3.15, and has no third-party runtime dependencies. This is an implemented
application library within the still-planned independent Learning deployment.
Legacy HTTP payloads remain legacy payloads; no wire-v1 conformance is implied.

The application owns replay/validation/recovery decisions, review sequencing,
required evidence binding and prepared-successor orchestration. Immutable records
carry the original durable payload, effective review time, local queue day,
local scheduling day, content identity and recorded result. SQL, locks, savepoints,
connection ownership and framework translation stay in the legacy adapter.

| Source / effect | Owner after extraction | Transaction and proof |
|---|---|---|
| HTTP `_apply_review`; receipt/projection/origin replay; guided/revision/deletion decisions | `application.apply_review` | Borrowed transaction; package tests and native replay/stale cases |
| `review_commands.submit_review`; evidence validation/completion, successor decisions | `application.submit_review` | Same borrowed transaction; native evidence/issuance/rollback cases |
| Card-before-queue locks, SQL, required gameplay clearing, scheduling/history/maturity and chronological rebuild | Legacy `LearningReviewAdapter`, existing scheduling store/wrapper and reconciliation helper | Existing scheduler signature/fields unchanged; legacy characterization and native failure/concurrency cases |
| `reconcile_review` handled conflict | Legacy command adapter | Roll back its savepoint before returning; native injected conflict after writes |
| Progression invalidation, maturity queue work and priority refresh | Explicit adapter capabilities | PG durable effects share transaction; SQLite ordinary effects retain post-context timing; replay stays quiet |
| Frozen-order constraints | Existing queue admission/readers | Native active/inactive fixtures; no ranking or private activation changes |

The application never opens, commits, rolls back or closes a transaction. The
legacy command gateway owns transport receipts and its handler savepoint;
reconciliation owns the nested rollback needed before converting conflicts into
normal transport responses. Logical receipts, queue-result replay and scheduling
source-reference replay remain distinct. Scheduling still appends history before
loading maturity facts. The existing scheduler wrapper retains required gameplay
remediation and legacy child unlocking, rather than substituting no-op effects.

Prepared continuation is optional: same local day/cycle/revision, supported kind,
nonblocking successor lock and current admission/issuance must all succeed. A
projection generation is never attempt authority. Historical continuation in a
receipt does not authorize later mutation of invalidated content. Evidence is
completed before successor issuance as before; on logical retry its stored result
may acquire the already-recorded continuation without another review/issuance.

From this directory:

```sh
uv sync --locked --no-editable --python 3.14
uv run --locked --no-editable python -m pytest tests
python qualify.py /absolute/evidence/directory
```

`qualify.py` builds from declared inputs, installs the wheel into separate 3.12 and
3.14 consumers, removes the build sources and runs all application tests under
service import/connection guards. It uses the existing shared Python boundary
checker and records inputs, commands, identities, environment versions and hashes.

Native integration requires only the legacy Python test dependencies, the
installed scheduling and Learning packages, and the existing local PostgreSQL
image; no Node dependencies or model assets. From the checkout root:

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r backend/requirements.txt -r backend/requirements-test.txt pytest==9.1.1
.venv/bin/python database/tempo_legacy_database/tooling/learning_review/run_postgres.py /absolute/evidence/directory
```

The native runner accepts only an evidence directory, creates a uniquely labelled
PostgreSQL cluster/port/secrets/tmpfs and provisions legacy reader/writer roles.
Migrations run only there. It refuses external database targets and verifies
owned-container removal. Advisory post-commit broker wakes may fail when no broker
is running; durable queue intent remains asserted. This proves the application
seam, not broker-free production delivery or independent service authorization.

The merged #275/#277 foundation is incorporated into the backend dependency,
lockfile, test installation, production image and implemented component manifest.
Fresh backend setup installs Learning 0.1.0, scheduling 0.2.0 and chess-core 0.1.0
noneditably. No deferred registration patch is required.

Both qualification runners compare an independent controlled collection with
executed node IDs and all setup/call/teardown phases, plus JUnit results. Missing,
duplicate, deselected, skipped, failed or errored tests fail qualification. External
pytest plugin autoloading and `PYTEST_PLUGINS` injection are disabled.
`qualify.py` rejects any nonempty inherited `PYTEST_ADDOPTS`. The native runner
allows only one `--junitxml` / `--junit-xml` destination, separated or equals syntax;
all other inherited options are rejected before creating a container. Quote paths
containing spaces. The external result file and native evidence copy are retained.

Runner regressions run without PostgreSQL:

```sh
backend/.venv/bin/python -m pytest services/learning/application/integration/test_qualification_runners.py database/tempo_legacy_database/tests/learning_review/test_native_runner.py backend/tests/test_learning_package_installation.py -q
TEMPO_COMPONENT_PYTHON="$PWD/backend/.venv/bin/python" node tools/component-build/test-lane.mjs --component learning-review-application
```

The component lane runs package tests, runner/backend installation regressions and
the native suite sequentially. Both Python entries use `run_suite.py` with fixed
`application` / `tooling` suite choices, independent collection and exact execution
validation. The wrapper rejects inherited `PYTEST_ADDOPTS` and unknown CLI options;
it accepts one explicit `--junitxml` / `--junit-xml` destination in either form.
The shared component runner and its reporting contract are unchanged. `TEMPO_LEARNING_EVIDENCE_DIR` can place native logs
outside the checkout; it does not accept or configure an external database.

#224 remains open for thin HTTP composition, essential worker/runtime isolation;
#194 owns broker-independent foreground delivery. Domain database cutover and full
production wire-v1 adoption are not delivered by this library.

## Native failure and evidence lifecycle

The native runner marks the current summary incomplete before image inspection or
provisioning. A rejected invocation also invalidates any existing current success
record, while a rejected new evidence path creates no database/build resources.
Each invocation keeps logs and collection/execution receipts under
`runs/<identity>/`; previous root summaries/JUnit copies are retained under
`history/<next-identity>/`. The requested external JUnit and the root `native.xml`
copy remain available, but only the current `native.json` qualification result
establishes success.

A creation attempt is tracked before `docker run`, so cleanup also covers a
container created before startup fails. Cleanup checks the exact name, verifies
both ownership/disposable labels, removes only that container ID and verifies
absence. Docker inspection failure is an unverified outcome, not absence.
`container_removed` records actual removal; `container_absent` is null when absence
was not established, and `cleanup_verified` records whether required cleanup was
verified. No creation attempt requires no container cleanup.

Summaries are atomically replaced on success and failure. `pytest_exit_code` is
separate from the qualification `exit_code`; pytest success with a skip, inventory
error or failed cleanup is a failed qualification. `complete` becomes true only
after the complete test inventory and required resource cleanup both succeed.
Primary and cleanup errors are retained together.
