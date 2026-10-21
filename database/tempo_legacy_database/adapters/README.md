# Legacy backend adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [database_executor.py](database_executor.py) | Single-owner SQLite writer with foreground-priority scheduling. |
| [discovery_acceptance_store.py](discovery_acceptance_store.py) | PostgreSQL Discovery acceptance adapter; never opens or completes a transaction. |
| [defensive_analysis.py](defensive_analysis.py) | Existing defensive settings read and SQL admission/recommendation predicates; task policy and HTTP errors stay with application composition. |
| [frozen_order.py](frozen_order.py) | Frozen-order reads, staging/activation and conditional rollback on the caller's transaction; [Study policy and composition](../../../backend/app/study/frozen_order/README.md). |
| [legacy_game_evidence_scope.py](legacy_game_evidence_scope.py) | Native accepted-scope exclusion owned by feedback persistence. |
| [opening_progression.py](opening_progression.py) | Existing completed-practice SQL predicate and legacy-child update, moved intact. |
| [tactic_admission.py](tactic_admission.py) | Existing daily-introduction count and PostgreSQL transaction-scoped admission lock, moved intact. |
| [postgres_store.py](postgres_store.py) | PostgreSQL connection and SQL compatibility seam for the cutover. |
| [real_game_feedback.py](real_game_feedback.py) | Feedback persistence and legacy queue effects on a borrowed transaction. |
| [real_game_miss_projection.py](real_game_miss_projection.py) | Persistence-only miss projections shared by explicit queue read adapters. |
| [repertoire_lines.py](repertoire_lines.py) | Existing authored-line snapshot query; [Studies conflict computation](../../../backend/app/studies/README.md) lives outside database. |
| [scheduling_review_store.py](scheduling_review_store.py) | Legacy adapter exports; Learning owns the shared implementation. |
| [snapshot_reads.py](snapshot_reads.py) | Existing SQL snapshot/evidence capture helper, relocated intact with native values, C collation, batch and digest behavior preserved. |

- [queue_position_lock.py](queue_position_lock.py): existing queue-position advisory lock boundary.
- [postgres_readiness.py](postgres_readiness.py): existing PostgreSQL/worker readiness adapter.
