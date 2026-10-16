# F1: Game Evidence to authoritative Learning remediation

## PR #287 recovery qualification — October 16, 2026

The three review findings were reproduced and repaired. Tested executable and
packaged inputs are commit `52d7b5c5d2cf34233723184315119ae6897af942`, containing
producer recovery commit `33185fa9` and Learning/remediation commit `52d7b5c5`.
The starting reviewed head remains `4dd771e60cd26da2e6134ca265e4fb440810ade2`;
remote main and merge base are `18543f08a77618f76f97be61deb2d444c95e7934`.
Task branch: `codex/f1-recovery-repairs-20261016`; publication targets the existing
`codex/f1-remediation-integration` branch without force-push or rewritten history.

Raw command ledgers, collected/executed inventories, JUnit, logs, artifact hashes,
source bundle and reproduction patch are preserved outside both task checkouts at
`/Users/andy/tempo/test-results/f1-recovery-20261016-efc64d65`.
The JSON record's `repair_qualification` is the current machine record. Original
F1 evidence and counts below remain historical, including their original archive.

### Repair disposition and concurrency

- **R1:** `durable_tasks.enqueue_task_in_transaction` selects remediation-specific
  marker coalescing. Same-day unfinished work preserves cursor, lease, generation,
  attempts and ready ordering; completed/new-day sweeps restart, delayed old days
  do not. `maintenance.finish_slice` reads current durable rescan state under a
  late fenced task lock, advances one-card slices and schedules follow-up work
  atomically. Release preserves concurrent requests. Review adapter requests flush
  after prepared issuance inside the original transaction, daily before remediation
  and projection last. Actual PostgreSQL locks are observed at continuation binding.
- **R2:** Runtime shortcuts move into `PostgresRepository.receive`, with optional
  trusted `historical_evidence_revision` and `v1_transport_stale` keyword arguments.
  Existing-ID digest checks precede stale policy under module serialization.
  Stale receipts, including content-stale rejection, bind payload identity without
  accepting evidence or requesting work; exact stale replay survives reconnect and
  cannot be overwritten by `record_status`. V1 transport-first behavior, v2 historical
  semantic replay and read-only historical receipts retain their deliberate differences.
  Historical same-frontier requests retain HTTP 409 for either wire. No conversion.
- **R3:** Producer `cli.main` preflights endpoint credentials before processing or
  claiming. `delivery.deliver_one` validates direct calls before claiming and maps
  only Learning HTTP 401 to durable retryable failure, with the same immutable ID,
  payload, null acknowledgement and minimum 60-second backoff. `retry-auth-delivery`
  requeues only an attempted, acknowledgement-free legacy 401 block under game-then-
  outbox locks, rejects live leases/unrelated states, and repeats safely as a no-op.
  Credentials reload on process restart. No HTTP runs under these locks.

Named regression ownership is in the three component REGRESSIONS.md inventories.
Both request/finalizer commit orders, concurrent transport reuse across independent
source identities, reconnect/client recreation, arrivals behind cursor, day/lease
fencing and receipt-write rollback pass. Actual installed producer -> authenticated
Learning HTTP -> separate databases confirms wrong-token rejection, corrected-token
restart and exactly one acceptance of the original delivery. Native producer cases
cover narrow old 401 recovery, predecessor fences and validated accepted history.
The frontier-first query-plan bounds and existing issuance/settlement invariants
remain unchanged.

Initial regressions were added before production repairs. The final identical
runtime tests also ran against reviewed production source in a separate task copy:
**115 passed / 20 failed**, versus **135 passed / 0 failed** after repair. The new API's
unsupported-keyword failure and an additional content-receipt absent-row diagnostic
are excluded from primary reproduction claims. Explicit primary assertions reproduce
cursor replacement/starvation, absent stale receipts/conflicting reuse, both legacy
stale branches, and a producer delivery remaining blocked after credential correction.
Pure credential preflight failed before repair; native library stale persistence
failed while its other 49 cases passed. No unavailable setup counted as reproduction.

### Final results and exact commands

All selected final cases pass without skips or deselection. Commands below run
from the isolated repair checkout; interpreter paths identify separate environments.
`E` denotes the external evidence root above. Every command's expanded argv, cwd,
exit status, source identity and log are also in the machine record.

| Lane | Actual command | Result |
| --- | --- | --- |
| Learning native | `services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py E/native-final` | 135 passed; includes 3 real Chromium cases, response loss/reload, producer-stopped Learning, restricted roles/image and new repairs. |
| Remediation native | `services/learning/remediation/.venv/bin/python services/learning/remediation/integration/run_postgres.py E/remediation-final` | 50 passed; original query-plan/frontier bounds retained. |
| Remediation pure/tooling | `services/learning/remediation/.venv/bin/python services/learning/remediation/run_suite.py pure` and separate `tooling` invocation | 58 / 10 passed. |
| Producer native | `services/game-evidence/.venv/bin/python services/game-evidence/integration/run_postgres.py E/producer-coherent` | 64 passed; input-equivalent evidence verified against 52d7b5c for every producer and receiver input, unchanged command/environment/toolchain/image. |
| Producer unit/CLI | Artifact runner's installed `python -I -m pytest tests --import-mode=importlib -q` | 46 passed on each of four consumers; standalone development unit run also46 passed. |
| Legacy compatibility | `TEMPO_COMPONENT_PYTHON=$PWD/backend/.venv/bin/python TEMPO_LEARNING_EVIDENCE_DIR=E/legacy-final node tools/component-build/test-lane.mjs --component learning-review-application` | 43 application + 101 tooling + 24 PostgreSQL passed. Changed packages reinstalled into the separate declared legacy environment. |
| Dependencies/contracts | `TEMPO_COMPONENT_PYTHON=$PWD/backend/.venv/bin/python node tools/component-build/test-lane.mjs --component scheduling --component chess-core --component content-publication-contracts --component game-evidence-contracts` | 55 / 105 / 99 / 99 passed. |
| Learning wheel/sdist | `services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py E/learning-artifacts` |Python 3.12.12 and 3.14.8; 3 installed checks per consumer, including new receipt API replay/conflict. These are installation checks, not four complete native inventories. |
| Producer wheel/sdist | `services/game-evidence/.venv/bin/python services/game-evidence/integration/qualify_artifacts.py E/producer-artifacts` |Python 3.12.12 and 3.14.8; 46 owning unit/CLI cases per consumer; copied build sources removed. Actual recovery command behavior is additionally native installed-CLI proof. |
| Boundaries | `TEMPO_COMPONENT_PYTHON=$PWD/backend/.venv/bin/python node tools/component-build/check-components.mjs --component learning --component game-evidence` |Passed affected component closure, exports/resources/dependencies. |
| Component tooling | `TEMPO_COMPONENT_PYTHON=$PWD/backend/.venv/bin/python node tools/component-build/test-node.mjs tools/component-build/tests` | 77 passed;0 failed/skipped/TODO. |
| Static | `/Users/andy/.local/bin/ruff check --select F821,F822,F823 services/learning/runtime services/learning/remediation services/game-evidence` |Passed focused undefined-name checks; not full lint/type qualification. |
| Repository text | `npm run check:conflicts`; `git diff --check` |Passed; modified local documentation links checked with no missing files. |

Toolchain: Node 26.10.0, uv 0.12.22, Ruff 0.15.17, Playwright 1.63.0,
PostgreSQL 18.6-trixie image
`sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
One existing Starlette/AnyIO deprecation warning remains; no selected failure is hidden.

### Review, qualification limits and preservation

A separate read-only reviewer found **no actionable findings** in
`4dd771e…52d7b5c`, after tracing the surrounding lock/transaction paths and inspecting
final native/artifact evidence. This is independent code/evidence review, not a
second independent test execution. Earlier setup failures, fixture corrections and
failed runs remain in the external archive and are distinguished in the JSON record.

Local technical readiness is established for this repair's selected boundary.
GitHub Actions is disabled as observed through its settings API; hosted qualification
is **not established**. The branch-protection endpoint reported 404/branch not protected;
no settings, required checks or workflows were changed. Mechanical mergeability is
read back separately after publishing. No merge or deployment occurs.

Final native receipts verify owned container/volume/image/temp cleanup. Source
and final-test patches are preserved outside the checkout in a verified bundle and
reports. After publication, preservation and process/mount inspection, eligible task
checkouts are removed; external `cleanup.json` records actual removals and durable
published refs. Live study data, original F1 evidence, unrelated worktrees and shared
images/caches remain untouched. Any root validation-only commit after 52d7b5c must
be verified to change no executable or packaged input.

F2 full Study routing and teaching, historical conversion/live cutover, service-worker
or full-product offline qualification remain deferred. #194 and #224 remain open.

---

## Revision and ownership

The producer prerequisite was PR #286, merging the existing #283 merge commit
`3af50215198c8d6889ca9aeda0dd75e402ebcaef` into refreshed main without flattening
its implementation/history. Its approved merge is
`18543f08a77618f76f97be61deb2d444c95e7934`. F1 branches from that main revision
on `codex/f1-remediation-integration` in a new isolated checkout. The live checkout
and both pre-existing worktrees, including the dirty Study test checkout, were
preserved. Prerequisite commands/results remain in
`docs/validation/game-evidence-main-adoption.md` and the external evidence archive.

F1 source and executed evidence are recorded below; local execution does not
establish hosted merge qualification. No deployment, live migration, historical
study conversion or architecture-issue closure is included.

## Before and after runtime map

| Boundary | Before prerequisite/F1 | F1 owner and behavior |
| --- | --- | --- |
| Game Evidence | Producer absent from main | Installed producer owns PGN import, comparison against the existing immutable file snapshot, source corrections and durable v2 outbox. Snapshot fixtures use the same publication and an actual Learning activation receipt. |
| Transport | No configured producer evidence bearer | Private credential file supplies Learning's existing evidence token. HTTP runs after the producer transaction, preserving immutable body, delivery identity, lease and acknowledgement validation. |
| Intake | Independent runtime's default v1 local obligation writer | Independent Learning authenticates workspace/evidence principal; validates v1/v2 against its own accepted active definitions; remediation is the sole new evidence/revision/receipt authority. No provider or source database access. |
| Durable work | Narrow daily queue registration | `daily_queue` and `remediation` share the essential executor. Claims commit before application locks. One-card scans persist bounded cursors, yield, and commit only under their unexpired lease/generation. Ordinary work retains progress. |
| Selection | Local miss predicate, incomplete promotion | Library-owned current-miss predicate, eligibility and ordering feed Learning's sole prelocked admission writer. Cap/progression exception remains; revision/validity/lifecycle fences still apply. Protected slots, ordinary relative order and frozen inputs are retained. |
| Issuance | Retained queue-origin issuance | First authoritative head issuance, including prepared continuations, binds a server queue-entry/revision identity to the current frontier and original time. Reload, prefetch and review submission cannot extend it. |
| Settlement | Broad timestamp satisfaction | Exact valid unaided correct review plus issued origin/revision/time/failure state settles only compatible bound work. Canonical and reconciled review effects share the required transaction/savepoint. |
| Legacy assembly | Backend adapters own legacy DB | Package and restricted-role boundaries retain isolation. Independent Learning never imports the legacy backend. |
| Study application | Legacy routing | F2 owns full routing/assembly adoption. This change exercises the real existing browser adapters, not the complete Home/Study screen. |

## Transaction and replay evidence

Learning owns the connection and commit. The module adapter uses tuple cursors
per query, retaining the shared connection's row factory. Card locks precede queue
locks and the module lock; the admission port applies only after those locks.
Accepted evidence, source revisions, acknowledgement and remediation-work request
commit together. `completed` means durable acceptance; admission follows.

Intake retains the background pool, 25 ms lock budget and 200 ms statement budget,
with a separate 1,000 ms atomic maximum-batch transaction bound. Ordinary maintenance
retains 250 ms transactions; foreground budgets are unchanged. Real timeout faults
must return 503/Retry-After and roll back all acceptance writes. Lease claims no
longer hold a task row while acquiring card/queue/module locks; final fencing rolls
back superseded or expired application effects and cursor advancement.

The regular native suites cover exact transport and semantic replay; conflicting
identity reuse; monotonic/reordered/corrected evidence and explicit withdrawals;
concurrent deliveries; both evidence/review commit orders; late and unknown game
times; unchanged frontier after reload; required-effect rollback, including handled
reconciliation; and ordinary/future-due/zero-allowance/protected/frozen selection.
The remediation suite retains its frontier-first query-plan regression and bound.
Independent Learning has no review invalidation command: invalidated reviews are
ineligible to settle. Any future invalidation writer must call the library's
settlement invalidation inside that same transaction before it is supported.

Historical v1 transport receipts remain exact read-only replay evidence. Their
old obligation rows are retained without invented provenance; new deliveries at
an unconverted historical frontier conflict. Explicit historical conversion is
still #224 cutover work. No second writer or broad settlement runs alongside the
new module.

## Execution record

Evidence root outside the disposable checkout:
`/Users/andy/tempo/test-results/f1-integration-20261015`.
All commands run from the isolated repository root unless stated otherwise.
`UV_CACHE_DIR=/private/tmp/tempo-f1-integration-uv-cache` is used for task package
operations. Component runners record collection/execution/phase/JUnit inventories;
native runners refuse external databases and remove only their owned resources.
Installed consumers build only declared component inputs and use isolated Python
imports after copied producer source removal.

Final tested source: `9d1331872a538fc1780903f68fea0d6da6144b06` (implementation
`3b369830`, then explicit issuance regressions). Later root-document-only evidence
updates change no tested executable, package, fixture, lockfile or runner input.
The adjacent [machine record](f1-game-evidence-learning.json) binds the exact
commands, inventories, artifact/report hashes and toolchain to that source.

| Owning proof | Executed result |
| --- | --- |
| Learning native, final issuance inventory | **112 passed**, 52.49 s; complete collection/setup/call/teardown/JUnit agreement; one existing Starlette warning. Includes real producer/HTTP/Chromium, independent restart and restricted image. |
| Remediation registered lane | **58 pure + 10 runner + 49 PostgreSQL passed**, no skips; original query-plan bound unchanged. |
| Game Evidence registered lane | **37 unit + 51 PostgreSQL passed**, no skips. |
| Legacy review application registered lane | **43 application + 101 tooling + 24 PostgreSQL passed**, no skips; imports remain in the separate legacy environment. |
| Actual scheduling/chess dependencies | **55 + 105 passed**, no skips. |
| Affected publication/Game Evidence v1/v2 contracts | **99 + 99 passed**, no skips. |
| Component tooling | **77 passed**, zero failures/skips/TODOs. |
| Source-removed Learning closure | Seven wheel/sdist artifacts built. **2 installation/import/migration checks per consumer** for Python 3.12.12/3.14.8 × wheel/sdist; all four zero-selected probes exited 5 as required. Remediation is an installed dependency in each. |
| Source-removed Game Evidence | **37 passed per consumer** for both Python versions × wheel/sdist; copied build sources removed, installed migration/CLI guards passed. |
| Restricted component/export/resource checks | Learning, Game Evidence and their actual dependency closure passed. Native restricted-user image also passed real PostgreSQL load/issue/review/replay with no producer/backend/broker/model modules. |
| Focused undefined-name, conflict and whitespace checks | Passed; these are not a full-product lint/build gate. |

Exact final invocations (the `checkout` and `evidence` values identify the actual
execution directories, not live data):

```sh
checkout=/Users/andy/tempo/.dev-copies/f1-remediation-integration
evidence=/Users/andy/tempo/test-results/f1-integration-20261015
export UV_CACHE_DIR=/private/tmp/tempo-f1-integration-uv-cache
cd "$checkout"
services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py "$evidence/native-issuance-final"
services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py "$evidence/installed-issuance-final"
TEMPO_COMPONENT_PYTHON="$checkout/services/learning/runtime/.venv/bin/python" TEMPO_REMEDIATION_EVIDENCE_DIR="$evidence/registered-remediation-final" node tools/component-build/test-lane.mjs --component learning-remediation --component scheduling --component chess-core --component content-publication-contracts
TEMPO_COMPONENT_PYTHON="$checkout/backend/.venv/bin/python" TEMPO_LEARNING_EVIDENCE_DIR="$evidence/registered-legacy-qualified" node tools/component-build/test-lane.mjs --component learning-review-application
TEMPO_COMPONENT_PYTHON="$checkout/services/game-evidence/.venv/bin/python" TEMPO_GAME_EVIDENCE_EVIDENCE_DIR="$evidence/registered-game-evidence-final" node tools/component-build/test-lane.mjs --component game-evidence
services/game-evidence/.venv/bin/python services/game-evidence/integration/qualify_artifacts.py "$evidence/game-evidence-artifacts-final"
TEMPO_COMPONENT_PYTHON="$checkout/backend/.venv/bin/python" node tools/component-build/check-components.mjs --component learning --component game-evidence
TEMPO_COMPONENT_PYTHON="$checkout/backend/.venv/bin/python" node tools/component-build/test-node.mjs tools/component-build/tests
/Users/andy/.local/bin/ruff check --select F821,F822,F823 services/learning/runtime services/learning/remediation services/game-evidence
npm run check:conflicts
git diff --check
```

The initial registered `--component learning` dispatch passed its 109-case native
inventory and 43 application cases, then stopped in the legacy tooling suite at a
missing Redis client import. The legacy native transition proof additionally
imports the full retained HTTP assembly and required Celery/Kombu and multipart
imports. Failed setup attempts remain in `registered-learning.log`,
`registered-legacy-final.log` and `registered-legacy-complete.log`; they are not
reported as passes. The final complete constituent suites above use separate
correctly provisioned environments. The independent runtime/image/clean consumers
never acquire these optional legacy service dependencies. No registration was
removed and no test was skipped or weakened.

Setup was scoped to locked runtime/Game Evidence components, the declared backend
seam and pinned contract requirements. Only the legacy transition test required
`backend/requirements.txt`; no analysis/model requirements or whole-repository
frontend dependencies were installed. Browser/tooling dependencies used their
local lockfiles. Node was 26.10.0, uv 0.12.22, Ruff 0.15.17, Playwright 1.63.0;
PostgreSQL 18.6 image digest is in every native resource receipt.

The 77-case tooling and component boundary results were obtained at `3b369830`;
the subsequent `9d133187` change adds only runtime tests and their prose inventory.
Those checker/tooling inputs and production sources are identical. The final
112-case native and four-consumer artifact results bind to `9d133187` directly.

Development failures are retained in `native-development*` and their logs:

- Initial run: 38 passed/55 failed; untyped nullable route parameter and an
  incomplete PGN fixture. The parameter now uses a typed string sentinel and the
  producer fixture contains a valid game result.
- Second run: 95 passed/4 failed; producer lease-token shadowing overwrote the
  bearer token, yielded maintenance retained its lease too long, and the fixture
  assumed away normal reinforcement. Delivery now keeps separate identities,
  rollback releases only its own current lease, and fixtures honor scheduling.
- Third run: 97 passed/2 failed; remaining reinforcement fixture assumptions.
- Fourth run: 104 passed, before the additional ordering/reconciliation/batch cases.
- Fifth run: 107 passed/1 failed; maximum wire batch exceeded the old maintenance
  transaction deadline. The dedicated bounded atomic intake budget addresses it.
- Sixth run: 108 passed/1 failed; timeout fault injection changed an already active
  PostgreSQL timer without rearming it. It now disables then arms the fault timer.
  The maximum batch and all other cases passed in that run.
- The first boundary check rejected dynamic resource loading. The implementation
  now statically imports only the declared remediation package; no checker rule
  was relaxed. A sandbox-only package refresh failed DNS and was repeated with
  network access; no test passed on that failure.

Every successful and failed native invocation records ownership-verified cleanup.
The final Learning report confirms its container, volume, restricted image tag and
temporary test data are absent; remediation and legacy reports confirm their
owned containers are removed; Game Evidence confirms container/volume removal.
Its native reports were copied outside the checkout and byte-verified before
removing generated originals. Raw inventories, JUnit, logs, distributions, source
hashes and a recoverable source bundle are retained in the evidence root. The
external `cleanup.json` records durable remote refs and final checkout/cache
removal. A stopped unrelated Learning container predating this task was observed
without mounts and left untouched because no F1 evidence established ownership.
The live checkout and two unrelated worktrees remain preserved.

## Independence proof and limits

The native runner launches installed Game Evidence and Learning processes with
separate disposable PostgreSQL databases and restricted identities. A forwarding
proxy loses the acknowledgement after acceptance commits; fresh processes replay
the immutable outbox. A real source correction then delivers an exact withdrawal.
Cross-domain database connections by both runtime identities are rejected.

For the Chromium case the producer is stopped after acceptance and Learning is
restarted alone. With no broker/model/optional provider workers, Learning performs
queue issuance, qualifying review/remediation settlement, receipt replay, normal
reinforcement and ordinary introduction. The existing strict schemas and durable
browser outbox exercise real HTTP response loss/reload and real PostgreSQL; no
Learning responses are mocked and non-Learning browser traffic is rejected.

This is a selected adapter/API proof, not full product, service-worker/offline shell,
two-tab UI, external provider, production routing or live-data acceptance. See the
[F2 handoff](../study/f2-remediation-handoff.md) for exact interfaces and remaining
teaching/routing/reload/two-tab work. #194 and #224 remain open for their actual
remaining implementation and cutover responsibilities. Controlled fixtures cannot
replace these PostgreSQL and installed-application proofs.
