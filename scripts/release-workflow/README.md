# Version-1 operator release controller

`npm run release:session -- ...` is the repository entry point for the reviewed
operator workflow. It replaces copying private task wrappers for each release.
The ordinary `tempo start` / `tempo restart` commands remain unchanged. This
controller supports the existing version-1 registration only; it does not
operate the separately owned version-2 rehearsal.

The controller orders the existing deployment, runtime, backup and development
budget owners. It introduces no scheduler, database writer, migration engine or
credentials. `build.mjs`, `release.mjs`, `release-session.mjs`, `closeout.mjs`,
`verify-installed.py` and `verify-ui-fix.mjs` are adopted without byte changes
from the supplied reviewed candidate.

## Prepare and execute

Preparation and execution require a separately authorized release action.
Preparation is read-only with respect to the installation, but does query Git,
CI, Docker/Compose and local image metadata and writes new private evidence.
Do not use a live checkout, reuse a previous evidence directory, or treat an old
receipt/token as current authority.

```sh
npm run release:session -- --prepare \
  --source "$release_source" --revision "$release_revision" \
  --registration "$release_registration" --state "$release_state" \
  --evidence "$release_evidence" --build-manifest "$release_build_manifest" \
  --minimum-free-bytes "$release_minimum_free_bytes"

npm run release:session -- --run "$release_evidence/prepared-session.json" \
  --budget-directory "$release_budget_directory" \
  --build-cpus "$release_build_cpus" --build-memory-mib "$release_build_memory_mib"
```

The variables are current operator inputs: a clean selected main checkout and
its full SHA, existing registration/state/environment/credentials, an API/web
build manifest bound to that checkout/revision, an empty private evidence
directory outside source/live state, a justified free-space reserve, and the
current owner's budget directory and explicit allocation. The existing installed
Playwright dependency must be present. Required exact-revision CI evidence and
all source/target/schema/credential guards remain authoritative.

Execution inherits the current owner's `TEMPO_DEVELOPMENT_BUDGET_TOKEN`. A
borrowed lease finishes `awaiting_owner`; its coordinator releases it after
cleanup. `--acquire-budget` is only for separately authorized self-acquisition.
`--helper-cpus` likewise requires authority for that temporary maintenance
allowance. Neither permission is saved in the prepared session. CPU selection
uses the selected checkout's current allocation owner, including #586's
explicit/daemon allocation policy; this controller adds no two-CPU ceiling.
Existing memory, ownership, lane, cleanup and kernel-limit checks remain in that
owner. Allocation is not evidence of idle CPU or adequate production headroom.

## Release and recovery boundary

- Build and verify immutable artifacts before shutdown. Revalidate source,
  registration, saved configuration, image identity and required running services.
- Preserve PostgreSQL, Redis and optional-engine definitions and identities.
  Only the selected backup `command` / `stop_signal` may additionally change,
  with the same locally verified immutable image. Other backup differences fail
  closed. The plan is bound to current source/environment/configuration hashes;
  it cannot authorize arbitrary service reconciliation.
- Skip fresh backup and migration phases when the owner reports no pending
  migration. This does not assert that a retained older backup covers later
  writes. Pending migrations use the existing fresh restore-verified backup,
  original-history guard and protected-state checks.
- Preserve additive tables, obligations and history. A pre-migration failure can
  resume the verified original services only under the existing checks. Once a
  migration/activation boundary is crossed, keep the guard/evidence and use a
  compatible fix forward; do not assume an old image supports a newer schema.
- Report availability immediately after actual readiness, service identities and
  authoritative deployment receipt readback. Post-readiness failure does not
  erase that availability fact; it is still reported as an incomplete session.
- Clean only exact owned helpers and claims. Partial self-acquisition is released
  once only after ownership, absent lanes and an empty claim directory are
  proved. Uncertain cleanup retains ownership/evidence and the original failure.
  Borrowed ownership always returns to its coordinator.

The controller never prunes images/volumes, deletes backup archives or clears a
user's browser state. The selected recurring-backup command remains a separate
source-owned policy; review its retention behavior against the actual release
authorization before execution. A backup lifecycle update is not permission to
discard existing recovery copies.

## Evidence and limitations

The supplied candidate's retained Node 26.12.0/Python 3.15.0 receipts record nine
passing synthetic cases (A1–A4, R1–R5), zero failed/skipped/cancelled, plus syntax
checks. File SHA-256 values were matched before adoption. Those cases cover
ordering, truthful availability, selected backup planning/identity, actual
adapter partial-allocation cleanup and UI request policy using fake external
capabilities. They do not establish real Docker/Compose behavior, database
recovery, browser rendering, installed state or a performance improvement.

No tests were added or run for this integration, as requested. The supplied
external test file and original logs remain outside this repository. Only source
inspection and static syntax/diff verification were performed. The unchanged
runtime/deployment APIs were checked against current main; its changed budget
API continues to accept the controller's explicit allocation.

`verify-installed.py` checks health, Settings, Tactics, Repertoire, the current
read-only queue-head probe and Game Review's existing reader GET. It deliberately
issues no queue HTTP GET because that route can create an attempt. The future
read-only queue contract remains owned by #585. The isolated UI checker blocks
service workers, all writes, queue issuance and off-origin requests; it never
uses Andy's browser/profile.

The controller does not implement or enable Game Review provider synchronization,
change its staged write guards, or complete genuine-game/review acceptance. It
can package an already-qualified merged Game Review change in a future
authorized release and report the installed reader surface. Feature readiness
and release-controller readiness remain separate.

The next operational use needs a fresh current-main selection, exact CI evidence,
current target/resource authority and a matching release request. This source
integration has not deployed anything.
