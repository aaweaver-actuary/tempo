# Game Evidence regression ownership

The installed package's regular `tests/` suite owns legal PGN replay, precise ply
and board side, alternatives versus misses, route ambiguity, Italian/Ruy separation,
shared membership, explicit transposition re-entry, repeated occurrences, custom
FEN, castling, en-passant, promotions, unknown acceptance, invalid provenance,
source bounds and provider cursor/rate-limit behavior.

`integration/test_postgres.py`, executed by `integration/run_postgres.py`, owns
atomic comparison/outbox publication, rollback, duplicate sources, verified-index
reuse, stale source/snapshot fencing, pending/completed/rejected/stale delivery,
lost acknowledgement with fresh-process replay, durable blocked corrections,
real database restart, runtime-role restrictions, read API isolation and provider
source checkpoint/cancellation. These cases run on a newly provisioned component
database. They never accept a live database URL.

`integration/qualify_artifacts.py` installs built wheel and source distributions
in clean Python 3.12/3.14 consumers and runs the same regular tests. No additional
global qualification framework or root regression inventory is required for this
independently discovered component.
