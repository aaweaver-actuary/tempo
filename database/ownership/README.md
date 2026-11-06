# Disposable PostgreSQL fixtures

[The remediation test runner](../tempo_remediation_database/tooling/run_postgres.py) creates a temporary Learning database with separate owner and runtime roles. [The PostgreSQL backup comparison](../tests/check_postgres_backup_batching.py) creates source and restored databases in a bounded fixture. Both callers use [fixture.py](fixture.py) for container ownership checks and teardown.
