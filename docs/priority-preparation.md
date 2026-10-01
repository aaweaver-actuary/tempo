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

Migrations 021 and 022 are required before deploying the worker. Migration 022
marks preparations made by the corrected card-ID ordering rule. An older
schema-21 preparation has a null ordering version and is replaced through the
fenced priority enqueue path. Schema-20 tasks carrying `source_signature`, or
nonzero cursors without a ready preparation, are replaced the same way. This
leaves the last published generation readable while an unverified generation
is abandoned for bounded retention. A cursor-zero preparation without legacy
state can retry after a partial write; a ready preparation resumes staging.
Replayed old leases cannot request further replacements.

Follow the stopped
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
These figures isolate the redundant CPU work. The disposable PostgreSQL
measurement below includes input reads, staged rows, and publication.

## Disposable PostgreSQL comparison

The raw observations are in
[`priority-preparation-2026-10-01.json`](benchmarks/priority-preparation-2026-10-01.json).
Run `node scripts/test-postgres-docker.mjs --mode priority-benchmark` from the
repository root to repeat the measurement. The runner uses a disposable Docker
PostgreSQL stack and stops test-owned background consumers during measurement.
The frozen calculation time is 2026-10-01 00:00 UTC. Each fixture contains two
transposing repertoire lines, repeated shared/transposed cards, completed
coverage evidence, and a personal game position. Both implementations use the
same 64-card fixture and cached Docker image/database process, alternating
baseline and corrected observations twice. The baseline is the exact priority
worker from main revision `86daf0053cdf7cf523956a723e5b309031ba09bd`
(source blob `648abb9b7cf2eb74decc51b3aaeb6a9fd6f21909`). The corrected
revision is `524335671dbc48671f5bc11cca9a603cec6fa645`, from a clean tree.
The container ran Linux ARM64, Python 3.12.14, with four visible CPUs.

At 64 cards the baseline completed in 3.341 and 3.119 seconds, with 65 loader
and calculator calls and 520 input SQL reads per run. The corrected path
completed in 0.114 and 0.083 seconds, with one loader and calculator call and
eight input SQL reads per run. All four runs published identical row hashes,
had no failures or retries, and committed all 64 rows. The corrected 1,000-card
path completed in 0.885 seconds with one loader and calculator call, eight
input SQL reads, 1,000 prepared and staged rows, and 65 bounded handler claims.

The artifact records preparation, staging, publication, transaction and lock
statement durations, committed progress, memory, and eight concurrent
foreground review-row insertion samples per observation. No lock timeouts
occurred. Lock statement time includes execution and possible waiting; it is
not an isolated wait-time measurement. `tracemalloc` excludes native and
PostgreSQL server allocations. Foreground samples measure review-row commits,
not the full HTTP review workflow. The disposable durability check exercises
the HTTP path under background backlog separately.

For local acceptance, `make python-file FILE=backend/tests/test_postgres_priority_preparation.py`
passed 15 tests, the game-completion file passed 6, and the focused retention
test passed 1. The runner test file passed 35. `make docker-durability` passed
its disposable PostgreSQL stages, including the schema-20/21 recovery rehearsal,
second-batch crash/reordered retry, source/scoring invalidation, and HTTP study
durability. The schema upgrade stage took 1.68 seconds. Under its background
backlog, the HTTP review completed in 124 ms and queue read in 4.8 ms; each is
one sample. Complete candidate validation is owned by PR CI under the current
testing policy. The [quality CI run for code candidate
`0070742838804817b424a199e2caff470168a5f2`](https://github.com/aaweaver-actuary/tempo/actions/runs/36867202179/job/110385555283)
completed successfully on 2026-10-01, including the complete application
test step. The [PR checks](https://github.com/aaweaver-actuary/tempo/pull/49/checks)
show validation for the latest documentation commit.
