# Learning runtime database tooling

[run_postgres.py](run_postgres.py) is the existing invocation-owned PostgreSQL native runner. From a full repository checkout, use the Learning runtime environment and optionally supply an evidence directory. It retains bounded disposable resources, exact collection/execution/JUnit checks, role provisioning and verified cleanup. The ordinary [runtime qualification workflow](../../../services/learning/runtime/qualify.py) remains application-owned; native proof selection remains [the runtime tests](../../../services/learning/runtime/tests).

`main(..., fixture_profile="full")` retains the default fixture set. Use `fixture_profile="essential"` for explicit core test files or node IDs in `test_runtime.py`, `test_publications.py`, `test_restart.py` and `test_container.py`. Essential installs Learning and its declared dependencies, including shared contract packages, without hashing or installing the Game Evidence producer, Content service or A bridge. Empty selections and directories are refused before resources are created; tests requiring omitted optional fixtures fail with guidance to select full explicitly. Container file and node selections still build the runtime image.

From the repository root, with the existing installed Learning runtime interpreter selected as `python`, one explicit case is:

```sh
python -c 'from pathlib import Path; import runpy; runpy.run_path("database/tempo_learning_database/tooling/run_postgres.py")["main"](test_paths=[Path("services/learning/runtime/tests/test_runtime.py::test_runtime_ready_authorized_and_ordinary_introduction")], fixture_profile="essential", evidence_directory=Path("/tmp/tempo-learning-essential-evidence"))'
```

Choose an absolute, task-owned evidence directory. Each run records its profile, components, source hashes, selected identities and cleanup receipt; no failed run retries with full fixtures.

[restore_rehearsal.py](restore_rehearsal.py) retains the existing explicitly configured disposable rehearsal restore and copied-review identity repair. Docker ownership/disposable labels, endpoint, workspace, empty-target and unchanged-history guards are unchanged. Its SQL and behavior are byte-identical; relocation grants no permission to run a restore or alter a database.

These tools are included in source distributions only. Runtime wheels exclude `tooling/` and `tests/`; offline before/after package comparison verifies the runtime payload. Native and restore behavior was not executed for this relocation. [Source schema documentation](../README.md) describes the stored tables; live deployment remains unverified.
