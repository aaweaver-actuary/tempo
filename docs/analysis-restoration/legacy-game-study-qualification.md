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
only supervises/provisions. Installed wheel bytes are rechecked/reinstalled offline.

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
