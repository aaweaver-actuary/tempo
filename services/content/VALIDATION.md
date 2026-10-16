# Content #108 validation and handoff

Assignment/base main: `0c7ef737bb2daacd8f671f252ce5a1d0787b1871`.
Tested executable: `b2feaa21c9b3e419e346585701efabe598a378c8` on
`codex/content-service-108`. The subsequent handoff commit changes documentation
only. No live deployment, data migration, Learning activation, or merge occurred.

## Reproduction and exact results

Run from the isolated repository root. All imports use installed distributions,
with no repository-root PYTHONPATH.

```sh
UV_CACHE_DIR=/private/tmp/tempo-content-108-uv-cache uv sync --project services/content --locked --no-editable --python 3.12 --reinstall-package tempo-content
services/content/.venv/bin/python services/content/run_suite.py pure
services/content/.venv/bin/python services/content/integration/run_postgres.py /private/tmp/tempo-content-108-evidence/native-final --benchmark
UV_CACHE_DIR=/private/tmp/tempo-content-108-uv-cache services/content/.venv/bin/python services/content/integration/qualify_artifacts.py /private/tmp/tempo-content-108-evidence/artifacts-final --consumers
TEMPO_COMPONENT_PYTHON="$PWD/services/content/.venv/bin/python" node tools/component-build/check-components.mjs --component content
TEMPO_COMPONENT_PYTHON="$PWD/services/content/.venv/bin/python" node tools/component-build/test-lane.mjs --component content --list
TEMPO_COMPONENT_PYTHON="$PWD/services/content/.venv/bin/python" UV_CACHE_DIR=/private/tmp/tempo-content-108-uv-cache node tools/component-build/test-lane.mjs --component content
npm run check:conflicts
git diff --check
```

Local runs reused the unchanged component tools from the primary checkout
read-only, with `TEMPO_COMPONENT_ROOT=/private/tmp/tempo-content-service-108`,
instead of installing Node/frontend dependencies. Preserved logs record the
exact absolute tool/interpreter paths.

| Proof | Result |
| --- | --- |
| Installed Content regular suite | 64 passed, zero failures/skips |
| Native PostgreSQL owning suite | 32 passed in 17.23 s |
| Registered component lane | 64 regular + 32 native + 105 chess-core + 99 Content-contract = 300 passed, zero failures/skips |
| Source-free wheel/sdist consumers | Identical 64 identities in Python 3.12 wheel/sdist and 3.14 wheel/sdist: 256 executions |
| Installed Learning/Game Evidence consumer | 1 named regression plus standalone assertions passed; Content absent from consumer environment |
| Boundary/discovery/packaging guards | Exact exports/dependencies, seven schemas, nonzero discovery, CLI/import isolation, packaged migrations, inventory/JUnit equality and zero-selection exit-5 probes passed |
| Conflict and whitespace checks | Passed |

Native cases cover both deletion lock schedules with observed PostgreSQL
blocking, durable bounded collection/re-registration, opposite-color shared
positions, crash/lease recovery, fairness, migration/grants, rollback, forged
payload/provenance rejection, immutable replay, policy coverage, special legal
moves, and installed CLI operations across fresh processes and two generations.
Earlier 63/18-case and diagnostic 28-case runs were development evidence only.

## Storage and native identity

Content owns one workspace-bound logical database, initial append-only migration
`0001_content`, and 18 tables. Migration SQL SHA-256:
`79655bc42742f7c22089f8ae4c80e166e35ae3980983e90bfb9e420214225b3a`.
The manifest/ledger bind its identity/checksum; startup applies no migration.

Tables own workspace metadata, staged input/manifests, sources/revisions,
tasks/leases/checkpoints, route artifacts/memberships/occurrences, canonical
positions/replies, exercise identities/versions/generation mappings, responses,
immutable publications/provenance and the collector. No Learning tables or
cross-service SQL are introduced.

Final native/benchmark owner: `tempo-content-108-983be067d5561bc8`; database
`content`; roles `content_owner` and `content_runtime`. Fresh guard databases
`learning`, `game_evidence`, and `intelligence` rejected the runtime credential.
The inspected `postgres:18.6-trixie` image was
`sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`.
The container had one CPU, 512 MiB and an owned persistent volume. Both removals
were verified, including the registered lane's separate native resources.
No live Study resource was inspected, restarted, migrated or removed.

## Representative measurement

Fixture: 512 routes, up to 64 plies, 2,048 explanation exercises, 935,241 input
bytes; 512 artifacts, 33,127 occurrences, 31,859 canonical route positions,
15,793 opponent positions and 494,581 legal replies.

| Measurement | Observed |
| --- | --- |
| Cold installed import / readiness | 0.146 s / 0.0147 s |
| Source registration | 2.002 s |
| Reconciliation | 4,604 slices; 104.849 s summed processing |
| Slice median / maximum | 13.32 ms / 274.78 ms |
| Writer transaction median / maximum | 2.37 ms / 243.98 ms |
| Publication construction/persistence | 0.510 s |
| Restart/read/replay | 2.381 s |
| Peak observed driver RSS | 84,197,376 bytes |

Writer durations include pool acquisition/commit; RSS includes the benchmark
driver, not PostgreSQL. Full setup/tests/measurement/cleanup took 140.78 s.
These observations describe one host/fixture, not arbitrary scale or production
latency. Canonical legal universes and immutable history are retained; collection
owns obsolete memberships and orphan route caches.

## Compatibility, evidence and remaining owners

Content emits unchanged shared v1 Publication, supporting 1–10,000 definitions
and all five exercise kinds, with independently validated chess legality.
Learning's supported `study_exercise` adapter preserves Content IDs. A valid
129-definition Content publication is explicitly rejected by Learning's current
128-fact bound. Its legacy opening adapter separately requires historical
FEN/moves card hashes. No truncation, chunk activation or identity rewriting
conceals these limitations. Game Evidence consumes its existing service-local
snapshot with both colors, routes/bindings and matching publication; activation
is null. No new shared wire schema is declared.

#193 owns durable user approval, identity/history bridging, acceptance/activation,
atomic complete-generation serving, coordinated large-publication staging and
empty-source retirement. README records complete-bundle versus paged-staging
options and atomic-switch requirements. #265 owns full PGN/connected-Study
import, integrity interpretation and segmentation; this service accepts structured
snapshots with stable source keys. These are intentional handoffs.

Preserved evidence includes exact commands/inventories/JUnit, source/dependency
hashes, benchmark rows/timings, installed artifacts/hashes, consumer results and
resource cleanup. The final handoff identifies its durable absolute directory
and pushed revision. Final artifact hashes in `artifacts.json` are authoritative.

Read-only GitHub checks reported Actions disabled, no main branch protection
(404), and no repository rulesets. Settings were unchanged. Focused local
qualification is complete; hosted/full-system, deployment and Learning activation
qualification are not claimed.
