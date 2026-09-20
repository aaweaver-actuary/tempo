# Foreground-first background work

Tempo treats training, tactics, editing, and active-workspace reads as foreground work. Analysis and derived data are secondary.

## Handler contract

Every background handler must claim one durable item, read a bounded snapshot, close SQLite while doing computation or network I/O, then commit one idempotent result through `connection(background=True)`. It must persist its cursor, retry state, and actionable error before yielding to the coordinator. A handler must tolerate process restart and repeated execution without duplicate cards, findings, issues, or priorities.

Ordinary HTTP requests are foreground by default. Browser workers use `backgroundFetch`, which sends `X-Tempo-Work-Class: background`. Background database sections wait for foreground requests, use the short background busy timeout, and are limited to one domain item or a 50 ms/100-row batch. No sweep, rebuild, or all-records analysis belongs in startup, queue hydration, or an interactive request transaction.

## Publication and availability

Derived results are staged and published atomically. A running replacement keeps the last published result visible. Newly created or changed analysis inputs stay pending until their generation publishes; a never-validated repertoire is quarantined only for itself. A failed analysis reports its error and remains retryable without blocking tactics or other clean repertoires.

## Adding a handler

Register the handler with the fair coordinator, add a restart/idempotency test, and add a foreground-concurrency regression that completes a review or tactic while the handler is paused between slices. Measure and log foreground wait, database-section duration, slice duration, retries, and preemptions.
