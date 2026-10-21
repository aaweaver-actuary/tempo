# Remediation database tooling

[run_postgres.py](run_postgres.py) retains the existing invocation-owned PostgreSQL harness. Run it from a full repository checkout with the Remediation component environment; its optional argument is an evidence directory. It refuses external database targets, applies the [owner migration](../ddl/migrations/001_remediation.sql), and invokes the [native proofs](../tests/native/test_postgres.py) through the existing Learning inventory checker. Fixture creation, ownership checks, cleanup and receipt publication are unchanged.

The harness and database tests are source-distribution inputs only; runtime wheels contain neither `tooling/` nor `tests/`. Relocation was checked offline and with [mocked runner guards](../tests/native/test_runner.py); no native fixture, database, restore or live schema was verified.
