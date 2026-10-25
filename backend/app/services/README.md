# Remaining legacy backend services

**Current unmerged source; organization unfinished.** This flat namespace still contains application policies, concrete workflow composition and background orchestration that need logical owner placement and mixed-function splits. The [202-module checklist](../../ORGANIZATION.md) records actual progress and remaining work. A whole mixed hub moved to a new folder would not close its responsibilities or dependencies.

| Existing cluster | Current examples / canonical extracted leaves |
| --- | --- |
| Games orchestration | `game_sync.py`, `game_sync_coordinator.py`, `game_findings.py`; extracted [Games ingestion/providers](../games/README.md). |
| Authoring / repertoire | `repertoire_coverage.py`, `repertoire_integrity.py`; extracted [Discovery acceptance](../authoring/discovery/README.md). |
| Study queue/review | `queue_application.py`, `review_service.py`, `postgres_queue_refresh.py`; extracted [Study opening/feedback policy](../study/README.md). |
| Analysis / Tactics / Defense | `analysis.py`, `tactical_catalog.py`, `tactical_opportunities.py`, `threat_pipeline.py`; extracted neutral [chess calculations](../chess/README.md). |
| Durable/background operations | `durable_tasks.py`, `background_runtime.py`, `activity_gate.py`; current coordinator/resource ownership must be separated without changing timing or authority. |
| Authored Studies | `study_portable.py` and existing command/route composition; extracted [PGN preview/conflict policy](../studies/README.md). |

Database queries, drivers, DDL, native harnesses and database tooling belong in the single [root database tree](../../../database/README.md). Some legacy composition still imports concrete adapters or other application workflows; it remains explicitly pending and is not an accepted domain boundary. Study must acquire no Games dependency. New domain policy uses only its own internals, explicit contracts and neutral pure libraries; outer composition wires capabilities and lifecycle.

No consolidation is performed during this organization phase. Observed duplication/simplification questions stay in the one [running proposal list](../../../database/SIMPLIFICATION.md). Existing function behavior, SQL, transaction/lock order and resource authority remain the constraints on every split.

## Immediate adaptive-priority callback cessation

**Implemented in this source branch; deployment unverified.** Seven immediate
adaptive-priority requests are removed from the six source paths below. This is
the remaining callback slice from #427 after #431 retired the legacy review
hook. It does not stop internal priority workers or their recovery paths.

```mermaid
flowchart LR
  prefix["Canonical prefix save"] --> definitions["Prefix values, source epoch and stale-score invalidation"]
  prefix --> graph["Existing repertoire game-refresh task"]
  prefix --> seed["Existing coverage seed request"]
  seed --> coverage["Explorer and Maia coverage publication"]
  coverage --> facts["Candidates, completion and progress"]
  coverage --> cursor["Existing native task cursor"]
  game["Native game derivation handoff"] --> completion["Existing per-repertoire cursor and derivation completion"]
```

Concrete owners are [`save_prefix`](../canonical_prefix_api.py),
[`submit_maia_node`](../coverage_maia_commands.py),
[`_publish_node`](postgres_coverage_explorer.py),
[`execute_coverage_node` and `submit_maia_coverage`](repertoire_coverage.py),
[`execute_game_priority_handoff_slice`](postgres_game_priorities.py).
The [coverage storage view](../../../database/tempo_legacy_database/adapters/coverage/README.md)
records the unchanged database owners and transaction boundaries.

Prefix/source certification, graph and repertoire definitions, source-epoch
triggers, coverage mathematics and stored candidates remain unchanged. The subsequent opportunity cut below removes that optional producer and its
callbacks; the earlier #454 receipt is not a claim that those callbacks remain.
Ordinary Study queue/review, frozen ordering and no-score fallback retain their
existing owners; no Study dependency on these optional producers is added.
Existing legacy cross-workflow imports and shared resources remain disclosed
above; this bounded removal does not establish full plugin/resource isolation.

The named regressions in [the regular inventory](../../../tests/REGRESSIONS.md#immediate-adaptive-priority-callback-cessation)
exercise primary prefix/coverage/game effects with adaptive enqueue functions
unavailable. SQLite and semantic fakes are source evidence; native PostgreSQL
locking, installed artifacts and deployment remain unverified.


## Opportunity production and optional API retirement

**Implemented source; deployment unverified.** The optional opportunity producer,
its exclusive storage and foreground state/train command module are removed.
The old code scored gameplay misses, coverage gaps and post-gap findings, refreshed
an auditable feed, and queued optional recommendations. Its eight feed/state/train/
refresh routes and all refresh callbacks/worker registrations are retired with it.
[Last-good source](https://github.com/aaweaver-actuary/tempo/tree/66d98610924971ea4cce3603c76cd4c330b50ae1/backend/app/services/repertoire_opportunities.py)
retains the algorithms and history; no archive or compatibility handler replaces it.

```mermaid
flowchart LR
  study["Ordinary Study queue owner"] --> queue["Existing queue-refresh adapters"]
  queue --> saved["current_repertoire_opportunities: saved weak-decision facts"]
  queue --> graph["Saved graph, card, membership and line facts"]
  builder["Opening graph build/publication owner"] --> graph
  segmentation["Segmentation projection"] --> source["Existing repertoire presence read"]
```

The retained serving boundary is the existing
[opening eligibility projection](../../../database/tempo_legacy_database/adapters/queue_refresh/openings.py),
[admission/resolution writer](../../../database/tempo_legacy_database/adapters/queue_refresh/application/admission.py)
and [capacity projection](../../../database/tempo_legacy_database/adapters/queue_refresh/capacity.py).
Their SQL, stored tables/views and transaction/queue ownership are unchanged.
Normal unranked introduction works without a producer; an already saved active,
non-analysis weak decision remains eligible through the ordinary queue owner.
The exact repertoire presence read formerly shared with opportunity commands now
lives in its actual [segmentation adapter](../../../database/tempo_legacy_database/adapters/authored_repertoires/opening_advisory/segmentation.py),
using the same borrowed connection, SQL, parameters and result.

Graph/repertoire definitions, canonical source epochs/triggers and primary
coverage/game/Study effects remain. The separately owned Discovery acceptance and
recommendation path remains outside this cut. Existing legacy shared-storage and
cross-workflow coupling is still disclosed above; no new ownership or runtime
service dependency is introduced. Source retirement does not delete stored rows,
reset history, remove a graph recovery copy, or perform cutover.

[Named retirement checks](../../../tests/REGRESSIONS.md#opportunity-producer-retirement)
cover physical module absence, normal missing-route/unknown-command rejection,
ordinary queue operation and saved-row eligibility. Five actual admission/cache
cases remain; three Maia/coverage cases move to their regular coverage suite.
Only tests/assertions exclusive to the removed producer/API are retired.
