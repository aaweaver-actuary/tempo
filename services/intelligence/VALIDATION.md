# Intelligence #109 validation receipt

Starting main: `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`.
Tested executable revision: `03ea13ce81900f1e225b5d0b7f2400bc051c233d`.
Branch: `codex/109-intelligence-sources`.
The follow-up commit adds this receipt only; its final identity is recorded in the
PR and preserved handoff report. Executable/package/test inputs remain unchanged.

## Results

All final selected cases passed, with no skipped cases or remaining failures.
One upstream Starlette/AnyIO deprecation warning remains; it is not suppressed.

| Proof | Actual execution |
|---|---|
| Registered Intelligence deterministic suite | 164 passed |
| Registered Intelligence native PostgreSQL suite | 60 passed |
| Declared chess-core owning dependency suite | 105 passed |
| Declared analysis-jobs contract owning suite | 92 passed |
| Source-free CPython 3.12.12 wheel consumer | 164 passed |
| Source-free CPython 3.12.12 sdist consumer | 164 passed |
| Source-free CPython 3.14.8 wheel consumer | 164 passed |
| Source-free CPython 3.14.8 sdist consumer | 164 passed |
| Installed wheel / Python 3.12 native runtime | 60 passed |
| Installed sdist / Python 3.14 native runtime | 60 passed |

These rows repeat owning cases across artifacts/runtimes; they are not distinct
new regressions. Reports retain actual JUnit identities and execution counts.
Component discovery, import/dependency boundaries, strict nonzero execution,
installed CLI/SQL resources, conflict checks and whitespace checks also passed.

The source-free proof built only Intelligence, chess-core and analysis-job
contracts, removed copied build sources, and installed wheel/sdist consumers with
unrelated service/model/broker distributions absent. Temporary consumers were
removed. It records 32 commands and 92 hashed source inputs.

The actual installed server runs in a separate interpreter with loopback-only
network guards. Its native regression starts without optional services, reports
missing registration, accepts an operator-scoped synthetic private registration,
fetches local provider counts through the background worker, gracefully stops,
and restarts with identical accepted evidence and no repeated provider call.
Native races additionally prove old leases/results cannot replace newer accepted
work, source failures remain independent, refresh rollback retains old evidence,
429 deadlines survive restart, and no pool connection/transaction spans HTTP.

## Exact final commands

Executed from the isolated checkout `/private/tmp/tempo-intelligence-109`:

```sh
UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-uv-cache uv sync --project services/intelligence --locked --offline --no-build-isolation --no-editable --reinstall-package tempo-intelligence
TEMPO_COMPONENT_PYTHON=/private/tmp/tempo-intelligence-109/services/intelligence/.venv/bin/python UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-uv-cache node tools/component-build/test-lane.mjs --component intelligence
UV_CACHE_DIR=/private/tmp/tempo-intelligence-109-uv-cache services/intelligence/.venv/bin/python services/intelligence/integration/qualify_artifacts.py /private/tmp/tempo-intelligence-109-evidence/artifacts-complete
node tools/component-build/check-components.mjs --component intelligence
node tools/component-build/test-lane.mjs --component intelligence --list
npm run check:conflicts
git diff --check
```

The registered lane's complete output is `component-lane-final.log`; discovery
and boundary inventories are `component-discovery.json` and
`component-boundaries.json`. `artifacts-complete/artifacts.json` contains exact
child commands, source/artifact hashes and consumer case identities. Native JUnit,
logs and verified resource-removal receipts accompany each installed native run.

An earlier development lane failed analysis-contract collection because
`jsonschema` was absent. Its pinned test dependency is now declared in the
Intelligence dev group/lock; the final registered 92-case contract run passes.
Earlier intermediate reports remain labelled with their original revisions and
are not relabelled as final evidence.

## Artifact and database inventory

- Wheel SHA-256: `7db5ea3622d5458c05e4f4d268115156574c6ae8b373c0072995fe396081a5ba`.
- Sdist SHA-256: `76a1bdc3416d40de277b1b66c64ef544bff80fd4a06e370f8ecd6afaac74e50e`.
- PostgreSQL fixture image: `postgres:18.6-trixie`, exact image ID
  `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
- Migration: Intelligence-owned `0001_sources`, packaged ordered manifest and
  startup checksum validation; no automatic startup migration.
- Runtime role: LOGIN, NOINHERIT, no administrative flags/memberships, no foreign
  database CONNECT, schema/table/temp creation or owner escalation. Tests prove
  migration/workspace authority and immutable source records cannot be rewritten.
- Immutable tables: snapshots, caller revisions, accepted evidence, command
  receipts and model-version bindings. Cache/job semantic inputs are protected
  from UPDATE; only named progress/result columns are mutable.

Fixture owners provision their own new cluster, databases, credentials and unique
volume. The fixture grants no Intelligence runtime access to Learning, Content,
Game Evidence, administrative or template databases. The installed native owners
`tempo-intelligence-109-65596a44aae566b1` and
`tempo-intelligence-109-a88e25e96ad78f2f` each report verified container and volume
removal. Other development/registered fixture cleanup receipts are preserved too.

## Preservation and limits

Evidence is preserved outside the disposable checkout at
`/Users/andy/tempo-evidence/intelligence-109`, including distributions, reports,
logs, source/ref preservation, exact final revision and cleanup audit.
Task-owned test resources and the eligible development checkout are removed
after preservation; final cleanup results are in the handoff report.

No live-provider smoke or credential registration, live-data migration,
deployment, merge, Actions/branch-protection change, or whole-product test pass is
claimed. Hosted merge qualification is separate from these local component proofs.

Credential IDs are a trusted immutable-operator contract without stored token
fingerprints. Maia wire v1 relies on an immutable configured model-version/artifact
binding; it cannot independently attest worker artifact bytes, ordered rating
pairs or speed conditioning. Single-FEN inputs cannot prove repetition history.
These supported-subset limits and #108/#110/#156/#4 handoffs are specified in
[INTERFACES.md](INTERFACES.md); no shared wire schema was changed.
