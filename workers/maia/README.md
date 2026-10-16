# Independent Maia worker

`@tempo/maia-worker` 1.0.0 performs offline inference using Tempo's existing bundled ONNX model. It owns computation and process-local execution receipts. Intelligence (#109) owns durable requests, accepted evidence, caching and publication. The worker has no database, broker, provider or foreground Study integration.

## Build and run

Requires Node 26.10.0. From this directory, install only this component:

```sh
npm ci --ignore-scripts --workspaces=false
node build.mjs --out dist
mkdir -p dist/image
cp dist/tempo-maia-worker-1.0.0.tgz dist/image/artifact.tgz
cp Dockerfile package-lock.json dist/image/
docker build -t tempo-maia:1.0.0 dist/image
docker run --rm -i --network=none --cpus=1 --memory=1g --memory-swap=1g --pids-limit=64 --read-only tempo-maia:1.0.0 serve
```

The digest-pinned Node image extracts the tarball and performs a production-only locked installation. `build-inputs.json` declares every external source asset and authoritative schema with SHA-256. Packaging reconstructs six local model parts, checks every hash, and includes only owned runtime code, descriptor, model, vocabularies and five unchanged v1 schemas. No model download or whole-repository dependency installation occurs. The artifact runs with `app/`, `backend/` and repository sources absent.

`node cli.mjs identity` verifies installed model/vocabulary bytes and library versions without creating a session. `health` initializes one usable model process and exits after reporting identity/resources. Without an enforceable cap, native health prints `ready:false`, identity and resources, then exits 1. `serve` waits for readiness, then consumes newline-delimited JSON. Native identity is available; serve rejects environments without a verified finite Linux memory cgroup. There is no bypass flag. Public `createModel`/`infer` are low-level computation APIs; they do not apply supervisor deadlines or memory admission. `MaiaWorker` and `serve` are the enforced job boundary.

## Protocol and lifecycle

Inputs contain exactly `{ "model": "MaiaJob", "payload": <MaiaJob v1> }` or the corresponding `CancellationRequest`. Output uses this record shape for `JobStatus`, `MaiaResult`, and `CancellationAcknowledgement`. Payloads follow `tempo_analysis_jobs_contracts.v1`; undeclared fields fail. Operational diagnostics are separate stderr JSON records, including `ready` and `execution_started`. Importing the public interface starts no polling loop or inference.

One prewarmed child holds one single-thread WASM session and executes at most one job. Admission emits pending/running statuses; the supplied wall deadline bounds accepted execution and terminates the child on expiry. Cold initialization is separately bounded at 120 seconds and finishes before readiness. Busy or warming workers return explicit retryable failures; callers own admission and retry scheduling.

Logical identity is workspace/job/request revision. All immutable contents, including FEN, requested moves, model, constraints and input digest, bind to that identity; execution generation is fenced separately. An exact completed replay returns the original result, including timestamps. Conflicting identity contents fail. Receipts are bounded to 1,024 execution records per process lifetime; exhaustion fails explicitly without evicting promised replays. Every final failure remains bound to its generation, including `worker_busy`, `worker_not_ready` and `execution_timeout`: replaying that generation returns its final attempt status; another computation requires a higher execution generation with the same immutable payload. After process/transport-session loss the requester **must advance execution generation before re-execution**, preserving all other immutable fields, because v1 has no session identity or recovered receipt. Its durable consumer must reject prior-generation results and deduplicate accepted effects. No durable exactly-once execution claim is made.

Cancellation waits for actual child exit before reporting `terminated`; late output is fenced. Completed work returns `already_completed` and obsolete generations return `stale_generation`. Unknown/future targets remain `requested` until their matching job arrives. Up to 64 distinct cancellation IDs are retained per unresolved generation; excess IDs receive explicit retryable `cancellation_capacity_exhausted` without acknowledging or changing cancellation state. Every retained ID receives its final acknowledgement. Killing execution releases capacity; a replacement model prewarms before readiness returns. EOF drains bounded work and shuts down. Signals terminate owned execution. Input lines are bounded to 64 KiB, total buffered input to 128 KiB; stdout backpressure pauses input.

## Model capabilities and probability meaning

`model-descriptor.json` binds model/vocabulary hashes, computational schema, runtime/chess versions, tensor shapes, ratings, limitations and unknown provenance. Model SHA-256 is `405bf76c15727dad8728b352c06a8f3c1b80fb2760e8d666b32485c63d75b856` (45,683,686 bytes). Move/reverse vocabulary hashes are `e3351a233174e99d9ddca981681b65a444d191e8a6b474e27bd8fad8514af8d7` and `bb9542b706d3011aa8c0bb067dd33b50bbd7313414cfb50052b0c92aaa9833ee`. Descriptor-derived model version changes with computational descriptor inputs; effective implementation identity also binds owned code and actual runtime context. Original upstream checkpoint/export revision remains unknown.

Runtime: ONNX Runtime Web WASM 1.23.0 and chess.js 1.4.0. Float32 inputs are `tokens [1,64,12]`, `elo_self [1]`, `elo_oppo [1]`; outputs are `logits_move [1,4352]` and `logits_value [1,3]`. Supported ratings are 1100–1900 in steps of 100. **MaiaJob v1 has one rating, supplied to both ordered model rating inputs.** Distinct player ratings and time-control conditioning are unavailable in v1. Unsupported ratings are rejected without rounding.

Requested UCI moves must be distinct, legal, and exactly representable in the vocabulary. Local position coherence, castling and en-passant consistency are checked; this does not prove retrograde reachability. Black-side color/rank normalization restores original move orientation and preserves promotion suffixes. Missing or duplicated vocabulary decoding fails.

Stable binary64 softmax normalizes actual logits over **exactly the requested legal set**. A subset yields conditional probabilities; omitted legal mass is not zero. Supply every legal move for a whole-position distribution. Nonfinite logits, wrong tensor shapes, missing/extra moves and invalid mass fail. Legitimate exponential underflow stays explicit numeric zero. Terminal positions return nonretryable `terminal_position` status because v1 has no valid empty successful result. Predictions are model outputs, not observed game frequencies or Learning utility.

## Resources and qualification

Supported profile: one CPU and a whole-worker 1 GiB hard memory cgroup, including supervisor, model child and WASM memory. Jobs below the supported 1,024 MiB floor, requests below the active cap, and unavailable/unlimited caps fail explicitly. Kernel cgroup enforcement can temporarily exceed its limit before reclaim/OOM handling; it is not an exact RSS ceiling. V8 heap limits and RSS monitoring do not substitute for this cap. No Docker socket is mounted.

```sh
node test.mjs pure
node test.mjs protocol
node test.mjs model
node qualify.mjs restricted --report dist/installed.json --artifacts dist/artifacts
node qualify.mjs container --report dist/container.json --artifacts dist/artifacts --exchange dist/exchange.json
# Run serially after other tests:
node qualify.mjs benchmark --report dist/benchmark.json --artifacts dist/artifacts
```

The runner reports executed case identities and fails empty selections. `REGRESSIONS.md` records local ownership. Container qualification uses only a locked installed artifact, no network, a read-only root and enforced limits. It preserves actual contract exchanges, CPU-observed in-flight cancellation, capacity release, complete worker restart/replay, explicit failures, configuration, exit and scoped cleanup. Root-owned installed Python tests independently validate exchanges through the shared consumer. The benchmark records identity, architecture, quota, cold initialization, first/warm latency, observed peak memory, actual inference count, disposal and exit. Measurements do not establish a universal SLO or prediction quality.

The benchmark's `measurement_scope` is `model_session`: latency excludes supervisor IPC and transport. Run it separately from other heavy suites. For the installed Python exchange consumer, from the repository root:

```sh
uv venv --python 3.14 /tmp/tempo-maia-contract-check
uv pip install --python /tmp/tempo-maia-contract-check/bin/python pytest==9.1.1 ./contracts/analysis-jobs
/tmp/tempo-maia-contract-check/bin/python -I -m pytest workers/maia/tests/contracts --import-mode=importlib -q
/tmp/tempo-maia-contract-check/bin/python -I workers/maia/tests/contracts/consume_observed.py --exchange workers/maia/dist/exchange.json
```

Explicit failures retain their distinct reason codes:

| Failure | Reasons |
| --- | --- |
| Unsupported context | `unsupported_model`, `unsupported_rating`, `terminal_position` |
| Input/protocol | `invalid_fen`, `invalid_legal_moves`, `duplicate_legal_move`, `invalid_request`, `invalid_record` |
| Provenance/runtime | `model_asset_missing`, `model_asset_unavailable`, `model_hash_mismatch`, `vocabulary_hash_mismatch`, `model_provenance_mismatch`, `model_loading_failed`, `invalid_model_output` |
| Resources/execution | `memory_limit_unavailable`, `memory_budget_unsupported`, `execution_timeout`, `worker_crashed`, `worker_busy`, `worker_not_ready` |
| Identity/capacity | `identity_payload_conflict`, `stale_generation`, `session_capacity_exhausted`, `cancellation_capacity_exhausted` |

An unchanged invalid, unsupported or terminal request is nonretryable. Transient execution/capacity failures explicitly mark retryability; callers should inspect statuses rather than infer completion from missing output. Malformed or unaddressable transport input terminates the stream with a diagnostic failure.

Legacy `scripts/maia-inference.mjs` stays a thin same-rating adapter to the owned model/core. Browser computation shares the pure orientation/normalization core. Frozen-helper and browser/container parity regressions preserve compatibility. Retirement belongs to legacy coverage/frozen/browser consumers when they adopt this versioned interface; automatic foreground work remains disabled.

## Handoff to Intelligence #109

Persist immutable request identity and current execution generation in the requesting domain. While a worker session is alive, retry a lost response with the exact same request/generation to retrieve its original receipt. A final retryable attempt status or lost process/session requires a higher generation with unchanged immutable contents; fence every older result at acceptance. Store accepted evidence once and validate model identity, requested move set, digest and normalization using the independent shared contract consumer. This package supplies the executable protocol and contract-valid observed fixtures; it supplies no Intelligence persistence.

For cancellation, retain the cancellation ID and inspect matching acknowledgements. `requested` is unresolved and does not prove termination; repeat the same ID or await the final acknowledgement. Unknown/future cancellations remain unresolved until their matching job arrives. Bound requester fan-out to the documented 64 unresolved cancellation IDs per generation and handle explicit capacity failure. `terminated` is a publication fence; `already_completed` preserves the completed result, and `stale_generation` requires consulting the current durable generation.
