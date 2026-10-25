# Next narrow-production cleanup packets

Source inspected: main `782bf5374b87f223a023568305056415d5b63cea`. Counts below use the original narrow-production membership recovered in [accounting PR #424](https://github.com/aaweaver-actuary/tempo/pull/424), including blanks/comments. [NARROW-NEXT.json](./NARROW-NEXT.json) records every named core path, frozen size/hash, exact mixed-file function spans and protected source hashes. These are source footprints, not an approved deletion manifest or promised net savings. [PR #423](https://github.com/aaweaver-actuary/tempo/pull/423)'s legacy guided-game-review candidate is excluded.

The only currently proven unused cohort is implemented in this branch: **18 narrow lines**, two private read helpers left behind after optional statistics GET routes retired. The larger pipelines below remain active and gated. No source-only analysis settles actual pending work, history or deployment authority.

## Why large earlier cuts barely affected the narrow target

The baseline-to-main accounting is exact: 69,686 → 69,309 narrow lines, net **−377**. It consists of 262 removed ranking-snapshot/adapter lines, 123 net removed statistics route/application/projection lines, and **+8** Learning connection lines. The original narrow target therefore still needs **34,466** more comparable physical lines on current main.

The large cuts primarily belonged to other scopes:

| Exact accounting category, baseline → inspected main | Net physical lines | Narrow target credit |
| --- | ---: | ---: |
| Browser runtime outside the original six-root audit | −11,042 | 0 |
| Test assertions/fixtures/support | −6,436 | 0 |
| Generated qualification/observed receipts | −19,350 | 0 |
| Offline evaluation corpus/synthetic inputs | −9,055 | 0 |
| Other tooling | −1,136 | 0 |
| Build/package/lock configuration | −234 | 0 |
| Prose/licenses and authored inventory metadata | +2,962 and +800 | 0 |
| Broad-only source: browser database adapters, Maia and Stockfish, offset by new Content/Learning source | −2,194 | 0 |

Together with narrow −377 these reconcile to the classified text net −46,062; narrow is included in broad and must not be added twice. The broader production net is −2,571, including 95 new Content runtime lines and 435 lines of historical helper removal. The 19 binary screenshot fixtures account for 2,109,457 bytes, never source lines. No whole-file move adjustment was found in that interval. These quantities do not establish semantic feature retirement or 50% achievement.

## Ranked packets

After the ready residual cleanup, the gated packets are ranked by inspected narrow footprint. Their counts do not include additional shared wiring edits and cannot be summed into a safe-removal target.

| Priority | Packet | Comparable footprint at pinned main | Readiness |
| --- | --- | ---: | --- |
| Ready | Unused retired statistics reads | 8-line file + 8-line function + 2 separator lines = **18** | Implemented here; focused owning checks passed |
| 1 | Opportunity feed/refresh core | 3 paths / **1,617** lines | Active commands, automatic producers and training effects; gated |
| 2 | Adaptive priority calculation/publication core | 6 paths / **1,407** lines | Active queue/scoring/publication callers and stored fallback readers; gated |
| 3 | Daily statistics materialization only | 4 complete files / 166 lines + 18 function spans / 151 lines = **317** | Active HTTP command, worker jobs and stored publications; gated |

### Ready: the two unused reads

Delete `database/tempo_legacy_database/adapters/http_composition/statistics.py` (8 lines), which only defines `daily_insights`. Remove `overview_rows` from `database/tempo_legacy_database/adapters/statistics/storage.py`, original lines 34–41, plus its two separator lines (10 lines). Both are original narrow members. Neither symbol has a current caller; the HTTP module is absent from main's explicit imports and neither package initializer exports it. No dynamic package/function loader or registry consumer was found in the owning application/adapters. Recursive Docker source copying includes files but does not register or invoke either helper. The existing owning README explicitly identified `overview_rows` as unused follow-up work.

The remaining storage AST is identical after removing only that definition. Eight active source hashes are protected in the JSON: main, tasks, coordinator, statistics service/command/native daily worker, canonical feature writer and connections. No live query, public route, command, queue writer, receipt, stored fact, schema, migration or transaction authority changes. The owner READMEs remove the stale link/cleanup statement and distinguish the historical route-retirement receipt from this focused check.

This branch's proposed narrow net is **−18**, not a new result on main. If integrated unchanged, comparable narrow source would be 69,291 lines (cumulative −395), leaving 34,448 against the original target. The original accounting receipt remains immutable.

### 1. Opportunity feed and refresh core

The exact core is `backend/app/services/repertoire_opportunities.py` (991), `database/tempo_legacy_database/adapters/authored_repertoires/opportunities/storage.py` (625), and its namespace `__init__.py` (1). The application owns scoring, bounded refresh and explicit existing-decision training; the database adapter owns complete queries on borrowed connections. All 64 top-level storage functions have direct references from the owning service, so no unused adapter block analogous to the ready statistics cleanup was found.

Incoming consumers are concrete. `main.py` imports the service at 166–170, registers `repertoire_opportunity` at 1224, and retains feed/refresh/dismiss/acknowledge/snooze/training-eligibility/train routes at 2627–2800. `backend/app/opportunity_commands.py` registers receipt-backed state/training operations. `tasks.py` imports/maps its durable work. Automatic refresh callers remain in `canonical_prefix_api.py`, `opening_graph.py`, `game_sync_coordinator.py`, `coverage_maia_commands.py`, `repertoire_coverage.py`, `postgres_coverage_explorer.py`, `introduction_priorities.py`, `postgres_priority.py`, `postgres_game_priorities.py`, `game_commands.py`, `game_analysis_publication.py` and `threat_pipeline.py`; `refresh_requests.py` reads its scoring version. These callers must be removed or preserved explicitly, rather than assuming retired feed UI made the service unreachable.

The core is not a safe whole-file deletion now. `admit_existing_decision` at 965–991 still applies prefix splitting, card introduction and normal queue placement. Keep the separate **956-line admission core** and **572-line foreground/acceptance boundary** while settling their contract; their exact paths are retained in the JSON. `legacy-workspace.tsx` still mounts `PendingDiscoverySaves`, and `discovery-admission-outbox.ts` polls admission status and retries explicit acceptance. The pending-admission maintenance handler at main 1264–1271 can republish saved intents. Deleting producer code cannot silently erase those obligations.

Ordinary queue readers also consume stored opportunities: `database/tempo_legacy_database/adapters/queue_refresh/openings.py:11–13` unions active weak-known-decision cards into candidates, and its joins at 57–61 plus `queue_refresh/application/admission.py:27–30` use `current_repertoire_opportunities` in eligibility/reasons. Stopping feed recomputation does not authorize removing these saved projections or changing locked-card/ordinary admission behavior.

The next bounded expert task should freeze cessation of new optional feed/refresh work, enumerate every automatic enqueuer above, and decide whether training remains or retires under the existing accepted-work/history contract. Require an exact pending-task/browser-intent policy before deleting command or handler names. Preserve authored content, canonical prefix/graph/integrity behavior, queue/history, admission status/recovery and all tables. `authored_repertoires/discovery_commands.py` is shared: `opening_advisory/segmentation.py` imports its `repertoire` query, so whole-adapter deletion would break an additional consumer. Then qualify only the approved slice with the existing opportunity/command/acceptance/queue tests and source-absence runner; #382/#359/#267/#369 authority gates remain separate. No such execution is approved here.

### 2. Adaptive priority calculation/publication core

The exact six-path core is `backend/app/services/introduction_priorities.py` (933), `backend/app/services/postgres_priority.py` (193), and database adapter namespace/`inputs.py`/`jobs.py`/`publications.py` (1/102/105/73). The application owns calculation, evidence selection, retries and publication policy; the root adapters own complete borrowed reads/writes. The 1,407-line footprint excludes shared lifecycle/retention and caller edits, not a declaration that all six files may disappear.

`main.py` imports enqueue/status operations at 155–159 and exposes priority evidence in repertoire readback at 1583. The coordinator imports claim/calculate/load/publish operations at 28–34, enqueues priorities from game derivation and executes the legacy refresh path. `tasks.py:390` registers the PostgreSQL `repertoire_priority` handler. Existing enqueue callers include `canonical_prefix_api.py`, `coverage_maia_commands.py`, `learning_review_adapter.py`, `repertoire_coverage.py`, `postgres_coverage_explorer.py` and `postgres_game_priorities.py`. Enqueueing also schedules `priority_retention`; publication triggers opportunity refresh. The synchronous `rebuild_introduction_priorities` has retained parity/feedback/native freshness test consumers and is not unreferenced code.

Keep `backend/app/services/priority_retention.py`, `database/tempo_legacy_database/adapters/priority_lifecycle/{preparation,retention}.py`, their leases/generation fences and saved publication facts until an exact decision resolves them. The queue readers `adapters/queue_refresh/openings.py:65` and `adapters/queue_refresh/application/admission.py:37` still join `current_repertoire_card_introduction_priorities`. Saved/frozen rank facts and ordinary eligibility/introduction therefore cannot be removed with a recomputation service.

The retained refresh machinery imports the proposed core too: `backend/app/services/refresh_requests.py:14–15` consumes `postgres_priority._priority_source_version` and `introduction_priorities.SCORING_VERSION`; `durable_tasks.py:248` consumes the source-version helper while completing a `repertoire_opportunity` task, taking the source epoch lock before recording publication. That retained completion can fail even when no priority recalculation runs. Database lifecycle `preparation.py:2` imports core `publications.publish_generation`, and `retention.py:3` imports core `jobs.current_job`. A later cohort must preserve or rebind these exact contracts; keeping lifecycle filenames while deleting their imported implementations would break recovery/publication.

The next task should freeze the selected no-recomputation policy and identify the retained ordinary-reader/fallback owner, then enumerate enqueue/publication/lifecycle edits while preserving existing frozen rows and review/queue behavior. Use existing introduction-priority, frozen/unranked, new-cards-per-repertoire, feedback and PostgreSQL publication/freshness proofs after that contract is approved. The source-only #231/Content receipts do not certify actual historical serving authority. This map approves neither a fallback redesign nor data/handler removal.

### 3. Daily statistics only, preserving game-feature derivation

Four wholly daily modules total 166 lines: `backend/app/services/postgres_daily_statistics.py` (69), `backend/app/statistics_commands.py` (32), `database/tempo_legacy_database/adapters/derived_publications/daily_statistics.py` (55), and `database/tempo_legacy_database/adapters/commands/statistics.py` (10). Four daily functions in `backend/app/services/statistics.py` total 75 source-span lines; fourteen daily functions in statistics `storage.py` total 76. The JSON names all 18 functions and original ranges. The combined **317** is a source-span footprint, not a net patch estimate; imports, decorators, separators, namespace retention and shared wiring need an approved exact manifest.

The POST `/api/statistics/daily/{local_day}/refresh` at `main.py:4427` dispatches `statistics.daily.refresh` or legacy enqueue/wakeup. `statistics_commands.py:32` registers it; tasks imports/maps `daily_statistics`; coordinator imports, claims and executes daily work. Activity controls expose those jobs. The saved pending jobs/snapshots/insights have no observed live disposition. The full seven-path statistics core was 510 lines at main and is 500 on this branch after the 10-line residual removal; deleting that entire core would wrongly remove shared feature extraction.

Protect `refresh_game_features`, its input projections and canonical writer. `backend/app/services/postgres_game_features.py` (39) calls it and, under the current lease, writes feature rows, advances derivation phase 6 and enqueues priorities. `database/tempo_legacy_database/adapters/games/derivation/features.py` (35) is the canonical writer. The coordinator also uses feature extraction in its legacy derivation sequence. Those 74 dependency lines are not exclusive daily statistics.

The next bounded task should enumerate daily-only source/wiring removal and freeze handling of already-issued refresh commands and queued jobs, retaining readable historical snapshots/insights unless separately authorized otherwise. Preserve feature extraction, writer/phase/lease fences, callbacks and ordinary queue behavior. Reuse `test_legacy_statistics_database_split.py` plus the affected statistics, durable-command/worker and publication contracts for the approved slice; do not run the entire product suite. No table DROP or live-state inference follows from this packet.

## Validation of the implemented residual

A disposable Python 3.13 environment installed only the six direct dependencies needed by the existing owning checks (pytest, python-chess, redis, psycopg[binary], psycopg-pool, sqlglot). The environment is task-owned and was not a system/application install. With all database/Redis URLs explicitly blank and an unused task-owned SQLite path, the exact existing suite ran:

```sh
PYTHONDONTWRITEBYTECODE=1 TEMPO_DB_PATH=/tmp/tempo-narrow-evidence/unused.sqlite TEMPO_DATABASE_WRITE_URL= TEMPO_DATABASE_READ_URL= TEMPO_REDIS_URL= /tmp/tempo-narrow-checks-venv/bin/python -m pytest backend/tests/test_legacy_statistics_database_split.py -q -p no:cacheprovider --junitxml=/tmp/tempo-narrow-evidence/statistics-owner.xml
```

Result: **2 passed**, zero failures/errors/skips. The tests cover the retained application SQL boundary and SQLite pause/promotion/Study-mode claim controls. Static checks additionally verify no symbol/import/registration consumers, unchanged retained storage AST, original narrow classification and all protected active-source hashes. No unchanged source_delta.py suite, full statistics/application suite, native PostgreSQL/browser/Compose/build, live Study or deployment was run or claimed. Source/header/package and dependency inspection does not itself prove deployment absence.

Independent source review accepted the isolated removal and the two-case receipt; final candidate-map review is recorded in the PR. Historical audits/baselines, FEATURES and RETIREMENT-BATCHES remain untouched, including #423's independent candidate. Architecture impact: none for this residual — its GET routes already retired; the removed private queries had no remaining interface/dependency edge. Active diagrams, runtime ownership and schema/relationships remain accurate and unchanged.
