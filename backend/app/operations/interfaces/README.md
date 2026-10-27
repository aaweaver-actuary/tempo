# Operational readiness contract

Owner: #501. `app.operations.interfaces.ReadinessProbe` is the typed callable
capability for a read-only operational observation: `() -> dict[str, object]`,
raising when dependencies are unavailable. Composition chooses the probe and
owns its connection, timeout and process budgets. Policy must not select a
database, inspect Celery or start runtime resources through this interface.

Importing this contract has no application, HTTP, database, environment or worker
startup. It does not export a driver or authorize a state mutation.

```mermaid
flowchart LR
  Root["Composition chooses bounded probe"] --> Contract["ReadinessProbe contract"]
  Contract --> Supplied["Injected operational implementation"]
```

This is a contract checkpoint, **not completed operational decoupling**. The
existing `postgres_readiness.postgres_health` imports Celery and the legacy read
connection. It is not pulled into an import-safe barrel or represented as an
isolated implementation. #501 retains that exact legacy edge for a subsequent
explicit composition migration with focused readiness/failure proof. No handler,
lease classification, queue-readiness rule or live health check is changed here.
