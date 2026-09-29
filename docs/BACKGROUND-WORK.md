# Foreground-first background work

Tempo treats training, tactics, editing, and active-workspace reads as foreground work. Analysis and derived data are secondary.

## Handler contract

Every background handler must claim one durable item, read a bounded snapshot, close its SQLite or PostgreSQL connection while doing computation or network I/O, then commit one idempotent result through a background connection. It must persist its cursor, retry state, and actionable error before yielding to the coordinator. A handler must tolerate process restart and repeated execution without duplicate cards, findings, issues, or priorities.

Ordinary HTTP requests are foreground by default. Browser workers use `backgroundFetch`, which sends `X-Tempo-Work-Class: background`. SQLite background sections use its short busy timeout and bounded rows. PostgreSQL background transactions use `TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS` (default 250 ms), separately from `TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS` (default 25 ms). The 250 ms ceiling gives headroom over the 8–19 ms populated synthetic measurements while still bounding a stalled section; reassess against a verified production-scale copy before changing it. Both settings are transaction local and reset on pooled connection reuse. These are limits, not a license for long transactions: claim, read, and publish bounded slices, and measure stage timings. No sweep, rebuild, or all-records analysis belongs in startup, queue hydration, or an interactive request transaction.

## Publication and availability

Derived results are staged and published atomically. A running replacement keeps the last published result visible. Newly created or changed analysis inputs stay pending until their generation publishes; a never-validated repertoire is quarantined only for itself. A failed analysis reports its error and remains retryable without blocking tactics or other clean repertoires.

## Adding a handler

Register the handler with the fair coordinator, add a restart/idempotency test, and add a foreground-concurrency regression that completes a review or tactic while the handler is paused between slices. Measure and log foreground wait, database-section duration, slice duration, retries, and preemptions.
