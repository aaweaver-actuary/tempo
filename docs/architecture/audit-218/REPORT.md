# Tempo runtime resource-isolation audit — issue #218

Audit source: `02b65f229aaccc91ae7c03fd11efc8f3aedd2883` (remote main refreshed on 2026-10-12). Live deployment is a separate revision, `81cad1cb33817484db5d5ce2d80b3286d178d021`; no result from audit main is attributed to that deployment. Audit-only tooling is on `codex/audit-218-resource-isolation`. Production files, schemas, settings, review/queue interfaces and live study data were not edited. The user explicitly authorized temporary live-study shutdown for uncontaminated local measurements.

## Evidence classification

**Source-confirmed:** dedicated server foreground workers already exist; optional and essential durable queue preparation share background worker capacity; PostgreSQL pool identity is process plus database URL, not work class; Study-only gates new delivery but browser caller abort cannot interrupt an already-running synchronous worker handler.

**Runtime-confirmed:** see `RESULTS.md` for final sample counts, latency distributions, resource utilization, failure/recovery observations and named native proof results. Synthetic injected pressure proves resource behavior at a controlled boundary; it does not prove a specific production handler has that duration or ignores admission.

**Historical:** #190's 50 paired real application navigation trials at `643f736b5e728c14f0abdd795eb38314d43a2ebe` reported 33,424 ms median and 46,312 ms p95 usable-Games latency, versus 122/478 ms baseline. The historical response had 8,268 lines and 250,503 prepared positions. These are prior shared-host observations, not new measurements or proof of a backend database cause. Private study contents are not republished.

**Unproven:** production PostgreSQL pool exhaustion, unrestricted process memory causing an OOM, or a current backend handler indefinitely monopolizing a worker. These require an actual offending handler/fixture and native trace. Import coupling to `main` is confirmed but does not establish contention causality.

## Worker and process ownership

The production [Compose definition](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/docker-compose.yml#L1) defines the following topology. Captured deployed commands/resource limits are preserved separately in `deployed-topology.json`; they distinguish configured limits from actual sampled utilization.

| Execution class | Queue / container / concurrency | CPU and memory ownership | Database and admission | Cancellation and recovery |
| --- | --- | --- | --- | --- |
| Browser main/UI | Browser tab, event loop | Shares browser/host resources; no Docker quota | HTTP; no authoritative DB | Attempt/generation fences protect publication, not background compute capacity |
| Browser study computation | Singleton dedicated worker, synchronous message handler | One handler executes at a time; queue/workspace/route/deck/index tasks share it | No PostgreSQL connection; browser-side queue/coalescing | Caller abort settles/deletes pending request but does not send cancellation or terminate computation; worker errors settle callers |
| API | `api`, Uvicorn process and request execution threads | No configured Compose CPU/memory cap | Reader URL; process-local pool; request work classification and shared Redis leases | Requests cannot terminate unrelated browser/server computation; actionable command/receipt errors |
| Foreground command workers | `foreground`, `foreground-worker`, two prefork children | Dedicated capacity; no configured CPU/memory cap | Writer URL, each process's own pool; foreground Redis lease | Late acknowledgment, worker-loss redelivery, durable command/logical review receipts |
| Background durable/command workers | `background`, `background-worker`, one prefork child | One CPU cap; no configured memory cap | Writer URL; admission check before claim and short DB sections | One fenced durable slice; deferred leases/retries; Study-only at delivery; no generic preemptive cancellation of a handler already computing |
| Scheduler | `background-scheduler`, Celery beat | Shares host; no configured cap | Redis publication, no configured PostgreSQL URL | Periodic wakes; scheduler restart re-establishes publication |
| Defense engine | `defense-engine`, engine job loop | One CPU, 512 MiB | HTTP claims/callbacks; DB work occurs in command workers | Admission polled during search; stop/preemption and durable release/failure delivery |
| Maia inference | `maia-worker`, sequential job loop | One CPU, 768 MiB | HTTP claims, heartbeat, submit/failure | Checks mode before next claim; in-flight inference has no matching interrupt checkpoint in the loop |
| PostgreSQL | `postgres` | Shared server CPU, storage and connection limit; separate pools do not isolate these | Authoritative transactional state | Transaction/lock deadlines for background sections; receipts, fenced publication and leases recover interrupted work |
| Redis | `redis` | Shared admission, broker, result backend and coalescing state | Foreground/background lease sets | Unavailability denies optional admission; ordinary command publication still depends on Redis |
| Backup | `postgres-backup`, daily dump loop | Shares database I/O and server resources | Admin direct connection | Independent of Celery; not cancelled by Study-only |

Browser references: [synchronous handler](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/app/lib/study.worker.ts#L11), [caller cancellation](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/app/lib/background-study.ts#L122), [Home preparation and cleanup](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/app/views/home_view.tsx#L688). Engine references: [750 ms admission polling](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/scripts/engine-search.mjs#L72), [release/failure handling](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/scripts/engine-worker-cycle.mjs#L66), [Maia claim/inference/heartbeat loop](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/scripts/maia-coverage-worker.mjs#L97).

## Queue routing, occupancy and recovery

| Task | Broker queue | Essential/optional ownership |
| --- | --- | --- |
| `execute_foreground_command` | foreground | Reviews, head issuance and other foreground commands; some import commands also route here and are mode-gated |
| `ensure_daily_queue` | foreground | Ensures durable refresh intent; it does not execute every refresh slice itself |
| `execute_background_command` | background | Optional results and required checkpoint/release/failure/control bookkeeping |
| `poll_background_tasks` | background | Claims and executes one durable slice, including essential `daily_queue` |
| `execute_background_slice` | background | Compatibility delivery of an already-claimed fenced slice |
| `recover_operations` | background | Recovers both foreground and background operation envelopes; republishes using their original class |
| `recover_active_coverage` | background | Optional coverage recovery; disabled by Study-only |

[Routing and beat cadence](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/celery_app.py#L23): late acknowledgments, rejection on worker loss, prefetch multiplier one; durable poll every second, operation recovery every five seconds, coverage recovery every 30 seconds, daily ensure every 60 seconds. Prefetch limits reservations; it cannot make a running synchronous handler yield. No explicit broker visibility timeout is set here; do not conflate broker redelivery with the application's durable lease deadline.

[Poll execution](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/tasks.py#L299) checks admission, claims one eligible task and executes its handler at worker capacity. [Queue capacity wakes](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/queue_refresh_wakeup.py#L37) also publish to `background`. Thus stopping the entire background worker removes essential queue publication and operation recovery capacity, even when already-prepared reviews continue on foreground workers. Study-only is a classification gate, not a shutdown instruction for that worker.

[Durable task leases](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/durable_tasks.py#L242) default to 60 seconds, reclaim one expired item with PostgreSQL locking, and fence results by task identity/generation/token. [Ordinary failure retry](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/durable_tasks.py#L424) uses a bounded attempt limit and exponential delay plus jitter capped at 60 seconds. [Foreground deferral](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/durable_tasks.py#L519) releases the lease and rolls back the consumed attempt; intentional denial is not ordinary failure exhaustion. [Command recovery](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/command_gateway.py#L157) has a separate retry-cycle contract. Wake coalescing and bounded handlers reduce churn but do not prove there is no starvation at every workload.

## PostgreSQL budget matrix

[Pool creation](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/postgres_store.py#L226) keys pools by `(os.getpid(), database_url)`, with minimum one and default maximum eight. Read and write pools merge when their URL strings are identical. The `background` flag configures transaction policy; it does not select another pool.

| Owner | Configured URL identity | Default pool capacity | Budget interpretation |
| --- | --- | --- | --- |
| API process | reader | 1–8 | Foreground and classified background reads can share the same process pool |
| Foreground child 1 | writer for reads and writes | 1–8 | Separate from background child's pool |
| Foreground child 2 | writer for reads and writes | 1–8 | Separate process, additional capacity |
| Background child | writer for reads and writes | 1–8 | Essential daily queue and optional slices share process capacity |
| Scheduler / model workers | no direct URL | 0 configured pools | Model callbacks consume command-worker capacity |
| Schema/migration/proof/backup processes | admin or proof-specific | Count separately | Direct connections and additional processes are outside the four-process estimate |

The basic active application-process ceiling is **32** pooled connections, with a lazy minimum of four once all four pools are opened. This is a configuration-derived budget, not a measured simultaneous connection count, a hard installation-wide reservation or proof that parent/maintenance processes cannot open more pools. Extra processes, URL variants, replicas and maintenance connections must be added. Capture PostgreSQL `max_connections` and actual process/session ownership before deciding that pool changes are needed.

[Background connection policy](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/postgres_store.py#L248) defaults to a 250 ms transaction deadline and 25 ms lock deadline. It bounds a transaction after acquisition, not time waiting for a pool connection or CPU computation outside the transaction. [Database connection wrappers](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/database.py#L55) enforce classification and acquire short background sections. [The database executor](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/database_executor.py#L229) uses these direct connections on PostgreSQL; the single-owner foreground-priority callback writer is the SQLite compatibility path, not a separate PostgreSQL foreground executor/pool.

## Study-only gate matrix

| Work | Prevented / deferred / allowed / interrupted |
| --- | --- |
| Home bulk repertoire preparation | New effect prevented unless mode is confirmed false. Cleanup aborts the caller and invalidates publication. Already-running native-worker computation continues. |
| Automatic game sync | Producer returns early in Study-only; server claim/delivery gates remain necessary for existing jobs. |
| PGN/study import commands | Producer/command gates pause specified imports. Already-delivered import commands defer their durable operation rather than discard input. |
| Optional durable tasks | SQL claim filter and worker delivery gate defer optional kinds, including raced legacy leased deliveries. |
| Daily queue | Remains claimable. Eligibility, due/study admission, quarantine and projection remain required; selected optional ranking/real-game/randomization phases are skipped. |
| Reviews/head issuance/receipts | Foreground path remains permitted. This does not remove broker/admission/database dependencies. |
| Checkpoints, reports, heartbeat, release/failure/control | Explicit allowlist retains lifecycle bookkeeping and accepted report handling. Allowlisted delivery still uses its assigned queue and admission path. |
| Defense/ordinary engine claims | Prevented by mode before claim; engine polls admission during search and preempts when no longer allowed. |
| Maia claim | Prevented before next job; active inference finishes or errors, with its heartbeat and report lifecycle retained. |
| Coverage recovery | Deferred before execution. |
| Backups | Not an optional feature producer covered by this setting; independent operational service. |

Sources: [policy allowlists](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/study_mode.py#L9), [SQL claim filter](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/defensive_analysis.py#L18), [foreground import delivery](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/tasks.py#L192), [background command delivery](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/tasks.py#L235), [slice delivery gate](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/tasks.py#L379), [minimal queue phases](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/postgres_queue_refresh.py#L784), [automatic sync producer](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/app/hooks/use-game-sync.ts#L85). The source difference from PR #195 must be assessed at this current SHA, not its merge report alone.

## #190 mechanism and remaining acceptance

The critical resource is the browser singleton study worker. The caller posts full repertoire preparation, the native worker announces `running`, then executes synchronously. Navigation/cancellation can reject the old Promise immediately; the foreground request is nevertheless queued behind the still-running computation. Generation guards prevent obsolete publication without releasing CPU/worker capacity. That mechanism has no PostgreSQL connection, Redis admission, Celery lease or retry of its own. It therefore cannot itself explain database-pool occupancy or queue-publication delays.

Historical navigation evidence and current synthetic native-worker results are separate cohorts. Current cancellation measurements require the abort to occur after the actual `running` event, and require the foreground reply ordering to remain blocked behind the obsolete computation. Neither a mocked worker nor a quick caller rejection proves capacity release.

Study-only now prevents scheduling new Home bulk work and uses [saved-source move verification](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/app/views/home_view.tsx#L1084) when the index is unavailable. It does not retrofit preemption into an index handler already running. Keep #190 open: no repair is implemented, representative historical-size current-main navigation and the complete acceptance matrix remain distinct from this synthetic proof.

## Minimal repair sequence and rollback

1. **Classification and admission:** retain daily queue/recovery/control work as essential. Document that stopping `background-worker` stops essential capacity. Correct an actual misclassified handler only after a native case proves it. Benefit: prevents accidental starvation; cost: explicit lifecycle inventory; failure risk: treating reports or issuance as optional. Roll back classification independently while preserving queued envelopes.
2. **Bounded execution slices:** prioritize the demonstrated browser bulk preparation path; capture/compute/publish in bounded units or an independently cancellable bulk worker. Keep foreground position-store state and output parity. Benefit: releases contested capacity; cost: checkpoint/lifecycle handling; risk: stale output or repeated rebuilding. Roll back behind a reversible routing choice, preserving authoritative data and accepted generations.
3. **Cancellation checkpoints:** distinguish settled caller from ceased computation. Bound obsolete work after confirmed start; fence late results and retain diagnostics. Native worker termination may be appropriate for isolated bulk work; terminating the shared foreground worker would destroy unrelated retained state. Roll back cancellation behavior without discarding unresolved foreground requests.
4. **Concurrency limits:** retain existing one-CPU optional-worker caps and bounded task admission. Change a limit only with comparable before/after measurements; more workers can increase database/host contention. Roll back configuration to recorded limits.
5. **Dedicated essential capacity:** consider a dedicated queue-preparation execution slot only if bounded slices cannot satisfy cold-publication deadlines under measured saturation. Foreground reviews already have dedicated Celery children. Coordinate all queue ownership/interface decisions through #217. Roll back routing without dropping queued work or bypassing durable claims.
6. **PostgreSQL budgets:** first inventory aggregate processes/URLs and acquisition waits. Add per-class pools or tighter aggregate ceilings only if shared-pool pressure is demonstrated at a real caller. Cost: extra idle connections/configuration; risk: trading one starvation mode for another. Restore recorded pool settings on rollback; no schema migration is needed.
7. **Process isolation:** a separate cancellable browser bulk worker is justified by the synchronous contested capacity. New backend services/databases are not justified by this audit. Process boundaries do not isolate host CPU, database locks/storage, Redis or memory by themselves.

Do not split or redesign the shared review/queue contracts in this audit. #217 owns those recommendations; references to #204/#220 are dependencies, not permission to extract them here.

## Permanent native qualification design

Proposed regular-suite cases and registry entries (design only; not installed by this audit):

| Named case | Native boundary and precise assertions |
| --- | --- |
| `test_issue218_optional_saturation_preserves_exactly_once_review_and_authorized_progression` | Real PostgreSQL/Celery plus browser. Confirm optional work started, save an issued review, commit within 5 s, observe the exact authorized successor installed; exactly one active review/logical receipt and unchanged receipt result on original-ID replay and new-operation same-attempt replay. |
| `test_issue218_cold_queue_publication_survives_optional_capacity_pressure` | Begin with no prepared successor/projection pending. Inject bounded optional CPU and SQL pressure; due/study eligibility, quarantine and essential projection publication finish within 30 s without draining optional backlog or using stale/sample records. |
| `test_issue190_abort_after_running_releases_bulk_capacity_without_losing_foreground_state` | Native browser worker. Abort after `running`; less than 50 ms p95 paired added foreground latency, no reproducible background stall above 500 ms; settled caller, retained Builder/comparison state, output parity, latest-generation publication and no obsolete diagnostic suppression. |
| `test_issue218_redis_outage_retains_review_intent_and_recovers_without_duplicate` | Real Redis refusal and nonreply faults. Explicit bounded error/pending result; browser retains original envelope. Recovery uses same attempt/operation identity, persists once, and never advances without authorization. |
| `test_issue218_study_only_raced_delivery_restart_and_idempotent_resume` | Queued and already-leased optional kinds before toggle. No discretionary new claim, raced delivery releases lease, essential preparation/review succeeds, restart preserves mode and envelope; disable resumes bounded slices exactly once with generation/lease fences. |
| `test_issue218_worker_loss_reclaims_one_slice_and_rejects_stale_commit` | Kill one audit-owned optional execution child after claim; derive deadline from 60 s durable lease + 1 s poll + 5 s foreground watchdog (66 s for this source). Reclaimed work resumes; old token cannot commit; foreground stays available throughout. Command-envelope recovery uses its own configured lease/poll deadline. |

Place browser/worker behavior in native browser specs, transaction/replay assertions in focused backend/native durability proofs, and admission/backoff policy in existing low-level regression files. Register the final names in `tests/REGRESSIONS.md` in the separately authorized repair. Do not add a test at every layer for the same assertion. Tests retain isolated databases, unique credentials/ports/volumes, real interruption/recreation and observable state-based deadlines.

For performance qualification, use 200 baseline/load observations per stable condition across four alternating blocks with ten excluded warmups; retain all failures. Report p50/p95/max, paired changes and uncertainty. Report p99 only with at least 1,000 comparable observations. HTTP next-head availability does not substitute for visible browser installation latency. CI must qualify the exact later repair candidate; this audit is not a complete release gate.

## Reproduction, restoration and publication

Audit runner: `python3 docs/architecture/audit-218/runtime.py prepare`, then elevated `python3 docs/architecture/audit-218/runtime.py measure`. The runner is specific to this workstation's evidence path and requires a reviewed live-deployment capture; adapt only ownership/evidence locations for another installation. Ports 4182/4183 and project `tempo-pg-regressions-21800-a2182026` must be unused. Fresh runs require fresh secret state and independent output cohorts; do not pool failed runs or reuse mutable database state across invocations.

The runner builds images before interruption, stops the captured application containers before dependencies, creates separate disposable volumes/credentials, saves diagnostics, removes audit state, and starts the original dependency/application container IDs. It verifies original image IDs, service health and a reader-only SQL queue projection. It never invokes an upgrade, live migration, live queue issuance or setting mutation. Interrupted audits restore the same release before reporting failure.

Final delivery adds final `RESULTS.md`, sanitized samples, exact command/duration evidence, artifact hashes, cleanup verification and GitHub update links. Restricted live resource manifests and private historical contents remain outside the published ref. #218 receives the complete findings; #190 receives evidence and remaining acceptance blockers; #192 receives architecture implications. No issue is closed and no production repair PR is created.
