# Prepared finite qualification windows — unexecuted

The parent/resource coordinator must grant each window before the commands below
run. No grant is assumed. `python3 scripts/qualify_legacy_bridge.py --describe`
prints the frozen plan without reading Docker or starting resources.

Prerequisites: a clean pinned feature head; a coordinator-approved **already local
immutable** helper image ID containing Python3.12 or3.14; the existing
host Python with psycopg; source/evidence paths below. The runner never builds or
pulls an image. Missing image/dependency or changed inventory stops the run.
Artifact dependency downloads use the pinned contract requirements and locked
Game Evidence runtime export; the chess1.11.2 pinned pure-Python sdist may build.
No whole-repository installation, engines, app services or browser is included.

The separate [real backend native plan](legacy-game-study-backend-native.md)
prepares the existing two vertical scenarios plus rollback over real HTTP,
Celery, Redis and backend migration50. Its three cases are unexecuted and require
their own grant. The five module-only native cases below do not qualify that
backend boundary.

## Artifact window: 15 minutes work + 5 minutes verified cleanup

After coordinator approval, use its actual local image ID as
`TEMPO_BRIDGE_HELPER_IMAGE_ID` (literal `sha256:` plus64hex digits):

```sh
node scripts/legacy-bridge-window.mjs \
  --python /Users/andy/Documents/Codex/2026-10-18/task-2/tempo/backend/.venv/bin/python \
  --mode artifacts --approved-window \
  --evidence /Users/andy/Documents/Codex/2026-10-18/task-3/evidence/qualification \
  --helper-image-id "$TEMPO_BRIDGE_HELPER_IMAGE_ID"
```

One owned helper: **1CPU,768MiB,no swap,128PIDs,256MiB tmpfs**. Source is mounted
read-only; only the new owned evidence directory is writable. No Docker socket,
credential files or live data mounts enter the helper. Descendants inherit its
cgroup, including children that change process groups. Kernel limits are checked
inside before work. Native fixture allocation is zero in this window.

Build exactly six wheels and six sdists: content-publication contract1.0.0,
game-evidence contract3.0.0, chess-core0.1.0, remediation0.1.0,
Game Evidence0.1.0 and A bridge0.1.0. Check GE/remediation/A-bridge locks. Install
the captured wheels, remove restricted copied `src` directories, and use isolated
Python with installed-origin boundary tests. Exact collect→execute inventories:

| Selection | Required cases |
| --- | ---: |
| Game Evidence contract `tests/test_v3.py` | 17 |
| Remediation `tests` | 85 |
| Game Evidence `tests` | 125 |
| A bridge `test_legacy.py`, `test_legacy_observation.py`, `test_legacy_study.py`, installed independence case | 57 |
| Total | **284** |

Four collection and four execution commands use the existing Learning inventory
checker. Zero, duplicate, deselected, skipped, missing or extra cases fail. Count
changes require review rather than silently updating the expected inventory.
Every pytest command carries `--maxfail=1`: first failure stops the window, keeps
`complete=false`, and an incomplete inventory is a failure. The +4 reviewed
cases at the previous255-case checkpoint were the paired ended-foreign-policy
controls in GE and remediation. The new29 are10 offline-v3 producer and19
explicit operator cases. This revised inventory is a proposal for fresh review
and authorization; the earlier failed255-case grant cannot be reused. The
cross-component SQLite/source integration is separate from this installed suite.
Full v1/v2 contract exchange is explicitly outside this bounded selection.

### Pinned tool setup inside the same artifact command

No separate image build/pull or host install is needed. The already-local API
image is a candidate only; Python version, architecture and cgroup caps are
checked at startup, and an explicit Python entrypoint prevents app startup.
The command above downloads **uv0.9.3** from its publisher's
[PyPI release metadata](https://pypi.org/pypi/uv/0.9.3/json), verifies the exact
wheel hash, extracts only its single `uv` binary into owned evidence, and checks
its reported version. It runs no installer script and uses no new credential.

| Linux architecture | Wheel bytes | SHA-256 |
| --- | ---: | --- |
| aarch64 | 20,022,427 | `406ab1a8b313b4b3cf67ad747fb8713a0c0cf3d3daf11942b5a4e49f60882339` |
| x86_64 | 21,318,117 | `73ae4bbc7d555ba1738da08c64b55f21ab0ea0ff85636708cebaf460d98a440d` |

The exact publisher wheel URLs are frozen in `UV_WHEELS`. Expected setup cost:
one20–21MB transfer plus extraction/version check, normally seconds; no measured
runtime is claimed. Download is capped at24MiB with20s socket timeout, extracted
binary at64MiB, version subprocess at10s, all inside the existing900s work/300s
cleanup allocation and1CPU/768MiB/128PID caps. The controller's whole-window
deadline also covers slow transfers. Missing/changed/unsupported tools stop;
there is no image/installer fallback. Native reuses and rehashes this retained
wheel offline. Compatibility with the lock export is unqualified until the
artifact run. A coordinator-approved actual local image ID is still required.

## Module-native window: 10 minutes work + 5 minutes verified cleanup

Only after the artifact run passes, use its exact emitted directory:

```sh
node scripts/legacy-bridge-window.mjs \
  --python /Users/andy/Documents/Codex/2026-10-18/task-2/tempo/backend/.venv/bin/python \
  --mode native --approved-window \
  --evidence /Users/andy/Documents/Codex/2026-10-18/task-3/evidence/qualification \
  --artifact-run "$TEMPO_BRIDGE_ARTIFACT_RUN" \
  --helper-image-id "$TEMPO_BRIDGE_HELPER_IMAGE_ID"
```

The helper image, pinned source head and all12 artifact hashes must match. Reuse
the existing domain OwnershipFixture, with its generated roles/database identity
and packaged module schema checksum. PostgreSQL image `postgres:18.6-trixie` must
already exist as the coordinator-supplied immutable ID
`sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
It receives **1CPU,256MiB,no swap,256PIDs,256MiB tmpfs**, a random
loopback setup port and one owned internal network. The helper joins that network
without a Docker socket and accepts only generated `native-db/learning_runtime`
fixture settings. No external database URL option exists. Total container caps:
**2CPU,1,024MiB,384PIDs**, two containers and one owned internal network; the host
only supervises/provisions. Installed wheel bytes and the retained environment manifest are rechecked offline;
no native reinstallation repairs or hides a changed environment.

Exactly five cases, each collected then executed once, in
`services/learning/remediation/integration/test_postgres.py`:

1. `test_native_outer_rollback_includes_obligation_and_completed_receipt`
2. `test_native_response_loss_restart_and_new_transport_have_one_effect`
3. `test_native_attempt_frontier_cannot_be_invented_changed_or_expanded`
4. `test_native_owner_projection_keeps_replay_prior_attempt_fence_and_invalidation`
5. `test_native_legacy_variant_reuses_receipt_frontier_settlement_and_outer_transaction`

The fifth now retains the scoped proof through actual module persistence. These
remain synthetic module ReviewOutcome tests, not a normal Study review or backend
HTTP/migration/queue proof. No claim of actual priority delivery follows.

## Supervision and teardown

The existing development budget/activity gate owns allocation and journalling.
The Python controller applies phase alarms, bounded process-group TERM/KILL,
per-command deadlines and5s native SQL/1s lock/3s connection deadlines. The Node
wrapper adds a hard overall20-minute artifact /15-minute native watchdog.
Work deadlines include setup and dependency/test work. Each cleanup phase reserves
300seconds; process termination time is reserved inside phase budgets.

Cleanup verifies exact owner labels, stops/removes the helper (all descendants),
then closes the verified PostgreSQL fixture, removes its owned internal network
and proves absence. It never globally prunes, stops other services or deletes
shared images. Ambiguous ownership/absence or exhausted cleanup is a failure:
retain evidence/resource journals and report the exact remaining owned names.
Forced termination cannot silently release an uncertain managed allocation.
Temporary fixture credentials are removed; captured wheels, environment, receipts,
logs and hashes remain outside the checkout for native reuse/review. They can be
removed only after the parent finishes that reuse and verifies preservation.

Runner guard/unit tests and syntax checks are source evidence only. This new
runner, its containers, package builds/locks and native paths have not executed.


## Private tmp inputs and durable receipt copy-back

The runner mounts only its own mode-0700 `/private/tmp/tempo-bridge-*` stage,
not the development checkout or durable evidence directory. It copies only
tracked allowlisted runner/backend/package inputs and hashes each file into a
source manifest, verified again inside the read-only source mount. Other package
sources, ignored files and private credential paths are excluded. The existing
six installed-wheel byte comparisons share one implementation. A successful
artifact run additionally binds retained tools, twelve artifacts, source-removed
test inputs and the installed environment/dependencies to a hash manifest; native
windows refuse drift rather than reinstalling over it. Earlier receipts lacking
this installed-input manifest cannot authorize these new native runners.

Fresh disposable fixture passfiles are created directly in the owned tmp stage.
Live credentials are never copied, and fixture private paths, env files and pytest
scratch are excluded from durable output. On exit, regular receipts/logs and
qualified inputs are atomically copied back with per-file hash checks and fsync.
The stage is removed only after resource absence and durable copy-back both
succeed. Failure retains the owned stage path in `host.json`; it never silently
loses evidence or claims verified cleanup. Work/cleanup deadlines, exact selected
cases, image pins and kernel caps are unchanged. No product, migration or Study
behavior is changed by this amendment.

Focused regular runner tests cover these filesystem/provenance boundaries and
a mocked controller transfer. They do not establish Docker bind availability,
artifact builds, installed test execution, PostgreSQL/Redis or native success.
The parent must grant each of those actual gates separately.

## Interrupted-command diagnostics

Work timers now atomically persist `host.json` / `inside.json` before each logged
command starts and again after its process group is known. `pending_stage` and
the command's pending/running state, UTC start, completion time, elapsed seconds
and error type distinguish setup, collection and execution interruptions. A
helper SIGTERM reaches its receipt-writing finalizer. An abrupt kill can leave
the last pending/running record; that record is evidence of incomplete work,
never a successful test or qualified artifact. It cannot identify a pytest case
that pytest did not report. Control captures do not log arguments or output.

Receipt writes consume the existing work deadline. Cleanup timers do not depend
on incremental receipt I/O; process-group TERM/KILL reserves, resource caps,
inventories and source/artifact identity guards are unchanged. These diagnostic
changes do not authorize a rerun or extend a window. Earlier partial artifacts
remain unqualified and a future actual run requires a fresh explicit grant.

## Current backend dependency prerequisites

The artifact window additionally builds three prerequisite wheels from the same
tracked head: Scheduling, Learning review application and Learning runtime. They
are installed into the isolated environment before source removal and the
unchanged 284 component tests. These are separate from the six component wheels
and six sdists; their installed bytes and retained hashes are verified again
before native reuse. They do not claim the complete owning dependency suites.
A same-version package in an older helper image cannot substitute for these
current wheels. Native windows still perform no build or dependency install.

Cleanup accepts Docker's identity-specific `network NAME not found` response in
addition to `no such network`. Unknown daemon errors and different network names
still retain the cleanup claim. Earlier failed receipts remain historical
failures even when an independent cleanup reconciliation proves absence later.

## Native retry composition

The local runtime schedules `app.tasks.recover_operations` every five seconds.
The native rollback case publishes one such task through the real configured
Celery app after the retained retry becomes due. A short owned publisher process
uses the same isolated broker namespace and is stopped on success or failure;
the existing worker claims and redelivers the original durable envelope. No
module handler is called directly and no retry state/deadline is rewritten.
The case awaits completion before replaying intake and retains all rollback,
receipt-identity, attempt-count and exactly-once assertions. Status polling uses
the supported background work-class header so monitoring does not impersonate
foreground Study activity. No scheduler or admission policy changes are made.
