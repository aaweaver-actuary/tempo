# Repertoire restoration increment

This increment isolates browser repertoire preparation and adds independent persisted controls. It does not deploy the change. Operational restoration requires the qualification results recorded with the final candidate.

Source baseline: `3f7332399796366eb32f9fcb5e1b5c678a5b1562`. The live API image at task start was separately verified as built from that revision; earlier audits measured `02b65f229aaccc91ae7c03fd11efc8f3aedd2883` and a different deployment. Historical measurements are not candidate evidence.

## Actual emergency-mode inventory

Study-only is an admission policy, not a Compose service profile. API/web, two foreground Celery children, one background child, beat, defense engine, Maia, PostgreSQL, Redis and backup remain running. Foreground review delivery depends on Redis and PostgreSQL; essential queue publication and operation recovery depend on the background worker. Stopping that worker is not a rollback procedure.

| Paused component / user value | Actual gate and available API behavior | Resources / possible contention | Restart, recovery and control |
| --- | --- | --- | --- |
| Home repertoire preparation / recognize saved alternative moves | Home effect suppresses bulk work; `/api/repertoire/lines` and saved-source move membership remain readable | Browser worker CPU, response parsing, structured-clone memory; previously shared foreground worker | Generation fences prevent stale publication; previous caller abort did not cease computation. New disposable worker terminates on abort; independent repertoire control |
| PGN and authored Study imports / add study material | PGN producer rejects before parsing; `imports.pgn.admit`, `studies.import.commit`, `studies.bundle.import` delivery defers durable envelopes | API parsing, foreground command capacity, PostgreSQL writes; downstream optional derivation | Original operation identity/payload retained; resume existing recovery; optional server control |
| Game sync / obtain new games | Browser producer pauses; server claim SQL and durable delivery gate preserve queued sync work | Provider I/O, background CPU, PostgreSQL, shared Redis; `game_sync_record`, `game_sync_window` | Durable cursors, generations and retry; no provider sample-data fallback; optional server control |
| Game-derived indexes, comparison, findings, misses, events, features and priorities / game evidence | Durable kinds `game_derivation_positions`, `game_derivation_compare`, `game_derivation_findings`, `game_derivation_misses`, `game_derivation_events`, `game_derivation_features`, `game_derivation_priorities`, `repertoire_game_refresh`, `repertoire_priority`, `priority_retention` denied new execution | Background child capped at one CPU, short PostgreSQL sections, memory not explicitly capped; shares essential execution capacity | Fenced slices, leases, checkpoints; pause preserves work; optional server control |
| Defense exercises and threat maintenance / defensive training content | Defensive SQL policy plus optional delivery; `defensive_rubric_audit`, `defensive_admission`, `defensive_threat_report_audit`, `defensive_threat_scan`, `defensive_threat_backfill`, `defensive_threat_validate` | Defense engine one CPU/512 MiB; callback writes share background capacity and DB | Engine admission polling and release/failure bookkeeping; existing defensive-analysis control remains subordinate to emergency/server pause |
| Ordinary game engine analysis, publication and follow-up / analyzed games | Expensive position preparation and engine search admission paused; `game_analysis_publish`, `game_analysis_followup` denied | Engine CPU/memory plus command callbacks; shared API/Redis/DB | Existing durable engine outbox, leases, release/failure and accepted-report bookkeeping retained; optional server control |
| Explorer/Maia coverage and recommendations / coverage evidence | Coverage recovery returns without work, Maia availability/claims pause; `coverage_seed`, `coverage_explorer`, `repertoire_opportunity`, `discovery_recommendation`, `discovery_admission` denied | Provider network, Maia one CPU/768 MiB, callback DB writes; in-flight inference is not instantly interruptible | Leases, heartbeat/release/failure and original accepted envelopes retained; optional server control |
| Opening graph, prefix previews, segmentation and integrity / derived repertoire views | `opening_graph_rebuild`, `canonical_prefix_preview`, `opening_segmentation`, `integrity_scan` and `prefix_transition_application` denied | CPU/traversal and short DB publication; one shared background slot | Persisted generation/cursor and stale-delivery fences; structural intent compatibility unchanged; optional server control |
| Daily statistics and next-opponent profile / derived insights | `daily_statistics`, `next_opponent_profile` denied; statistics and profile reads remain available | Background aggregation and PostgreSQL reads/writes | Existing refresh intents and fenced publication; optional server control |
| Optional queue enrichment / additional prioritization | Existing minimal queue branch skips optional opening/miss/randomization work while retaining essential admission, eligibility, quarantine and publication | Shares background worker and authoritative PostgreSQL | Existing durable phase checkpoints, original generations; server pause selects the same minimal path |
| Activity controls and automatic refresh / status of analysis | Service panel stops optional polling/control actions; automatic game sync pauses | Browser polling/API load; Redis scheduling | Confirmed settings and visibility refresh; pending work remains visible as paused |

Configured limits are not utilization measurements. Compose assigns the background worker one CPU, defense engine one CPU/512 MiB, and Maia one CPU/768 MiB. The foreground worker has two children and prefetch one; background has one child and prefetch one. API, foreground worker, scheduler, web, PostgreSQL, Redis and backup have no explicit Compose CPU or memory cap. Browser preparation has one active dedicated worker, with no browser-enforced memory or CPU quota. Uncapped process memory, shared database I/O/locks and shared-host CPU remain risks; this inventory does not claim pool exhaustion or OOM. The audit's process-local pools have a default maximum of eight connections per process/URL; foreground and background worker processes have separate pools, but share PostgreSQL and Redis.

Periodic tasks remain scheduled: durable poll every second, operation recovery every five seconds, coverage recovery every thirty seconds, daily queue ensure every sixty seconds. Coverage work is gated; essential daily queue and lifecycle recovery remain eligible. Daily backups continue independently. Existing authored cards, tactics, foreground editing/reads, review persistence, receipts and authorized head issuance remain available. GitHub Pages remains a practice demo. New opening-capacity frontier activation, architecture extraction and frontend session coordination are excluded.

Evidence locations: `backend/app/services/study_mode.py` (allowlists/effective policy), `backend/app/tasks.py` (delivery/recovery), `backend/app/services/defensive_analysis.py` and `background_activity.py` (SQL claims), `backend/app/services/postgres_queue_refresh.py` (minimal phases), `backend/app/celery_app.py` (schedule/routing), `docker-compose.yml` (resources), `app/views/home_view.tsx` and `app/hooks/use-game-sync.ts` (browser producers). These describe this branch; inspect the final head before applying operational instructions.

## Ranking and selected increment

Scores are ordinal planning judgments, not measured probabilities. Value and safe-restoration likelihood are 1–5; effort is 1–5. Score = value × likelihood ÷ effort.

| Candidate | Value | Likelihood | Effort | Score | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| Isolated repertoire preparation | 5 | 4 | 2 | 10 | First: demonstrated contested resource, independent cancellation, no authoritative DB mutation |
| Derived daily statistics | 2 | 4 | 2 | 4 | Later: statistics reads already available |
| Bounded game sync/import | 5 | 3 | 4 | 3.75 | Later: parsing, persistence and downstream fan-out require separate qualification |
| Explorer/Maia coverage | 4 | 2 | 4 | 2 | Later: provider/inference interruption and accepted-result lifecycle |
| Broad engine/defense/derived work | 4 | 2 | 5 | 1.6 | Later: shared essential capacity and measured bounded-work requirements |

## Controls and rollback

All new defaults are true and omitted fields preserve stored values, maintaining historical normal operation. Study-only overrides both controls without changing their saved values.

For repertoire-only restoration, save: Study-only **off**, Repertoire preparation **on**, Optional analysis and imports **off**. Defensive analysis's existing setting cannot override either pause. Unknown settings suppress browser preparation. A pending or failed save does not publish effective settings.

To disable only this increment, save Repertoire preparation **off**. The active browser caller/worker are canceled and saved-source move verification remains available. To invoke the emergency fallback, save Study-only **on**. No service restart or code revert is required. Other tabs observe confirmed settings on visibility or the existing periodic refresh; cross-tab transactional session coordination is outside this increment.

To resume existing optional server work, save Optional analysis and imports **on** with Study-only **off**. Existing operation identities, generations, cursors and envelopes resume; do not purge queues or receipts. A release of older binaries requires their compatible schema and cannot be assumed to understand the new independent control.

## Remaining roadmap and delivery evidence

Retain #192 tracking and #190 acceptance. #217/#218/#219 are source and qualification references. #220 queue relocation, #204 scheduling store, #191 settlement and #223 bounded queue work have independent owners. This branch changes admission predicates only; it does not implement those boundaries.

Next increments separately qualify bounded imports/sync, statistics, provider coverage and engine-derived work. Dedicated essential backend capacity is conditional on native offending-workload evidence; a new distributed coordination system is unnecessary.

The candidate handoff records exact source/head/base/integration SHAs, regressions, commands/durations, native latency distributions and resource samples, confidence calculation/thresholds, CI URLs, limitations and cleanup. Until those proofs succeed, implementation status is not operational restoration.

Qualification commands (run sequentially after builds and other heavy checks finish):

```sh
backend/.venv/bin/python scripts/build_restoration_workload.py /absolute/path/public-workload.json
TEMPO_API_PORT=4192 TEMPO_BROWSER_PORT=4193 make ui-file FILE=study-only-mode.spec.ts
TEMPO_RESTORATION_WORKLOAD=/absolute/path/public-workload.json TEMPO_API_PORT=4192 TEMPO_BROWSER_PORT=4193 TEMPO_RESTORATION_QUALIFICATION=1 TEMPO_TRAINING_TIMINGS=1 make ui-file FILE=repertoire-restoration-qualification.spec.ts
TEMPO_RESTORATION_WORKLOAD=/absolute/path/public-workload.json TEMPO_API_PORT=4192 TEMPO_BROWSER_PORT=4193 TEMPO_RESTORATION_QUALIFICATION=1 make ui-file FILE=repertoire-worker-isolation.spec.ts
```

The regular qualification browser case uses four samples per cohort to prove the workflow and evidence driver. It does not assert or claim statistical acceptance. The explicit qualification run uses 1,000 samples in each of two calibration cohorts and control/restoration cohorts, followed by 50 alternating navigation pairs. Ten warmups are excluded. Thresholds are frozen before restoration samples as the fixed-seed 95% upper bootstrap confidence bound on the p95/p99 absolute paired calibration difference. Loaded-minus-control p95/p99 must remain within those bounds; #190's independent 50 ms p95/500 ms stall limits remain required for navigation. Nearest-rank quantiles and every failed/incomplete run are retained. Native worker p99 uses 1,000 baseline and 3,000 loaded observations for each of queue, workspace, tactics and route tasks; it is distinct from real UI progression latency.

Regular browser regressions use repeated legal knight cycles to reproduce capacity contention cheaply. Operational qualification requires the independently generated public legal workload: 8,268 lines, 250,503 indexed positions and 250,436 distinct FEN keys, across ten repertoires. Generation uses seed 190 and sorted legal moves, with varied starting positions after twelve legal plies. Its raw file SHA-256 is `f4ed656f79d73bcd1a5985a7313535af633005d2525d6547e497bc6d3490b7e0`. This represents position count, diversity, validation and transfer demand; it does not reproduce private opening distributions or historical hardware. Historical private inputs are never read by the generator. Review timing starts with the final opening move and includes the product’s automatic completion pause; no additional manual grade action is sent.

The original native capacity defect is reproduced with `TEMPO_ISSUE190_SHARED_BASELINE=1 make view VIEW=issue190_native_cancelled_bulk_releases_foreground_capacity`. The harness compiles app modules directly from pinned main `3f7332399796366eb32f9fcb5e1b5c678a5b1562`, without changing the checkout. Chromium, Firefox and WebKit each failed the 500 ms bound after caller cancellation (12,399.095 / 16,044.740 / 12,683.780 ms respectively). This is a native original-source reproduction, not mocked-worker or live-deployment evidence.


## Restart review corrections (2026-10-13)

The confirmed-save event immediately suppresses both optional browser controls while retaining the last known Study-only value for foreground continuity. A failed, malformed, timed-out, pending or hidden-tab refresh cannot keep old enabled controls effective. A later valid GET restores the authoritative values. Failed or pending saves still emit no confirmed-change event.

Optional server admission reads Study-only and the independent flag from one settings row in one SQL statement and one owned reader. At PostgreSQL READ COMMITTED this is a statement snapshot. The earlier no-reader path recursively rechecked Study-only, so a commit between reader acquisitions alone did not establish the reported authorization race. A commit between the recursive Study-only read and optional-flag read can mix two paused rows `(false,false)` and `(true,true)` into an enabled decision; deterministic actual-row regressions cover that interleaving. The gate remains a point-in-time admission decision: a later pause does not retroactively revoke a slice already admitted. Existing execution-time rechecks and bounded slice contracts remain necessary.

Migration 048 is unchanged. At restart inspection #227 remains open; reconcile if it merges before final qualification. Previous FULL CI 38292491135 qualifies head `5b6464066a64d613124f96acfba80adf35c98b7a`, base `d82f78e9b764fbba5b48e9ea6de8abcca5d133e7`, integration `f08578083043a9cacefb205823fdd171ad0feaff`; it does not qualify these corrections. Operational acceptance remains pending an exclusive slot; no activation or deployment is authorized by functional or CI passes.
