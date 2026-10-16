# PR #293 repair qualification

Verdict: **READY FOR INDEPENDENT RE-REVIEW**. This qualifies the repaired B
component for re-review; it does not establish hosted/full-system merge qualification.
No merge, live deployment, Actions change or domain-service change occurred.

## Repair source and disposition

Reviewed/starting runtime: `603822b2c2f3618ddd2055001a6c5afdfecde3f8`.
Clean qualified executable/build/test source:
`c652abadc9a0abcd022995447014d42e9e88d411`.
Remote main/base remained `0edf566ad48f51846a43d3341809379ff0469ada`;
`git merge-tree --write-tree origin/main HEAD` succeeded. Final publication adds
only this validation/evidence; `qualification/pr293-repair/qualified-inputs.json`
records the exact input tree that must remain unchanged. The final published SHA
is recorded in the PR and external publication/cleanup receipts.

| Finding | Original proof | Repair and ordinary owner | Final result |
| --- | --- | --- | --- |
| P1 repeated startup orphaned active engine | `B_repeated_start_preserves_active_child`: spawned children 2, expected 1 | worker.mjs:55–125 retains initial-start promise; prewarm refuses to overwrite an owned live child; protocol lifecycle regressions | Pass; actual CPU-active installed engine, confirmed cancellation, replacement and one later completion also pass |
| P2 stale staged inputs | `B_test_runner_refreshes_staged_contract_inputs`: stale schema SHA `02d4bd4d…`, manifest SHA `b81564a9…` survived another runner invocation | test.mjs:14–64 uses invocation-owned verified copies; pure runner regressions cover all inputs, source mismatch, missing/partial stages, parallelism and selection | Pass |
| P2 rejected commands falsely failed execution | Four protocol regressions observed shared `JobStatus failed` for active/completed conflicts and cancellation ID/capacity refusal | worker.mjs:168–175,226–251,338–350 throws typed command rejection; cli.mjs:46–47 closes session; protocol and installed-client owners | Pass; all four real CLI/client cases exit 1 with typed loss, no false failures/acks, and explicit generation-2 restart |

Before/after commands from the repository root:

```sh
node workers/stockfish/test.mjs protocol 'B_repeated_start_preserves_active_child|B_conflicting_duplicate|B_cancellation_identity_conflict_does_not_fail_target_execution|B_cancellation_capacity_refusal_is_not_execution_failure'
node workers/stockfish/test.mjs pure B_test_runner_refreshes_staged_contract_inputs
```

Against the reviewed production bytes plus the preserved regression patch, the
first command had five intended assertion failures and the second had one intended
freshness assertion failure. These were not setup/import failures. The regular
repaired suites now execute and pass every selected case. `red.json` retains names,
actual assertions, source and patch hash. Full original red logs/patch are preserved
outside the checkout. Fake children are independently tracked and reaped even when
the baseline loses its child pointer.

Startup is idempotent after successful initialization, including busy/stopping/
recovering children. Concurrent callers share inspection and initial child startup;
closing/closed calls reject explicitly. Existing crash recovery remains owned and
bounded. Genuine execution failures still emit retained JobStatus. A refused
command targeting an accepted execution, or a refused cancellation command, emits
no execution failure. Direct callers catch CommandRejection; NDJSON callers lose
the session after owned-child shutdown. Python raises WorkerSessionLost and never
retries automatically. C fences the old session and advances generation. This
availability tradeoff preserves strict shared v1 without inventing a command ack.

## Final commands and results

Exact private-path invocations and complete raw output are preserved externally;
`qualification/pr293-repair/checks.json` contains path-redacted command/exit records.
`<EVIDENCE>` and `<PYTHON>` below mean the task-owned locations from those records.
No repeated execution is counted as additional distinct coverage.

| Command | Result |
| --- | --- |
| `node workers/stockfish/test.mjs pure` | 21 passed, 0 failed/skipped |
| `node workers/stockfish/test.mjs protocol` | 32 passed, 0 failed/skipped |
| `node tools/component-build/check-components.mjs --component stockfish` | 8 runtime sources; unchanged 3 public exports |
| `node tools/component-build/test-lane.mjs --component stockfish --list` | Four nonempty owning suites |
| `node tools/component-build/test-lane.mjs --component stockfish` with explicit installed client/runtime environment | All four suites executed: 21 pure, 32 protocol, 16 Python, 17 actual-container cases; no failures/skips |
| `node --input-type=module -e 'import {runNodeFiles} from "./tools/component-build/test-node.mjs"; runNodeFiles(["tools/component-build/tests/workers.test.mjs"]);'` | 4 worker-hook cases passed; includes negative registration/empty proof rejection |
| `<PYTHON> -I -m pytest contracts/analysis-jobs/tests --import-mode=importlib -q --junitxml=<EVIDENCE>/shared-contract-final.xml` | 92 passed |
| `<PYTHON> -I contracts/export_schemas.py --check --package analysis-jobs` | 7 exports verified; all shared contract bytes unchanged |
| `node workers/stockfish/qualify.mjs restricted --report <EVIDENCE>/restricted-final-report.json --artifacts <EVIDENCE>/artifacts-restricted-final` | 35 proofs: 3 artifact/import/corruption plus 16 installed cases each on Python 3.12 and 3.14 |
| `node qualify.mjs container` from workers/stockfish, executed by registered dispatcher | 17 actual installed cases; source absent; one CPU, 512 MiB, zero swap, no network/mounts/socket; all 13 created containers absent after cleanup |
| `<PYTHON> -I workers/stockfish/qualify_container.py --image sha256:a42a0a8e44fe0db8af81ab7424dca1be0c1f5fff8e93ae517dafb6486ad2c81f --report <EVIDENCE>/benchmark-accepted-image-report.json --exchange <EVIDENCE>/benchmark-accepted-image-exchange.json --benchmark` | Five cold sessions, 20 depth-12 searches, five CPU-observed cancellations; installed consumer passed; all exits 0 |
| Changed JS `node --check`, Python AST parsing, component import/export boundary | Pass |
| `npm run check:conflicts`; `git diff --check 603822b2 HEAD` | Pass |

The actual successful exchange independently validated 27 results (26 distinct
executions), 104 statuses and 12 cancellation acknowledgements. Four conflicting
command sessions are separately recorded under container.json/rejection_exchanges;
they intentionally contain conflicting inputs and are not presented as ordinary
immutable successful exchanges. fixtures/repaired retains the initial repair
actual exchanges/provenance separately from synthetic, observed and qualified
historical fixtures. Independent consumption now also rejects false terminal
statuses after a valid completed result.

## Exact replacement artifacts and C integration

- npm 1.0.0 SHA-256: `d9491edfb5834eb6530519f79a1800302fa86ede1c34f48a454bd9f6edb14e34`.
- Accepted image: `sha256:a42a0a8e44fe0db8af81ab7424dca1be0c1f5fff8e93ae517dafb6486ad2c81f`.
- Runtime implementation: `sha256:c9c9a625c099b58f0c05b103d68c84f1a25cc1b6c374694ae2ca74ea6c93cd64`.
- Engine profile remains `stockfish-19-smallnet:sha256:577f1e0a5fc385a809603142330931ab2353fe92e3b6ab91d927bc39a7af31e4`.
- Python client 1.0.0 wheel remains byte-identical: `7acac937976ca97aa02d7b449829243d9bedfb66e5dc23e8a32550dad8aa3a17`.
- Contract wheel remains `d313afc9df5284d93ced4619254d44d99e0f110c63a62e6e60406f617d42851c`.

Wheel/sdist and Dockerfile hashes are in summary.json and the external immutable
release MANIFEST.json. There is no registry upload or deployment claim. C can keep
its existing final safe-env client wheel when its digest matches; earlier interface
wheels should adopt the wheel above. Pin the replacement image/npm digest explicitly,
preserve old pinned images, and handle session loss before retrying. There are no
new wire fields, cancellation states, service dependencies or database permissions.

```sh
uv pip install --python "$C_PYTHON" --no-deps "$RELEASE/tempo_analysis_jobs_contracts-1.0.0-py3-none-any.whl" "$RELEASE/tempo_stockfish_client-1.0.0-py3-none-any.whl"
docker run --pull=never --rm -i --network=none --cpus=1 --memory=512m --memory-swap=512m --pids-limit=64 --read-only sha256:a42a0a8e44fe0db8af81ab7424dca1be0c1f5fff8e93ae517dafb6486ad2c81f serve
```

RELEASE is the verified digest-named directory supplied in the private handoff;
C_PYTHON is C's own interpreter with locked contract dependencies. StockfishClient
receives the Docker command as argv and its explicit safe environment. Complete
actual job/result/status/cancellation examples are in fixtures/repaired/exchange.json
and the final external exchange. README documents the digest and typed API.

## Serial measurements and limits

These measurements use the exact accepted image after other acceptance work.
Five cold worker sessions reuse a cached image; they are not cold image pulls.
All 20 searches returned depth 12, with 8,780–70,353 actual nodes.

| Measurement (ms) | min | median | nearest-rank p95 | max |
| --- | ---: | ---: | ---: | ---: |
| Cold startup | 575.50 | 660.78 | 944.14 | 944.14 |
| Engine initialization | 291.36 | 349.19 | 471.60 | 471.60 |
| End-to-end search | 38.94 | 121.14 | 183.37 | 184.20 |
| Engine-reported search | 26.00 | 91.50 | 166.00 | 167.00 |
| Cancellation to confirmed termination | 6.45 | 7.73 | 14.58 | 14.58 |
| Cancellation to replacement readiness | 234.56 | 249.20 | 285.01 | 285.01 |

Peak observed whole-worker memory was 224,714,752 bytes (214.30 MiB); hard cap
536,870,912 bytes, one CPU, zero swap. All five sessions exited 0. The host was
contended (load averages 13.20/10.38/6.86; host swap used 4,604.44 MiB). These are
fixture samples, not product latency/SLO, saturation or live Study measurements.
The benchmark wrapper also passed on a separately rebuilt image with identical
npm/runtime digests; its report remains external and is not substituted for these
same-image measurements.

Exploratory failures are retained: the intended six red assertions; one 18-pass/
one-fail pure run exposing file-wrapper counting in the new empty-selection probe;
and one manual conflict command used an absent script pathname before the supported
package script passed. Early recorder metadata used started_at for completion time;
subsequent records have separate actual start/end timestamps. An overwritten
intermediate npm filename was deterministically recovered and verified against its
original recorded SHA-256 (ARTIFACT-PRESERVATION.json). No failed required check was
removed, skipped or weakened. Final required component checks have no failures.

Separate source self-review covered lifecycle/reentrancy, receipts, transport,
freshness, resources and integration; no remaining actionable repair finding was
identified. Full repository/producer exchange, hosted checks, other architectures,
C durable scheduler/acceptance, broader #43 fairness/saturation and production
cutover remain unverified or outside B. Learning, Maia and all domain services are
unchanged. No issue is closed by this component proof.

## Preservation and cleanup

Raw logs, tests-only red patch, all source-bound exploratory/final reports, immutable
release files and saved repair images are preserved outside the disposable checkout.
The previous task's evidence and C-pinned images remain untouched. Cleanup is owned
by this repair after verifying pushed refs, artifacts, declared-input identity,
process/container mounts, ignored/untracked/stashed work and exact resource ownership.
The external CLEANUP.json records actual removed/retained resources and final remote
SHA. This repository retains path-redacted qualification reports; raw exchanges
are never rewritten to manufacture desired protocol output.

---

# Historical qualification at the reviewed PR head

The following original report is preserved as historical source-bound evidence.
Its artifact IDs, case counts and timings are not the repaired worker's results.

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
