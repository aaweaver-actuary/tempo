# Game Evidence adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [store.py](store.py) | Game Evidence's transaction owner. No cross-domain database access. |
| [analysis_export.py](analysis_export.py) | Existing consistent source reads, immutable export/page reads and cursor-batched publication writes. |
| [delivery.py](delivery.py) | Existing outbox claim/fence, receipt persistence, inspection and original-delivery requeue queries. |
| [providers.py](providers.py) | Existing import/provider lease, raw source checkpoint and cursor persistence queries. |
| [reconciliation.py](reconciliation.py) | Existing accepted-head, desired-state job, receipt-history and reconciliation state queries/savepoint. |

The category modules execute complete parameter-bound statements on a caller-supplied connection. They neither acquire a connection nor commit a transaction; `store.py` retains the existing connection owner. Write helpers do not return driver cursors. Driver adaptation (`Jsonb`), explicit SQL row locks, cursor batches and savepoints stay here. Query text, lock order, bounded reads and migration/schema definitions are unchanged by extraction.

[Application composition and decisions](../../../services/game-evidence/README.md#database-operation-location) remain outside the database tree. Existing outer orchestration retains transaction scope, provider/HTTP work, receipt validation, retry/status decisions, chess traversal and immutable-export/cursor validation. This physical split does not certify live grants, deployment/resource isolation or complete domain-interface adoption.

`store.py` delegates import preparation, validated snapshot cache choice, snapshot preparation/staleness/affected-position computation, comparison preparation/receipt/report construction and read/import-status/list-input shaping to that owner's `source.py`, `snapshot.py`, `comparison.py` and `reporting.py`. `worker.py` owns the existing job control flow; `record_job_failure` retains the original lease-fenced SQL and connection scope. `reconciliation.reconcile_comparison` owns the existing request/finalize pair on the borrowed publication transaction. Snapshot computation retains its points before connection acquisition and after the locked prior-document read; the adapter retains immutable identity conflicts and snapshot/epoch/queue writes. Report construction retains its existing point after the callback and locked publication fences, immediately before persistence; import-status shaping retains its point after closing the read scope. Method signatures, private per-worker cache tuple, replay shortcut and transaction/query order are unchanged. The application README records the remaining import-metadata, retry-interval and compatibility-cache audit items; these moves do not claim complete root-store separation.
