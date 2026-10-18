# Prepared real backend native gate — unexecuted

This is test/qualification preparation after the independently source-cleared
producer checkpoint `f8f271b3`. It adds no product feature. Parent holds all heavy
work while the shared Study-activity timeout is diagnosed. No Docker inventory,
container, backend service, Redis connection or native test was run to prepare
this plan. The source checkout is retained for qualification.

## Exact owning cases

`backend/integration/legacy_bridge/test_native.py` mirrors the existing two-case
source vertical scenario and adds one rollback control. It is outside the
documented `pytest backend/tests` selection. Collection must find exactly these
three cases:

1. `test_native_generated_accept_replay_restart_and_normal_study`: migrate a
   fresh backend database through50 using the **installed** remediation schema;
   configure actual reader/writer roles; seed one introduced original full
   physical card, a competing Italian route, one completed synthetic game, an
   unrelated protected head and ordinary card. Real backend read capture →
   installed A projection/GE v3 producer → actual HTTP/Celery/background command
   → durable operation/module receipts. Retain/discard the first caller response,
   restart a real worker, replay the original operation and use another transport
   identity without another contribution/frontier. Explicit HTTP queue admission
   must preserve the protected head. Normal GET prefetch must not bind the
   target; complete the protected card through normal HTTP review, issue the
   target through normal GET, then submit/replay a qualifying unaided review.
   Require one binding, target scheduling review, review receipt and settlement;
   ordinary card history remains unchanged. Read-only API has no write URL and
   cannot INSERT; operation rows distinguish background intake from foreground
   queue commands. Nothing invokes a handler directly or enables eager Celery.
2. `test_native_prior_issued_attempt_cannot_settle_later_acceptance[prior]`:
   obtain a genuine target issuance through normal GET **before** acceptance.
   Accept the generated delivery; explicit queue must defer with
   `issued_head_preserved`. A later correct review on that old attempt can save
   normal scheduling but creates zero module bindings/review receipts/settlements.
   The accepted obligation remains unsettled.
3. `test_native_module_effect_and_complete_operation_receipt_roll_back_together`:
   a trigger in this exclusive disposable database raises SQLSTATE40001 only
   when the exact test operation attempts to publish `state='complete'`. Real
   HTTP/Celery preparation/receiver execute. Before throwing, the trigger requires
   the exact expected completed acknowledgement (including its real module-owned
   workspace/evidence revision), in-transaction contribution, frontier1 and
   matching completed module receipt. It then raises a unique
   per-case marker. The test must find that marker and `SerializationFailure` in
   the persisted `last_error` before removing the trigger: an unrelated timeout
   with zero rows cannot pass. The failed writer transaction leaves
   zero module/frontier effects and no completed response, while its durable
   retry envelope survives. Remove the owned trigger, wait for its actual retry
   deadline, then explicitly replay the same preview/key; require one acceptance.
   There is no production fault hook, mocked transaction or fabricated receipt.

All three use real PostgreSQL queries/triggers/constraints, Redis broker/results
and foreground admission, one actual API process and dedicated foreground and
background solo command workers. No beat, provider, engine or periodic worker
is started. The queue's own existing bounded wake behavior remains enabled.
Every case creates a fresh39-character database name and namespaced broker/
admission keys. No ambient URL or host port is accepted. Graph/integrity rows
are coherent **synthetic completed owner facts**; this gate does not independently
qualify the graph builder/integrity scanner or fabricate Content activation.

The first case alone requires actual API → worker → durable receipt roundtrips
for intake, queue, issuance and review. Source scripts and the five module-native
cases cannot substitute for this result. Reconciliation/invalidation, stale
source, contention and query-plan claims beyond these assertions remain under
their focused owners and require additional evidence when selected.

## Finite command and aggregate caps

Pure preparation inspection (no resource access):

```sh
python scripts/qualify_legacy_backend.py --describe
```

After a separately approved helper diagnostic, successful same-head284-case
artifact run, review and a **new explicit backend-native grant**, use:

```sh
node scripts/legacy-bridge-window.mjs \
  --python /Users/andy/Documents/Codex/2026-10-18/task-2/tempo/backend/.venv/bin/python \
  --mode backend-native --approved-window \
  --artifact-run EXACT_SUCCESSFUL_ARTIFACT_RUN_DIRECTORY \
  --evidence /Users/andy/Documents/Codex/2026-10-18/task-3/evidence/qualification \
  --helper-image-id sha256:a095f09e905ecb00635b80387e17d195560a5752df83d970f94f1ada902ba3e2 \
  --postgres-image-id sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336 \
  --redis-image-id sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499
```

The Redis ID is a coordinator-supplied **candidate**, not verified by this task;
its actual image/declared-volume provenance awaits an authorized preflight. A
mutable tag is rejected.
There is no pull/build/download/install fallback. The runner refuses absent or
incomplete artifact receipts, different head/image, an inventory other than284,
changed artifact hashes or a dirty checkout before allocation.

Allocation: **15 minutes work +5 minutes verified cleanup**,20 minutes all-in,
one attempt, stop at first failure. Existing development budget/admission owner,
managed Docker, shared `Deadline` and Learning's exact pytest inventory checker
are reused. This does not introduce a general qualification framework.

| Owned allocation | CPU | Memory; no additional swap | PID limit | tmpfs |
| --- | ---: | ---: | ---: | ---: |
| Helper: test driver + API +2 solo workers | 1 | 768MiB | 128 | 256MiB `/tmp` |
| PostgreSQL | 0.75 | 384MiB | 128 | 384MiB `/var/lib/postgresql` |
| Redis | 0.10 | 64MiB | 32 | 32MiB `/tmp` +32MiB `/data` |
| **Aggregate** | **1.85** | **1,216MiB** | **288** | cgroup-accounted |

This stays within the existing2CPU/2,048MiB development budget. Each service's
`memory-swap` equals its memory cap. Redis has32MiB maxmemory/noeviction and no
snapshot/AOF. PostgreSQL has32 connections/32MiB shared buffers. Helper verifies
its cgroup caps; host verifies service caps. One owned **internal** network,
zero published ports/volumes. Source and qualified artifacts mount read-only;
only the new evidence directory is writable. No Docker socket, live credentials,
provider URL or live data mount enters the helper. API receives reader-only
connection configuration; workers receive only the generated disposable writer
role. Test passfiles are0600 and removed after verified cleanup.

Image preflight reads actual `Config.Volumes` before allocation and requires each
declared destination to be covered by an explicit tmpfs/bind. Redis's declared
`/data` is explicitly replaced by a32MiB tmpfs. Actual container `Mounts` and
`HostConfig.Tmpfs` must match the plan, including sizes, binds and read-only flags;
any persistent volume or extra mount fails qualification. Both inventories are
retained, including during partial-start cleanup. No broad volume listing is used.

The helper retains the same image/Python ABI as artifact qualification. It imports
qualified installed distributions first and the current backend source explicitly
under isolated Python. Existing image site-packages supply only the additional
legacy backend dependencies; no package is installed in this window. Startup
records the actual package origins and exact dependency versions. It also hashes
each installed package/code/data file against the corresponding verified wheel,
rejecting missing, changed or extra package files. Installer-rewritten `RECORD`
is excluded. Generated bytecode caches cannot substitute for verified source:
all native processes use a fresh task-owned cache prefix and `-B`. This bounded
check covers only the six already-qualified distributions, installs nothing and
introduces no separate provenance store. The existing
installed legacy runtime dependency is not started as a separate Learning core.

## Image provenance and missing prerequisites

| Input | Provenance / status |
| --- | --- |
| Helper `a095f09e…` | Already-local backend API image selected in the previous granted attempt. Its image inspect succeeded; Python execution/startup did not. Repository `backend/Dockerfile` is the expected dependency source, not proof of this image's installed contents. Diagnostic/runtime preflight must verify. No rebuild is implied. |
| PostgreSQL `74935e72…` | Parent-provided immutable local PostgreSQL candidate already pinned by the module-native plan; repository compose uses18.6-trixie. Native window must inspect actual image/architecture. No mutable-tag substitution. |
| Redis `858f009f…` | Coordinator supplied immutable local candidate `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499`. Repository compose specifies `redis:7-alpine`; actual ID, architecture, declared volumes and container mounts must be verified in the later authorized preflight. No Docker probe occurred here. |
| Qualified artifacts | No successful284-case receipt/environment exists yet. The consumed255-case attempt produced zero artifacts/tests. All12 hashes and same source head/image are required. |

Native dependency preflight requires backend's pinned FastAPI0.116.1, FSRS6.3.2,
Celery5.6.3, psycopg3.3.6/pool3.3.3, Redis6.4.0, sqlglot30.19.0,
multipart0.0.20, Uvicorn0.35.0 and httpx0.28.1. It also checks pytest9.1.1,
chess1.11.2 and Pydantic2.12.5 from the qualified environment, existing
scheduling0.2.0/review0.1.0/legacy-runtime0.1.0, and the six qualified distribution
origins. Host needs only the existing Python runner, Git, Node and Docker CLI.
Actual helper contents remain **unverified**, not assumed present. A missing or
different dependency stops; this grant would not authorize installing it.

## Evidence and cleanup

Collection/execution inventories must match the three **exact full pytest node
IDs**, including `[prior]`, pinned in `CASES`; a different same-count selection
fails. JUnit must match with no
skip, deselection, duplicate or missing phase. Retain case capture/observation/
preview, real operation/queue/status traces, worker/API logs, imported artifact
origins, migration output, image/cap inspection and exact source head. Whole
window completion requires both tests and cleanup; no partial inventory passes.

Each fixture terminates only its created process sessions, verifies its database
marker before drop and removes its private passfiles. Host then stops/removes
the exact labeled helper **before** Redis/PostgreSQL, removes its labeled internal
network and independently verifies exact absence. Exact owned container removal
uses `--volumes`, then every volume name observed on those containers must be
independently absent before `cleanup_verified` can become true. This is the
[Docker-owned anonymous-volume removal behavior](https://docs.docker.com/reference/cli/docker/container/rm/);
named or unrelated volumes are not separately deleted. A surviving volume or
unavailable/foreign absence diagnostic blocks cleanup. Shared repaired supervision
reaps failed wrapper descendants; exact absence reconciliation preserves other
owners. Unverified cleanup retains the ownership report/journal as a blocker.
No broad prune, daemon restart or live/shared-resource change is allowed.

Affected source owners from `f8f271b3` retain their passing evidence. Only new
plan/refusal checks and native collection are run during preparation, plus
syntax/conflict/whitespace checks. Do not repeat broad source suites to replace
the missing native boundary. Qualification remains entirely pending.
