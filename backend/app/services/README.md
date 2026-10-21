# Remaining legacy backend services

**Current unmerged source; organization unfinished.** This flat namespace still contains application policies, concrete workflow composition and background orchestration that need logical owner placement and mixed-function splits. The [202-module checklist](../../ORGANIZATION.md) records actual progress and remaining work. A whole mixed hub moved to a new folder would not close its responsibilities or dependencies.

| Existing cluster | Current examples / canonical extracted leaves |
| --- | --- |
| Games orchestration | `game_sync.py`, `game_sync_coordinator.py`, `game_findings.py`; extracted [Games ingestion/providers](../games/README.md). |
| Authoring / repertoire | `repertoire_coverage.py`, `repertoire_integrity.py`, `repertoire_opportunities.py`; extracted [Discovery acceptance](../authoring/discovery/README.md). |
| Study queue/review | `queue_application.py`, `review_service.py`, `postgres_queue_refresh.py`; extracted [Study opening/feedback policy](../study/README.md). |
| Analysis / Tactics / Defense | `analysis.py`, `tactical_catalog.py`, `tactical_opportunities.py`, `threat_pipeline.py`; extracted neutral [chess calculations](../chess/README.md). |
| Durable/background operations | `durable_tasks.py`, `background_runtime.py`, `activity_gate.py`; current coordinator/resource ownership must be separated without changing timing or authority. |
| Authored Studies | `study_portable.py` and existing command/route composition; extracted [PGN preview/conflict policy](../studies/README.md). |

Database queries, drivers, DDL, native harnesses and database tooling belong in the single [root database tree](../../../database/README.md). Some legacy composition still imports concrete adapters or other application workflows; it remains explicitly pending and is not an accepted domain boundary. Study must acquire no Games dependency. New domain policy uses only its own internals, explicit contracts and neutral pure libraries; outer composition wires capabilities and lifecycle.

No consolidation is performed during this organization phase. Observed duplication/simplification questions stay in the one [running proposal list](../../../database/SIMPLIFICATION.md). Existing function behavior, SQL, transaction/lock order and resource authority remain the constraints on every split.
