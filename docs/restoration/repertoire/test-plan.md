# Selected validation scope

Base: 3f7332399796366eb32f9fcb5e1b5c678a5b1562. Live API image at task start is built from the same revision; this task does not operate live services.

Changed behavior: isolate the complete repertoire preparation pipeline; cancel obsolete bulk computation without interrupting foreground state; independently pause optional server work with emergency precedence.

Risks: shared-worker dispatch, stale publication, diagnostics loss, worker failure/retry, settings omission, admission races, persisted settings/migration compatibility, receipt replay and queue progression.

Smallest proofs: named worker lifecycle/unit regression, existing alternative-index/Home/worker tests, named backend settings/admission regression. Native real-worker contention/cancellation is mandatory. Stable scope expands to affected callers, typecheck/lint/build, Settings visual, PostgreSQL durability and real browser save/authorized progression/restart.

CI owns final complete candidate validation. Native performance qualification keeps live services running, records shared-host contamination, freezes baseline-derived thresholds before load measurements, and preserves all raw observations. No mock/SQLite proof substitutes for PostgreSQL durability.
