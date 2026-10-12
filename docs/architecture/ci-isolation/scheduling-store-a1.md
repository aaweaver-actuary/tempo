# A1 scheduling-store boundary (#204)

`review_service.apply_scheduling_review(database, ...)` retains its signature and
result. It borrows the existing connection through
`OwnedConnectionSchedulingReviewStore` and invokes the scheduling application.
The application owns schedule/result decisions and depends only on five typed
capabilities: source lookup, card-state load, review append, maturity-facts load,
and card-state save. Immutable records carry the existing columns; completion
clock/date and source identity are supplied explicitly. Valid history and optional
opening seed facts remain separate until the application combines them.

The adapter owns SQL translation inputs, not transactions. It opens no connection,
commits nothing, and has no rollback, close, savepoint, receipt or locking API.
Existing PostgreSQL compatibility queries also support historical SQLite callers.
History is appended before maturity is read; state is saved before wrapper
follow-ups. Source replay returns the current card response, skips follow-ups,
and remains distinct from logical review and gateway operation receipt replay.

Satisfied-miss clearing and legacy unlocking stay in the transaction-bearing
wrapper. Gateway transactions/savepoints, card/date locks, request identity,
evidence completion, prepared continuation, receipts and post-commit wakes stay
with their existing owners. Queue extraction belongs to #220. The broader
`review_commands -> main._apply_review` dependency remains: #224 is separate and
incomplete. No public API, persisted shape or migration changes occur here.

The seam removes SQL-shaped persistence from scheduling orchestration, enabling
no-service tests without adding a repository framework or another unit of work.
Rollback is a revert of the A1 extraction commits. Existing durable records and
pending intents remain readable by the original implementation.

Regression names and regular native hooks are recorded in `tests/REGRESSIONS.md`.
Qualification evidence must name the actual head/base/integration candidate;
#217's audit passes characterize an earlier revision and do not qualify A1.
