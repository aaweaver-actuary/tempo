# Independent Maia worker qualification (#110)

Starting main: `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`.
Branch: `codex/issue-110-maia-worker`.
Tested executable: `026135939d86158d3e968d5c1b92a78dc19843e9`.
The following delivery commit adds this record only; worker artifact inputs remain unchanged.

## Artifact and capabilities

Artifact: `@tempo/maia-worker` 1.0.0, npm tarball and minimal container.
The independently repeated restricted/container/benchmark builds produced SHA-256
`a1f9c020b21dce407558d6db26256e3f9d2b6fb9df551e59233b6e326b5c1836`.
The installed npm `tempo-maia identity` executable also succeeded in a fresh consumer.

The [immutable descriptor](../../workers/maia/model-descriptor.json) records:

| Input | Verified value |
| --- | --- |
| Model ID/version | `maia3-onnx` / `maia3-v1-5636517db9d76a5f` |
| Model bytes | 45,683,686 |
| Model SHA-256 | `405bf76c15727dad8728b352c06a8f3c1b80fb2760e8d666b32485c63d75b856` |
| Move index SHA-256 | `e3351a233174e99d9ddca981681b65a444d191e8a6b474e27bd8fad8514af8d7` |
| Reverse index SHA-256 | `bb9542b706d3011aa8c0bb067dd33b50bbd7313414cfb50052b0c92aaa9833ee` |
| Qualified worker runtime | Node 26.10.0, ONNX Runtime Web WASM 1.23.0, chess.js 1.4.0 |
| Observed Linux implementation identity | `maia-worker-v1-3eee2e5fbca3c613` |

Model version binds the significant descriptor. Implementation identity additionally
binds core/model/error/descriptor source hashes and actual runtime context. The
original upstream checkpoint/export revision remains unknown; the bundled model
was not replaced or downloaded.

The model receives float32 `tokens [1,64,12]`, `elo_self [1]`, and `elo_oppo [1]`.
Outputs are float32 `logits_move [1,4352]` and `logits_value [1,3]`; value logits
are validated but not published as evidence. Only occupancy and ratings enter
the network. Castling/en-passant affect legal-move selection; history, counters,
speed, and time control are not dedicated inputs.

The nine supported ratings are 1100–1900 in increments of 100. **Wire v1's one
rating is passed to both ordered model inputs.** No distinct-player-rating v1
fields or silent rounding were introduced.

Probabilities use stable binary64 softmax over exactly the requested legal set.
Subsets are conditional; whole-position distributions require all legal moves.
Omitted move mass is not zero. Numeric underflow zero is retained. Local FEN
coherence and necessary material/promotion bounds are checked; arbitrary
retrograde reachability and empirical calibration are not claimed.

## Validation commands and outcomes

Evidence directory outside the disposable checkout:
`/Users/andy/tempo/test-results/2026-10-16-issue110-maia/`.
Reports contain case identities, artifact hashes, actual exchanges, Docker
configuration, runtime identities, exits, and cleanup results.

| Exact check | Outcome |
| --- | --- |
| `TEMPO_COMPONENT_PYTHON=/private/tmp/tempo-maia-contracts-env/bin/python node tools/component-build/test-lane.mjs --component maia` | 54 pure/artifact/resource + 38 protocol + 21 installed Python consumer cases passed; no skips |
| `/private/tmp/tempo-maia-contracts-env/bin/python -I -m pytest contracts/analysis-jobs/tests workers/maia/tests/contracts/test_exchange.py --import-mode=importlib -q` | 113 passed |
| `TEMPO_COMPONENT_PYTHON=/private/tmp/tempo-maia-contracts-env/bin/python node tools/component-build/test-node.mjs tools/component-build/tests` | 81 passed; shared-tool inputs unchanged by the subsequent CLI/qualifier guard fix |
| `node tools/component-build/check-components.mjs --component maia` | Static exports/dependencies/import closure and syntax checks passed |
| `node tools/component-build/test-lane.mjs --component maia --list` | Three owning suites listed without execution |
| `node tools/component-build/restricted-build.mjs --component maia <evidence>/restricted-build.json` | Complete clean-source report; 3 installed-artifact cases passed, hashes verified, temporary workspace removed |
| `node qualify.mjs container --report <evidence>/container.json --artifacts <evidence>/artifacts --exchange <evidence>/exchange.json` from `workers/maia` | 23 actual acceptance cases passed; both process shutdowns exited 0, no OOM |
| `/private/tmp/tempo-maia-contracts-env/bin/python -I workers/maia/tests/contracts/consume_observed.py --exchange <evidence>/exchange.json` | 85 independent checks passed over actual records, including stale-result rejection |
| `node /Users/andy/tempo/node_modules/vitest/vitest.mjs run --config /private/tmp/tempo-maia-legacy.vitest.config.mjs --reporter=json --outputFile=<evidence>/legacy-parity.json` | Both existing browser/Node and frozen-helper regressions passed |
| `python3 <evidence>/legacy_container_proof.py` | Minimal legacy adapter image built; offline 1 CPU/1 GiB smoke exited 0 with five moves; owned container/tag removed |
| `node /Users/andy/tempo/node_modules/typescript/bin/tsc --project /private/tmp/tempo-maia-adapters.tsconfig.json` | Strict browser-adapter dependency-graph check passed |
| Focused ESLint using the existing cached checker and `<evidence>/tempo-maia-eslint.config.mjs` | All changed JavaScript/TypeScript and new worker modules passed; no code warnings. Checker emitted a React-version discovery notice in this isolated checkout |
| `node scripts/check-merge-conflicts.mjs`; `git diff --check` | Passed |

`<evidence>` expands to the directory above. The saved temporary test
configurations explicitly resolve existing cached frontend checking dependencies;
no frontend dependency tree was installed. The worker's artifact proof has no
access to that cache or the repository source directories.

Actual model cases cover both opening orientations, white/black castling,
white/black legal en-passant, pinned/uncapturable en-passant, white/black promotion,
a complete legal universe, a constrained subset, and ratings 1100/1900.
Exact-session replay preserves the original full result/provenance. The real
cancellation witness observed child PID 16 advance from 189 to 193 CPU ticks
before termination. Capacity returned after model reinitialization. Complete
worker restart re-executed the immutable request at generation 2, rejected
generation 1, and accepted another fresh job.

Regular suites separately cover malformed/illegal/duplicate input, vocabulary
and model tampering, tensor/nonfinite/mass failures, identity conflicts,
generation replacement, cancellation/completion races, multiple cancellation
IDs, bounded receipts, deadline/crash/recovery, output backpressure and EOF/EPIPE.
The independently maintained requester ledger proves one accepted logical effect;
it does not claim Intelligence PostgreSQL integration.

Final qualification found a real launch defect in the initial executable
`dd81802b2ca4477f85b53f829cec6d70fc7557cd`: symlinked paths could make the CLI
exit 0 without running. The failed reports are retained as `*-dd81802-failed.*`.
The correction adds named regular CLI/qualifier symlink regressions. The final
artifact and container proof use the corrected executable above.

## Serial offline benchmark

Command from `workers/maia`:
`node qualify.mjs benchmark --report <evidence>/benchmark.json --artifacts <evidence>/artifacts`.
It ran after this task's other heavyweight checks. It measures the model session,
excluding NDJSON/supervisor overhead; no dedicated-host or universal SLO is claimed.

Linux ARM64; Node 26.10.0; ORT 1.23.0; concurrency one; kernel CPU quota
`100000 100000`; 1 GiB whole-container memory cap; swap limit zero; no network;
read-only root. Exactly 26 model calls: one first inference and 25 warm samples
across five fixed positions and supported ratings.

| Measurement | Observed |
| --- | --- |
| Cold model initialization | 1,520.11 ms |
| First inference | 375.47 ms |
| Warm p50 / p95 / maximum | 85.55 / 193.15 / 193.78 ms |
| Peak process RSS | 557,998,080 bytes (532.15 MiB) |
| Kernel cgroup memory peak | 511,537,152 bytes (487.84 MiB) |
| CPU user / system | 3,937.40 / 415.47 ms |
| Benchmark process wall time | 4,370.90 ms |
| Exit / OOM | 0 / false |

Process RSS and cgroup accounting are distinct observed metrics; they are not
interchangeable. Session disposal, container/tag removal, and temporary-directory
removal succeeded. The qualified 1 GiB floor is conservative support policy, not
a measured lowest possible model footprint. Kernel caps may temporarily overshoot.

## Handoff and limits

Intelligence (#109) should own the worker process lifecycle and durable request/
acceptance ledger. Wait for readiness; bind each accepted result to workspace,
job, request revision, current generation, opaque input digest, requested move
set, and the expected actual model/runtime implementation identity. Include
rating and conditional move-set identity in accepted evidence/cache semantics.

Retry the exact request within a living session to retrieve its original receipt.
Failed execution receipts also replay, including retryable failures; recomputation
requires a higher generation with the same immutable semantic payload. After
process loss, record unresolved execution and advance generation before retrying.
Changing constraints or other semantic contents requires a new request revision.
Fence prior-generation and duplicate effects in Intelligence's durable owner.

Cancellation `requested` does not establish termination. Unknown/future targets
remain requested until their matching job arrives. Only `terminated` certifies
the execution is stopped/prevented in this session. Up to 64 distinct cancellation
IDs per generation receive final acknowledgements; overflow is explicit. The
worker has no durable outbox or accepted-result database.

Legacy Node/frozen adapters and the browser share one computation owner; browser
loading/session/SAN behavior remains with its adapter. Frozen ordering tables
and live Study were not recomputed, activated, or restarted. Legacy adapters
retain their previous scalar-rating behavior; unsupported worker-v1 ratings are
still rejected. Retire those adapters when their respective consumers adopt the
versioned interface.

Production resource proof covers Linux ARM64. Other deployment architectures,
arbitrary runtimes, empirical prediction quality, Intelligence persistence,
full-system behavior, and hosted required checks are unverified here. No
Learning/Content/Game Evidence database, broker, provider, or Stockfish was used.
Actions and branch protection were not changed. No merge or deployment occurred.
