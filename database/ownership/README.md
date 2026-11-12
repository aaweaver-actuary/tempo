# Disposable PostgreSQL fixture ownership

[fixture.py](fixture.py) allocates a fresh uniquely named PostgreSQL fixture; it
does not accept an external/live database URL. Its container, role and cleanup
methods are test infrastructure, not a serving Learning/remediation database.
[Backup batching proof](../tests/check_postgres_backup_batching.py) loads this
fixture for bounded source/restore comparisons. The former remediation bootstrap
runner is retired. Do not infer a current runnable bootstrap from historical
fixture helper names; actual target admission and cleanup remain required.
