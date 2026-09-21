# Backend tests

These tests cover the FastAPI application, SQLite workflows, pure service
logic, provider failures, durable background jobs, and restart/idempotency
behavior.

Run `python -m pytest backend/tests` from the repository root. Keep every
user-reported defect in `tests/REGRESSIONS.md` with a specific test name. New
background handlers need a foreground-concurrency regression covering
contention, restart, and replay.
