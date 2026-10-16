# Stockfish B qualification and handoff

## Revisions and scope

- Base and refreshed `origin/main`: `0edf566ad48f51846a43d3341809379ff0469ada`.
- Branch: `codex/analysis-b-stockfish`; PR: https://github.com/aaweaver-actuary/tempo/pull/293.
- Interface published first: `76b930dccd0df73cc4451d09ddaff3806f5c3f49` (issues #43/#266).
- Final container acceptance source: `df54723a05facae761539480dad58f3b74ef50ad`, clean at build.
- Final serial measurement source: `c1ccba8dc35881e668809788ab9f39be7091f656`, clean at execution.
- Final restricted/client case evidence source: `b7906f9c976ef9dbbcc4de21fa1b03524f4dc05e`, clean at build.
- Later commits retain evidence/documentation. Runtime hash and npm artifact below are unchanged across these tested revisions. The PR body records the final published SHA.

B owns computation, bounded process-local receipts, cancellation and actual engine
termination. Shared analysis-jobs v1 and all Maia sources are unchanged. Only the
Stockfish component registration and scoped testing documentation change outside
`workers/stockfish`. C retains durable jobs, leases/generations, retries, age,
acceptance, invalidation, ranking and UI; Learning retains training effects.

These are component proofs, not full #43 fairness/saturation acceptance, live Study
latency, production adoption, deployment, or hosted merge qualification.

## Exact artifacts and execution identity

Qualified image: `sha256:21172218ef8fb1c5d1edf6dabae11ab7de13f829b0cdb2c001974a4fdbe07f81`.
Runtime implementation: `sha256:40aefd1ef998ef83928296e7ebe43f8b645af3fbe11aded9178d952503f056bc`.
Profile: `stockfish-19-smallnet:sha256:577f1e0a5fc385a809603142330931ab2353fe92e3b6ab91d927bc39a7af31e4`.
Actual runtime is Node 26.10.0, Linux ARM64, Stockfish 19 smallnet, Threads=1,
Hash=32 MiB, full strength, no pondering/Chess960/WDL/tablebases. Exact JS, WASM,
network hashes, source revision, patches, licenses and toolchain references are
retained in `engine-descriptor.json`, `build-inputs.json` and `licenses/`.

| Artifact | SHA-256 |
| --- | --- |
| `tempo-stockfish-worker-1.0.0.tgz` | `fd12c15fc5f539e327a124086be21ae425e8ae316b04761f4c4a92fc333962d3` |
| `tempo_analysis_jobs_contracts-1.0.0-py3-none-any.whl` | `d313afc9df5284d93ced4619254d44d99e0f110c63a62e6e60406f617d42851c` |
| `tempo_analysis_jobs_contracts-1.0.0.tar.gz` | `18b3dd64f20a2bba67b6f2e88cbe0c595749ecc2eae2eb30784a88cf2e98266d` |
| `tempo_stockfish_client-1.0.0-py3-none-any.whl` | `7acac937976ca97aa02d7b449829243d9bedfb66e5dc23e8a32550dad8aa3a17` |
| `tempo_stockfish_client-1.0.0.tar.gz` | `ed29a1d633f89bb7d7295d644c985456e32997e29c7a0cea1b1359dbfe01945a` |

The npm artifact contains only allowlisted runtime files, declarations, the three
unchanged assets, five unchanged v1 schemas, lock/shrinkwrap and notices. Python
client wheel/sdist are separate and depend only on the installed shared contract.
No repository, application, database, broker, model substitution or provider setup
is needed at execution. Builds may install locked dependencies; startup downloads
nothing. The base image is pinned by digest in Dockerfile.

Immutable local release directory (outside the disposable checkout):
`/private/tmp/tempo-analysis-b-stockfish/evidence/releases/fd12c15fc5f539e327a124086be21ae425e8ae316b04761f4c4a92fc333962d3`.
Its manifest hashes each file. Safe-env client release is also retained at
`evidence/client-releases/7acac937976ca97aa02d7b449829243d9bedfb66e5dc23e8a32550dad8aa3a17/`.
Both initial and qualified images are saved in `evidence/images/initial-and-qualified.tar`
with a verified SHA-256 manifest. The original npm artifact
`43a25e51b1e1d8136f5d335db47e7614fcf1c32a5fe7bb0a8005e871f6928bc9`
was recovered from the saved initial image layer and verified. Initial image
`sha256:713e7d04340e510418a77535b26d3f56421f3b59e9623b579c12f0c390cd242b`
and `fixtures/observed` remain historical actual evidence; C's initial pilot pins it.

After unpacking the exact npm tarball into an empty directory, install with
`npm ci --ignore-scripts --omit=dev --workspaces=false`, then run
`node cli.mjs identity`. Build the saved Dockerfile from a context containing only
it and the tarball named `artifact.tgz`. Or restore the preserved images with
`docker image load -i initial-and-qualified.tar` and run the image by exact ID:

```sh
docker run --pull=never --rm -i --network=none --cpus=1 --memory=512m --memory-swap=512m --pids-limit=64 --read-only sha256:21172218ef8fb1c5d1edf6dabae11ab7de13f829b0cdb2c001974a4fdbe07f81 serve
```

Install the exact client and contract wheels into C's environment with
`uv pip install --python /path/to/C/python --no-deps /path/to/contract.whl /path/to/client.whl`
(after the shared contract's locked dependencies). The complete executable caller
is `backlog_driver.py`; the typed asynchronous API, job example, digest and retry
semantics are in README. Pass `StockfishClient(argv, env=explicit_safe_environment)`.
Neither the default whitelist nor this mapping inherits ambient provider/DB/private-C
configuration. Importing either package is passive.

## Executed checks

Commands below ran from `/private/tmp/tempo-analysis-b-stockfish/tempo` unless
noted. The Python interpreter was the task-owned Python 3.14.8 environment; Node
was 26.10.0. Only worker, contract/test and component-parser dependencies were
installed. No root web/backend installation was performed. Individual identities
are retained in `qualification/*.json` and the shared JUnit XML.

| Exact command (report redirection omitted) | Outcome |
| --- | --- |
| `node workers/stockfish/test.mjs pure` | 15 passed, 0 failed/skipped |
| `node workers/stockfish/test.mjs protocol` | 19 passed, 0 failed/skipped |
| `node tools/component-build/check-components.mjs --component stockfish` | Pass: 8 runtime sources and 3 declared public exports |
| `node tools/component-build/test-lane.mjs --component stockfish --list` | Four owning suites listed without execution |
| `node --input-type=module -e 'import {runNodeFiles} from "./tools/component-build/test-node.mjs"; runNodeFiles(["tools/component-build/tests/workers.test.mjs"]);'` | 4 passed, 0 failed/skipped |
| `/private/tmp/tempo-analysis-b-stockfish/python-env/bin/python -I -m pytest contracts/analysis-jobs/tests --import-mode=importlib -q --junitxml=/private/tmp/tempo-analysis-b-stockfish/evidence/shared-contract-final.xml` | 92 passed |
| `UV_CACHE_DIR=/private/tmp/tempo-analysis-b-stockfish/uv-cache TEMPO_STOCKFISH_PYTHON=/private/tmp/tempo-analysis-b-stockfish/python-env/bin/python node workers/stockfish/qualify.mjs restricted --report /private/tmp/tempo-analysis-b-stockfish/evidence/restricted-final.json --artifacts /private/tmp/tempo-analysis-b-stockfish/evidence/artifacts` | 23 executed proofs: 3 artifact/import/corruption cases plus 10 installed Python cases on each of 3.12 and 3.14; none skipped |
| `TEMPO_STOCKFISH_PYTHON=/private/tmp/tempo-analysis-b-stockfish/python-env/bin/python node workers/stockfish/qualify.mjs container --report /private/tmp/tempo-analysis-b-stockfish/evidence/container-final.json --exchange /private/tmp/tempo-analysis-b-stockfish/evidence/exchange-final.json --artifacts /private/tmp/tempo-analysis-b-stockfish/evidence/artifacts` | 12 actual acceptance cases passed; cleanup confirmed |
| `/private/tmp/tempo-analysis-b-stockfish/python-env/bin/python -I workers/stockfish/qualify_container.py --image sha256:21172218ef8fb1c5d1edf6dabae11ab7de13f829b0cdb2c001974a4fdbe07f81 --report /private/tmp/tempo-analysis-b-stockfish/evidence/benchmark-final.json --exchange /private/tmp/tempo-analysis-b-stockfish/evidence/benchmark-exchange-final.json --benchmark` | Five cold sessions, 20 actual searches, five in-flight cancellations; independent installed consumer passed; all five sessions exited 0 |
| `git fetch origin main`; `git merge-tree --write-tree origin/main HEAD` | Refreshed base unchanged; no merge conflict |
| `git diff --check` and comparison of all non-Stockfish component registrations to the base | Pass; unrelated registrations unchanged |

The benchmark wrapper captures clean source state, artifact identity and host
pressure immediately before the exact command above, then annotates the report.
Its run is serial and follows heavy component/resource tests. Node fake engines
own malformed output, reentrancy and deterministic races; their results are not
represented as actual Stockfish execution. All 25 exact B8 names have ordinary
owners in REGRESSIONS.md and component discovery. Additional proofs cover strict
digests/FEN whitespace, bounded receipts, mutable subscriber outputs, late startup
on shutdown, passive imports, and C-reported client environment isolation.

Actual acceptance independently consumed 25 results (24 distinct executions),
87 statuses and four cancellation acknowledgements using installed shared v1 and
python-chess. The consumer imports no worker engine/client, recomputes the digest,
applies all history and every PV legally, checks restrictions/ranks/depths/typed
scores/provenance, exact replay and cancellation identity/publication fencing.
`fixtures/qualified` preserves the actual exchange and provenance separately from
clearly labeled synthetic fixtures and the earlier observed exchange.

The node-limited depth-20/10,000-node request returned an actual completed depth
12; it was not relabeled depth 20. C decides minimum-depth product acceptance.

## Measured scope

Five independent process/container starts reuse the cached image; they are cold
worker sessions, not cold disk/image-pull measurements. Twenty searches consist of
five repetitions each of white start position, black after supplied e2e4 history,
restricted e2e4 root, and MultiPV=3. All returned actual depth 12. Engine-reported
nodes were 8,780–70,353; each case's exact nodes and engine time are retained.
Search time below is the engine's UCI time, while end-to-end timing includes client,
transport, validation and publication. No depth/strength/profile was reduced to
meet a latency threshold.

| Measurement | min / median / nearest-rank p95 / max (ms) |
| --- | --- |
| Cold startup, end to end | 616.12 / 943.07 / 1531.15 / 1531.15 |
| Engine initialization | 302.93 / 453.56 / 614.10 / 614.10 |
| Search, end to end | 71.96 / 169.22 / 226.05 / 272.40 |
| Engine reported search time | 27.00 / 110.50 / 186.00 / 205.00 |
| Cancellation to confirmed termination | 8.56 / 12.51 / 13.67 / 13.67 |
| Cancellation to replacement readiness | 249.40 / 327.77 / 535.85 / 535.85 |

Each cancellation observed increasing real engine CPU ticks, received `terminated`
after confirmed child exit, checked the old PID was gone, and waited for the latest
replacement readiness event. All five sessions exited 0 and every owned container
was absent after cleanup. Whole-worker cgroup peaks without deliberate pressure
were 175.69–203.42 MiB. The enforced cap
was 536,870,912 bytes, one CPU and zero swap. Peak/RSS observations are not the
memory enforcement mechanism. Acceptance separately allocated an oversized helper;
kernel `memory.events` increased `oom_kill` and the helper exited 137. That proves
the cgroup cap without attributing unexplained worker/transport loss to OOM.

Slow-output evidence used one real 20-root search plus 64 exact replays, keeping
finite input below the ingress cap. The actual CLI reported `output_stalled`,
exited 1 with its engine gone, drained 151,476 bytes, and did not hit the harness
watchdog. Input/record/output-queue bounds and active watchdogs also have fake-stream
regressions. No partial result is accepted on wall timeout or effective cancellation.

The finite driver completed 12 waiting fixture jobs, observed one busy refusal,
cancelled one active probe, retried the refused job at generation 2 and recovered
capacity. Foreground-like end-to-end samples were 104.94, 25.18 and 40.63 ms;
queue waits were 422.35–879.21 ms, with class/original-age metadata outside jobs.
This is useful finite caller evidence, not C's durable scheduler/fairness proof.

Host at final measurement start: `14:20  up 1 day,  1:42, 2 users, load averages: 2.83 11.51 45.76`;
`vm.swapusage: total = 6144.00M  used = 4734.69M  free = 1409.31M  (encrypted)`. Other tasks/applications remained active. Earlier
checks saw substantially heavier contention. These small samples establish this
artifact/profile/envelope on this host, not a universal SLO or saturation budget.
Docker host details are retained in `qualification/docker-host.json`.

## Failures, omissions and limits

Earlier nonpassing runs are recorded, never counted as passing:

- A busy-host candidate startup reported `engine_loading_failed`; its root cause
  was not established. No historical startup/transaction-budget cause is claimed
  fixed from later passes.
- Qualification cleanup first hid an earlier error during concurrent `--rm`, then
  rejected Docker's lowercase not-found diagnostic. Cleanup now distinguishes
  genuine absence from daemon failure and validates ownership before removal.
- A readiness helper could select an older ready event; a subsequent pressure
  helper overlapped initialization. It now requires the latest replacement event.
- The initial slow-output producer could first exceed the ingress buffer. The
  worker failed explicitly and terminated; finite result replays now isolate the
  separate output-stall requirement without increasing or weakening limits.
- A repeated measurement exposed independent stdout/stderr delivery order. The
  harness now awaits the matching bounded diagnostic before measuring engine CPU.
- `node tools/component-build/test-node.mjs tools/component-build/tests` ran 80
  passing cases and one failure in the unrelated TypeScript/chess-line dependency
  test (`Unavailable dependency: chess.js`). That package was not installed for
  this component-local task. The four selected worker-hook cases pass. Two initial
  runner invocations selected no cases (file used as directory, then mismatched
  name filter); neither was counted as a pass.

Available failure reports/logs/exchanges remain in external `evidence/failed-runs`.
No test was skipped, removed or weakened to manufacture a pass. Final required
component checks have no remaining failures. Full-repository/frontend/backend,
shared TypeScript producer exchange, native C/database scheduling, saturation,
other architectures, and live Study/deployment/Actions proofs were not run by B.
They are not implied by this PR. C's separate pilot evidence remains C-owned.

After supervisor/session loss, C must advance generation and fence older output;
B never invents a status after supervisor death. There is no durable exactly-once
computation promise. Search node ceilings can overshoot between UCI polling;
actual nodes/depth are reported. Unsupported/unrepresentable v1 outcomes fail.

## Preservation and cleanup

Artifacts, dependency locks, identity, saved images, all machine reports and failure
history are retained outside the checkout at
`/private/tmp/tempo-analysis-b-stockfish/evidence`. The final branch and PR preserve
source and reviewable fixtures/proofs. No branch, live Study checkout/data,
credentials, Actions, deployment, unrelated worktree/container or shared base image
is eligible for this task's cleanup. Initial and final images remain available to C.

The external `CLEANUP.json` records actual ownership/ref/process/mount checks and
removed/retained resources after publication. The disposable checkout and temporary
installs are removed only after these records and immutable artifacts are verified.
