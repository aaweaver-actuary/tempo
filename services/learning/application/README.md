# Learning review application library

This retained library supplies review application policy through injected
capabilities and a borrowed transaction. The backend and storage owners supply
the concrete writer, scheduling and receipts. It is not a standalone Learning
service or deployment.

The registered component runner has fixed `application`, `tooling` and `native`
suites. The native command is:

```sh
python services/learning/application/run_suite.py native
```

Its fixed test is `backend/tests/test_queue_attempt_abandonment_postgres.py`;
it validates collected and executed identities and JUnit evidence. Native
PostgreSQL prerequisites and task-owned isolation still apply. This suite is not
a substitute for position-plan replay/rollback or assembled Study acceptance.
The application and tooling suites use the same runner with their respective
suite names. Installed-package qualification tests the built library separately.
