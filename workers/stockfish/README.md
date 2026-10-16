# Independent Stockfish worker

B owns bounded computation, process-local receipts and actual engine termination.
C owns durable jobs, retries, age, accepted results, invalidation, ranking and UI.
Learning owns training effects. No application, provider, database or broker starts.

## Interface handoff

Runtime: `@tempo/stockfish-worker` 1.0.0, Node 26.10.0, existing Stockfish 19 smallnet
WASM plus `nn-61e7af4bb97d.nnue`. Upstream `@lichess-org/stockfish-web` 0.5.0 source
is `931c33470b0c80756c99dfb05008d951aaa8f751`, Stockfish base
`edb0d9db6731067ec50ce619ff372b463bc4dd5d` plus both smallnet patches.
`engine-descriptor.json` defines the computational profile; its canonical SHA-256
forms `engine_version`. Ordinary native Stockfish 19 is a different profile.
Implementation identity separately binds all supervisor/runtime sources.

Target: Linux ARM64, one CPU, 512 MiB whole-worker hard cgroup, no swap, one child,
Threads=1, Hash=32, full strength, no tablebases. No startup downloads. Identity
inspection needs no job execution; supervised health/serve require the enforceable
resource envelope. Other platforms are unqualified.

`serve` consumes NDJSON: exactly `{"model":"StockfishJob","payload":<shared v1>}`
or `CancellationRequest`. Outputs use `StockfishResult`, `JobStatus` and
`CancellationAcknowledgement`. Diagnostics/readiness are stderr JSON. There are
no new shared v1 fields. Commands: `identity`, `health`, `serve`.
`fixtures/synthetic` are parser examples, not observed computation.

Python public imports:
`from tempo_stockfish_client import StockfishClient, stockfish_input_digest, WorkerSessionLost`.
Install the client wheel and shared analysis-jobs wheel without application sources.

```python
async with StockfishClient(command) as worker:  # immutable runtime command argv
    await worker.send_job(job)  # v1.StockfishJob
    async for message in worker.records():
        if isinstance(message, v1.StockfishResult):
            consume(message)
            break
```

`send_cancellation(request)` confirms delivery only. Inspect the matching ack;
`requested` is not termination. Client import is passive. Session/readiness loss
raises `WorkerSessionLost`; the client never retries jobs automatically.

## Input, result and provenance

Digest: `sha256:` plus SHA-256 of UTF-8 `tempo.stockfish.input.v1\n` followed by
compact recursively key-sorted JSON of the complete validated job excluding only
`input_digest` and `execution_generation`. Keep explicit nulls, ordered arrays,
original six-field FEN and clocks. Maia digest semantics are unchanged.

Apply ordered history before root validation. Empty history supplies no continuation;
empty search_moves permits all legal roots. Restrictions are roots, not a forced
continuation. Effective MultiPV is min(requested, permitted roots). At least one
depth/node limit is required; both null is `unsupported_search_limits`. No legal
roots is `terminal_position`. Send original FEN plus full history to UCI.

Return the last complete exact MultiPV iteration after valid bestmove: legal
nonempty PVs, distinct roots, contiguous ranks, one actual completed depth, typed
cp/mate, final-root side-to-move perspective. Do not combine depths or relabel
bounds. A natural node-limited result may be below max_depth. C alone decides
quality (including its equal-depth >=18 comparison). Timeout/cancellation never
publishes partial success. Missing bestmove, illegal PV, malformed/incomplete or
bound-only output and process failure have explicit failures.

## Identity and lifecycle

Workspace/job/request_revision binds all immutable contents. Execution generation
identifies attempts. Invalid/conflicting/resource-invalid requests and speculative
future cancellations cannot advance authority. A valid newer admitted attempt
fences older dispatch/publication. Only one same-request replacement is retained
while its predecessor terminates; unrelated work receives retryable worker_busy,
warming receives worker_not_ready. No hidden product backlog or cross-job preemption.

Exact current-generation completed replay returns original result/timestamps.
Final failures retain attempt identity. After final retryable failure or process/
session loss, C advances execution_generation with unchanged logical contents,
fences old results and deduplicates accepted effects. No durable exactly-once
computation across process loss is promised.

Cancellation IDs are immutable and distinct. Fence publication before callbacks.
Effective cancellation/timeout kills the owned child; confirmed exit is required
before terminated or capacity reuse. Completion-first returns already_completed.
Unknown future targets stay requested until matched or superseded; every retained
ID then receives a supported final acknowledgement.

Limits: 1024 execution/tombstone records; 64 cancellation IDs per target; 64 MiB
serialized receipt budget with result reservation before admission. Exhaustion is
explicit; no promised receipt eviction. Input line/buffer: 64/128 KiB. Output
record/queue: 1/2 MiB. Backpressure pauses input; watchdogs remain active.
Startup/recovery 30 s; termination confirmation 2 s; shutdown/output stall 5 s.
EOF drains at most 5 s, then interrupts. Signals and supervisor IPC loss stop owned
computation. Unconfirmed exit prevents replacement/readiness.

## Qualification boundaries

Owning suites and actual installed-container qualification are registered as
`stockfish` in components.json. Exact source-bound results are recorded in
VALIDATION.md and retained machine reports. No fixture establishes deployment,
current live Study latency, or C's durable scheduler acceptance.

## Initial actual output

`fixtures/observed/exchange.json` is real output from the independently installed
container, including both colors, history, restricted roots, MultiPV, castling,
en-passant, promotion, typed mate scores, cancellation, wall failure, exact replay,
restart and a twelve-job fixture backlog. `provenance.json` binds its actual image,
npm artifact and runtime identity. An independently installed Python contract and
python-chess consumer accepted it. These initial fixtures are available before final
harness registration and full qualification; final evidence will supersede the
initial measurements. The memory-pressure helper, not the engine, was OOM-killed;
its kernel event proves the cap without inventing an engine-specific failure cause.


## Independent setup, build and commands

From the checkout root, install only the worker and its test/client environment:

```sh
npm ci --prefix workers/stockfish --ignore-scripts --workspaces=false
uv venv --python 3.14 /tmp/tempo-stockfish-check
uv pip install --python /tmp/tempo-stockfish-check/bin/python --require-hashes -r contracts/requirements-test.lock
uv pip install --python /tmp/tempo-stockfish-check/bin/python python-chess==1.999 chess==1.11.2
uv pip install --python /tmp/tempo-stockfish-check/bin/python --no-deps ./contracts/analysis-jobs ./workers/stockfish/python
export TEMPO_STOCKFISH_PYTHON=/tmp/tempo-stockfish-check/bin/python
export TEMPO_COMPONENT_PYTHON="$TEMPO_STOCKFISH_PYTHON"
node workers/stockfish/test.mjs pure
node workers/stockfish/test.mjs protocol
node workers/stockfish/qualify.mjs restricted --report /tmp/tempo-stockfish-proof/restricted.json --artifacts /tmp/tempo-stockfish-proof/artifacts
node workers/stockfish/qualify.mjs container --report /tmp/tempo-stockfish-proof/container.json --artifacts /tmp/tempo-stockfish-proof/artifacts --exchange /tmp/tempo-stockfish-proof/exchange.json
# Run serially, after other heavy checks:
node workers/stockfish/qualify.mjs benchmark --report /tmp/tempo-stockfish-proof/benchmark.json --artifacts /tmp/tempo-stockfish-proof/artifacts --exchange /tmp/tempo-stockfish-proof/benchmark-exchange.json
```

The restricted qualifier builds the npm tarball plus client/shared-contract wheels
and sdists from allowlisted inputs, installs with repository sources absent, checks
passive imports/assets/dependency closure and tests installed clients on Python
3.12 and 3.14. It deliberately corrupts a disposable installed engine asset to
prove identity rejection. The container qualifier builds only from its tarball
and Dockerfile; the digest-pinned base image has no Tempo sources or services.
Only labels/names created by the current run are eligible for container cleanup.
Images and immutable artifacts are retained as evidence, not deployed.

To install the runtime without a checkout, unpack the npm artifact into an empty
directory and run `npm ci --ignore-scripts --omit=dev --workspaces=false` there.
Its included npm-shrinkwrap fixes the production dependency graph. Run
`node cli.mjs identity`. Native macOS identity/pure tests are supported inspection;
`health` and `serve` reject an unavailable cgroup envelope. No bypass flag exists.
Build the container from a directory containing only `Dockerfile` and the tarball
renamed `artifact.tgz`. The supported execution command is:

```sh
docker run --pull=never --rm -i --network=none --cpus=1 --memory=512m --memory-swap=512m --pids-limit=64 --read-only "$IMAGE_ID" serve
```

Use the exact image ID and artifact SHA-256 from qualification reports. Install
`tempo_stockfish_client-1.0.0-py3-none-any.whl` and the shared contract wheel in C's
own environment. Construct a complete v1 job, set engine_version from the pinned
identity, compute input_digest with `stockfish_input_digest(payload)`, then validate
with `v1.StockfishJob.model_validate(payload)`. The digest helper accepts a complete
job dictionary without its own digest, validates other fields and preserves them.
The client supports one records consumer; its bounded receive queue is transport
buffering, not a scheduler. A valid result must still pass C's stored request,
current generation, provenance and quality checks before durable acceptance.

`backlog_driver.py --command-json '<argv JSON>' --report REPORT` runs the external
fixture caller independently using installed client/contracts. Class and original
age live in fixtures/backlog.json, outside strict shared job records. It reports
real completions, queue wait and foreground-like latency without claiming live
Study effects or durable fairness. Rendering a report never starts this driver.

## Explicit outcomes and operational limits

Nonretryable input/profile refusals: invalid_request, invalid_record, invalid_fen,
illegal_history, illegal_search_move, duplicate_search_move, terminal_position,
unsupported_search_limits, input_digest_mismatch, unsupported_engine,
identity_payload_conflict, stale_generation, memory_budget_unsupported, and
resource_envelope_unsupported. Invalid requests never advance generation authority.
Retryable execution/admission outcomes: worker_busy, worker_not_ready,
execution_timeout, worker_crashed, worker_shutdown, session_capacity_exhausted and
cancellation_capacity_exhausted. Malformed/incomplete engine output is nonretryable;
an unexplained process exit is worker_crashed, not an invented OOM diagnosis.
Startup/asset/runtime failures prevent readiness and use explicit diagnostics.

A refusal status rejects the received request; it does not rewrite a retained
accepted receipt. Cancellation refusal reasons must not overwrite a completed
execution in C. An excess cancellation ID receives no requested/final ack and is
not retained. Cancellation IDs are bound within a workspace to their full target;
reuse for another target is cancellation_id_conflict. A final failed/cancelled
attempt cannot be revived in the same generation.

The initial enforced platform uses Linux cgroup v2: exactly 512 MiB, finite <=1 CPU,
zero swap. RSS/peak readings are observations, not the memory enforcement mechanism.
Kernel memory reclaim/OOM and OS scheduling are not instantaneous exact-RSS or
millisecond guarantees. UCI's node limit can overshoot between engine polling
checks; report actual nodes, never claim a strict counted-node ceiling. Wall
watchdogs fence results and kill the owned child at expiry, with a separate bounded
termination-confirmation interval; readiness returns only after replacement startup.
Missing bestmove eventually produces timeout or process failure, never a successful
static eval. Complete iterations may precede a partial final iteration; the returned
rank-1 root need not equal that later bestmove. The result explicitly describes the
last coherent iteration, and C owns minimum-depth acceptance.

EOF drains admitted work for up to five seconds, then interrupts; signals interrupt
immediately. Abandoned unknown cancellation targets remain requested until their
job is admitted, superseded, or the process/session is lost. The caller must advance
execution generation after session loss. A lost Docker launcher can leave container
ownership uncertain: operators/qualifiers must use the exact task-owned name/ID,
never broad process-name kills or global cleanup.


## Client environment boundary

`StockfishClient(command, env=mapping)` passes an explicit complete environment to
its owned launcher. The default reads and forwards only PATH, HOME, TMPDIR, LANG,
and LC_* values. It does not inherit TEMPO_, PG*, DATABASE_URL, provider secrets,
NODE_OPTIONS or PYTHONPATH. An explicit mapping is copied; C should supply only
its safe launcher environment. This applies to a direct native worker command as
well as the Docker CLI. Docker containers do not receive C environment variables
implicitly. No C configuration file or database/provider credential is required.
The named `test_B_client_environment_isolation` uses synthetic secret canaries;
it never reads real provider credentials. Callers using the earlier interface
wheel should reinstall the final wheel before passing `env=`.
