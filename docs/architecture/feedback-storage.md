# Feedback policy and persistence

The focused backend extraction follows the existing Learning `ReviewStore`
pattern. `services/real_game_feedback.py` consumes typed `MissCard` facts and
semantic operations from `real_game_feedback_ports.py`. It imports no database,
concrete adapter, cursor, SQL fragment, framework or clock. The legacy call-site
composition is `services/real_game_feedback_adapter.py`.

`storage/real_game_feedback.py` implements those operations on the **borrowed
caller connection**. It does not open, commit, roll back or close a transaction.
The existing command gateway still owns command/module receipt atomicity.
Promotion retains card advisory lock, card row lock, current-fact recheck,
burial check, queue-day lock, the existing protected-head queue writer, and the
existing stable priority overlay, in that order. New/locked unintroduced cards
still request canonical admission; no schedule or review is manufactured.
First issuance and accepted-obligation settlement remain with their existing
owners; this port does not replace either hook.

`storage/real_game_miss_projection.py` owns the shared predicate, precise instant
comparison, SQLite function registration and native accepted-scope exclusion.
`storage/legacy_game_evidence_scope.py` owns the latter query; the service module
with that name now contains only semantic identity/reason constants. Complete
parameter-bound card and evidence queries are exposed by `BorrowedFeedbackStore`.
Card identity batches are capped at 128; evidence readers yield at most 128 typed
facts from the same original query snapshot. Existing explicit bulk queue SQL
projections reuse the persistence predicate instead of adding per-card reads.
Those older queue/read modules still contain SQL and are **not** claimed to be
isolated application policy by this extraction.

The compatibility path still takes a snapshot of all event identities before its
short per-event write transactions. Bounded row transfer is not a claim that the
whole historical scope is constant-memory or that the driver uses a server-side
cursor. Durable PostgreSQL worker slicing remains unchanged.

## Proof ownership

- `backend/tests/test_real_game_feedback_policy.py`: fake semantic store decisions,
  effect ordering and explicit application import/SQL guards. Its allowlist covers
  only the two isolated feedback application modules, not the entire backend.
- `backend/tests/test_feedback_storage_adapter.py`: actual SQLite compatibility
  projections, exact instants, unsafe/excluded facts, typed batches and caller
  rollback. No SQL-prefix fake database or policy SQL literal inventory.
- Existing feedback, queue, review and remediation owners retain their behavioral
  assertions. Import changes bind them to the new legacy composition entry.
- Actual PostgreSQL adapter locking, accepted-scope exclusion and command rollback
  remain required native proofs; source/SQLite passes do not establish them.

The extraction is a follow-on stacked on PR311's accepted-scope contract. It does
not change storage schema, scheduler policy, command transactions, live data or
service deployment. Other queue/discovery storage boundaries are separate work.
