# PostgreSQL priority preparation

Priority jobs keep the existing generation and publication pointer. Each
PostgreSQL worker verifies its lease and job generation before reading sources.
A preparation manifest records source epochs, scoring version, and a frozen
calculation time. Triggers advance a repertoire epoch for local changes and a
shared epoch for game history and settings. The worker checks both after input
reads, between preparation writes, and under row locks at publication. A
changed epoch queues another generation through the existing priority enqueue
path. An unrelated repertoire edit does not invalidate this one.

The calculator runs once for a stable generation. Prepared rows are written in
short transactions and become reusable only when the manifest is marked ready.
A crash before that checkpoint may repeat calculation. Later claims stage at
most `TEMPO_PRIORITY_STAGE_BATCH_SIZE` rows per transaction (default 16,
clamped to 1–64). The durable task cursor advances in the same transaction
as each stage batch. Publication checks the prepared and staged counts and
atomically switches the pointer. Readers continue to use only that pointer.
Retention removes superseded preparations and generation rows in bounded
slices, preserving the published rows and an active preparation.

The source epochs cover the calculator's repertoire lines, linked opening
cards, coverage runs/nodes/candidates, imported games and positions, decision
misses, study reviews, and coverage horizon/path-floor settings. The existing
coverage completion and game handoff signals request refreshes; review
completion now requests one for every opening card, including reconciled
reviews. Settings and repertoire edits already request coverage refreshes,
which in turn request priorities. The epoch fences prevent an in-flight
calculation from publishing while those follow-ups are pending.

Migration 021 is required before deploying the worker. Follow the stopped
writer backup and migration procedure in
`docs/STARTUP-BACKGROUND-RECOVERY.md`; the schema version gate prevents an
old worker from running against the new schema. Roll back code and database
together from the verified pre-upgrade backup. Prepared rows are derived data;
they are not user study records. The shared epoch serializes short game-history
and relevant settings writes; repertoire epochs serialize changes to the same
repertoire. Measure lock waits and foreground review latency on a disposable
PostgreSQL stack before rollout.

## Local CPU comparison

On the audit revision `9dec8735`, an ARM64 Mac with a warm Python cache,
100 synthetic cards, no lines or external evidence, and no database reads:
the old per-card-slice calculation/hash loop made 101 calculator calls in
2.295 seconds with 0.09 MiB peak traced allocations. One calculation/hash
took 0.021 seconds with 0.06 MiB peak traced allocations. At 1,000 cards, one
calculation/hash took 0.210 seconds and 0.73 MiB peak traced allocations.
These figures isolate the redundant CPU work; input reads, PostgreSQL staged
rows, transaction latency, and end-to-end speed were not measured because
the disposable Docker stack was unavailable in the sandbox.
