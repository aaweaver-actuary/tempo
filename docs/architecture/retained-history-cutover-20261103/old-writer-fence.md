# Disposable old-writer fence contract

Source baseline: `967289c3e9463b5bf6bdd631bb30d18c46df19c4`.
Scope: unselected native PostgreSQL admission/session-denial prototype, F13 subset.
No live roles, services, credentials, source capture or authority transition.

The database owner prototype is
`database/tempo_learning_database/tooling/disposable_writer_fence.py`.
`fence_disposable_source(control, DisposableSource(...), timeout_seconds=5)` accepts
an already-open autocommit administrative connection to a different database.
It accepts no DSN, environment configuration or CLI input. The allocating runner
must own the cluster, bind its `pg_control_system()` system identifier and source
database OID/name, and place matching `tempo-disposable-fence:<32 hex token>`
comments on `postgres` and source databases. Prefix alone is insufficient.
The exclusive controller requires SQL statement/lock timeouts of 1–1000 ms.
Its allocator must exclude competing administrative changes and autonomous
extension/background writers; catalog comments do not constrain a malicious admin.

| Phase | Operator and observable evidence | Failure disposition |
| --- | --- | --- |
| F0 allocation | Disposable runner validates cluster ownership, identity, source OID/comments, separate superuser controller; prepared transactions absent | Held before mutation |
| F1 admission | Database owner commits `ALLOW_CONNECTIONS false` from controller DB | No receipt on failure; inspect actual admission state |
| F2 drain | Terminate all source-database backends, boundedly observe no sessions; recheck admission, source identity and prepared transactions | Held, leave admission closed; no automatic reopen |
| F3 fence | Return `disposable_admission_session_denied` receipt only after final source backend exit | Receipt proves only this disposable DB boundary |
| F4 capture | Separate owner must provide verified post-fence cold/isolated export and final watermark | **BLOCKED: not implemented or demonstrated** |
| F5 activation | Release owner verifies complete parity and privileged/host controls before target acceptance | **BLOCKED: prototype authorizes nothing** |

F3 linearizes at confirmed final source-backend exit, while admission remains
closed. A transaction that commits during F2 precedes that point and belongs in
the eventual final capture. No transaction from a drained session can commit
after success. Revoke, NOLOGIN and password rotation alone cannot establish this.
An exception or controller death after F1 leaves a closed, potentially incompletely
drained source. Retry may finish the same fence; it never reopens it.

Normal final `pg_dump` cannot connect after F1. There is **no supported source
capture integration** here; privileged temporary reopening is not a substitute.
No final watermark, complete migration or Learning activation claim follows.
Before any target acceptance, reopening requires separately verified original
authority and explicit operational authorization. After accepted or uncertain
target commits, stale-source rollback is forbidden; forward recovery or proven
lossless reverse migration belongs to the release owner.

Source inventory: foreground/background workers use `tempo_writer`; API reader
dispatch and beat induce worker writes; normal CLI and alternate Compose can
recreate them. Closing database admission denies those reconnects independently
of command registration or table grants. Existing sessions must be drained.
Direct SQL and stale tasks receive the same database boundary. The backup service
holds `tempo` admin credentials; maintenance and cluster administrators can reopen
the database from another database. Host users can change configuration, replace
volumes or access SQLite files. PostgreSQL admission cannot stop those paths.
Privilege/secret-holder census, administrative isolation, alternate SQLite
authority rejection and restart/configuration persistence remain **BLOCKED**.
The unselected JavaScript binding guard authenticates none of these observations.

T07 owns the explicit native fixture cases in
`services/learning/runtime/tests/test_disposable_writer_fence_postgres.py`:
existing uncommitted writer terminated and unable to commit after return; previous
commits retained; fresh writer/direct SQL/stale-task/restart credentials denied;
privileged login also denied while admission is closed; admin reopening from the
controller DB demonstrates the residual bypass; allocation mismatch and prepared
transactions fail before mutation; incomplete drain produces no receipt.
Use only the explicit file with the existing essential PostgreSQL runner. No
runtime image or broad build is required. Full F13 and F14 remain unqualified.

Architecture impact: no serving boundary change. This tooling prototype is not
registered by any launcher or runtime; existing architecture diagrams remain valid.
