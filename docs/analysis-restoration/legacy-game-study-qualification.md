# Prepared finite qualification windows — unexecuted

The parent/resource coordinator must grant each window before the commands below
run. No grant is assumed. `python3 scripts/qualify_legacy_bridge.py --describe`
prints the frozen plan without reading Docker or starting resources.

Prerequisites: a clean pinned feature head; a coordinator-approved **already local
immutable** helper image ID containing Python3.12 or3.14 and `uv`; the existing
host Python with psycopg; source/evidence paths below. The runner never builds or
pulls an image. Missing image/tool/dependency or changed inventory stops the run.
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
| Remediation `tests` | 83 |
| Game Evidence `tests` | 113 |
| A bridge `test_legacy.py`, `test_legacy_observation.py`, installed independence case | 38 |
| Total | **251** |

Four collection and four execution commands use the existing Learning inventory
checker. Zero, duplicate, deselected, skipped, missing or extra cases fail. Count
changes require review rather than silently updating the expected inventory.
Full v1/v2 contract exchange is explicitly outside this bounded selection.

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
already exist. It receives **1CPU,256MiB,no swap,256PIDs,256MiB tmpfs**, a random
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
