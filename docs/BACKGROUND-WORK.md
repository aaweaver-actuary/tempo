# Foreground-first background work

Tempo treats training, tactics, editing, and active-workspace reads as foreground work. Analysis and derived data are secondary.

## Handler contract

Every background handler must claim one durable item, read a bounded snapshot, close its SQLite or PostgreSQL connection while doing computation or network I/O, then commit one idempotent result through a background connection. It must persist its cursor, retry state, and actionable error before yielding to the coordinator. A handler must tolerate process restart and repeated execution without duplicate cards, findings, issues, or priorities.

Ordinary HTTP requests are foreground by default. Browser workers use `backgroundFetch`, which sends `X-Tempo-Work-Class: background`. SQLite background sections use its short busy timeout and bounded rows. PostgreSQL background transactions use `TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS` (default 250 ms), separately from `TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS` (default 25 ms). The 250 ms ceiling gives headroom over the 8–19 ms populated synthetic measurements while still bounding a stalled section; reassess against a verified production-scale copy before changing it. Both settings are transaction local and reset on pooled connection reuse. These are limits, not a license for long transactions: claim, read, and publish bounded slices, and measure stage timings. No sweep, rebuild, or all-records analysis belongs in startup, queue hydration, or an interactive request transaction.

## Publication and availability

Derived results are staged and published atomically. A running replacement keeps the last published result visible. Newly created or changed analysis inputs stay pending until their generation publishes; a never-validated repertoire is quarantined only for itself. A failed analysis reports its error and remains retryable without blocking tactics or other clean repertoires.

Opening-graph cleanup reads at most 256 ordered link keys before checking opening
content and membership in the published generation. It removes at most two
obsolete cards per slice. Its checkpoint stops at the second obsolete key, or
the last inspected key when fewer than two are obsolete, so later obsolete keys
remain available after restart. A current-only or non-opening page advances the
cursor; only an empty candidate page ends cleanup. Cleanup effects and the
checkpoint commit together through the current task's generation and lease fence.

Priority retention keeps the active preparation and published/active priorities.
It selects one stale preparation manifest, then locks and deletes at most 16
prepared rows from that exact generation. It removes that manifest only after
an exact-generation lookup proves its prepared rows are gone. Retention locks
the priority job before its task, matching producers and protecting a generation
transition through deletion. Locked stale rows remain pending and are revisited;
a committed slice with no deletions is not useful completion.

Transaction timeouts in `opening_graph_rebuild` and `priority_retention` preserve
their last committed checkpoint and use the existing durable failure backoff and
attempt limit. Lock contention still yields without spending a failure attempt.
`background_slice_timeout` logs identify kind, phase, SQLSTATE and durable retry,
failure or supersession. Celery success acknowledges a handled delivery, while
the durable task state records whether product work is retrying or failed. An
`idle` worker sample records handler exit and does not prove useful completion.

The regular `make docker-durability` background-workload stage runs
`scripts/check_postgres_graph_retention.py` with real PostgreSQL and Redis.
The rehearsal verifies the disposable bootstrap marker and current schema
through a read-only connection before creating its own helper database. It
covers 64,000-card current/stale generations, shared cards and unchanged history,
locked rows, generation replacement, restart, timeout rollback and delayed replay.
The runner stops defense engine, background worker and scheduler consumers during this
stage, then restores dispatch. Recorded query plans execute separately after
measured transactions and are rolled back; their rows describe post-slice state.

Standalone checkpoint HTTP requests validate the envelope and dispatch `opening_evidence.checkpoint` without reading evidence sources or constructing an authoritative manifest. Unsupported PostgreSQL configuration returns the existing structured rejection before dispatch. Semantic validation belongs to the worker: an initial pending operation confirms no persistence, and definitive rejection retains its structured failed receipt.

Standalone `opening_evidence.checkpoint` uses the optional `register_command(..., prepare=...)` hook in `backend/app/command_gateway.py`. The worker first durably claims the original source envelope and closes that write. Preparation reads immutable presentation/scope data and one bounded attempt/event snapshot through an authoritative background read, releases the connection, then validates chess and reduces up to 256 events into at most 20 observations. The prepared result holds copied source data, never a database connection or cursor. Historical saved payloads containing `prepared_manifest` keep their original identity; worker preparation ignores that derived field and revalidates immutable database authority.

Publication locks the attempt and compares its exact header and ordered raw event rows with that source before writing. Changed source raises retryable `SerializationFailure` and recomputes from the original durable receipt; immutable conflicts retain their structured error receipts. Missing events use one bounded bulk insert, while cheap comparisons against at most 20 current observations apply summary deltas and unique clean days. Publication retains the background transaction budget. Preparation is not persisted in the receipt or added to its fingerprint.

Evidence completion attached to `cards.review` keeps its foreground transaction: evidence validation/persistence, aggregate review/reconciliation and evidence finalization commit together. It does not use standalone preparation. The regular PostgreSQL evidence rehearsal pauses reduction with an idle source session and no attempt lock, completes a real foreground review before releasing it, measures publication, and covers stale completion, process exit before publication and exact replay.

Background orphan-attempt verification uses `background_read_connection(authoritative=True)`: admission precedes the connection and evidence SQL, and the read retains the existing background transaction/lock limits. Unmarked foreground diagnostic requests retain their read-only foreground connection, avoiding admission against their own request lease. Both paths materialize at most 256 ordered events and close the read before response decoding. The disposable PostgreSQL/Redis rehearsal proves HTTP admission ordering, historical payload replay/recovery, foreground diagnostics and error cleanup.

## Adding a handler

Register the handler with the fair coordinator, add a restart/idempotency test, and add a foreground-concurrency regression that completes a review or tactic while the handler is paused between slices. Measure and log foreground wait, database-section duration, slice duration, retries, and preemptions.
