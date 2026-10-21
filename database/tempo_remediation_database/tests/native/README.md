# Remediation native database proofs

[test_postgres.py](test_postgres.py) owns actual PostgreSQL repository, rollback, replay and bounded-plan proofs. Invoke it through the [owned runner](../../tooling/run_postgres.py), never against an external database. Its application policy fixtures remain in [the component tests](../../../../services/learning/remediation/tests/support.py).

[test_runner.py](test_runner.py) contains the existing five mocked native-runner guard cases. It starts no container or database. The five normal-suite policy cases remain [outside the database tree](../../../../services/learning/remediation/integration/test_runner.py); the component `run_suite.py tooling` explicitly selects both files. Test bodies and parameterizations retain their existing ownership and behavior. These sources are included only in source distributions, not runtime wheels.
