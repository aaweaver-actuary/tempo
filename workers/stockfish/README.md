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

## Qualification status

This initial interface commit is a handoff, not runtime completion or qualification.
Subsequent commits register owning suites, restricted artifacts, actual exchanges,
resource proofs and measurements. No fixture establishes deployment, current live
Study latency, or C's durable scheduler acceptance.

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
