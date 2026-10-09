# Segmentation provenance and retry validation

Resolve a presentation's trained color from matching current published graph
provenance, preserving study records and paused/failed staging. Fail with the
exact repertoire/card/generation when that provenance is missing or conflicting.
Retry a compatible failed projection at its saved phase/cursor; fence source or
graph replacement and restart from current inputs when obsolete. Drain one superseded run at a time, including imported run identifiers, so
bounded child cleanup cannot fall through to large parent cascades.

Risks: assumed colors, silently skipped legacy presentations, stale graph reads,
source edits while computation is outside the database, replay duplication,
failed retry losing group pages, graph replacement accepting stale publication,
old generation cleanup deleting current results, and foreground contention.
Smallest proof: named provenance/retry tests plus the existing worker tests.
Expand to activity-control/cutover callers and actual PostgreSQL/Redis proof for
row locks, restart, failed retry, publication fencing and bounded old-run cleanup.
The regular deployed segmentation durability proof remains required. No browser
or rendering changes in this PR; CI owns full required candidate validation.

Baseline: all six new regular-suite cases fail on integrity candidate 457f323:
null color raises generic Unknown trained color; conflict/missing/invalid
provenance does not identify its source; manual retry resets groups to queued.
The isolated baseline command used the future test file in /tmp before changing
repository source. Evidence: root test-results/analysis-activity-2026-10-09.

Implementation keeps chess traversal outside authoritative read transactions,
resolves at most two distinct matching graph colors, then rechecks the card and
current graph under short result-write locks. Compatible manual retry retains
its run, phase and payload; obsolete retry queues current graph inputs and
returns that full serialized task. Cleanup drains one obsolete run at a time,
with eight child rows per slice and guarded parent deletion. One additive
schema46 index selects obsolete runs by repertoire. Equality child lookups also
handle imported IDs and avoid locale-dependent namespace ranges. SQLite's
existing advisory compatibility behavior is unchanged; no learning data is
modified by the projection worker.

Dirty candidate based on integrity 493e225, macOS ARM64/Python 3.14.8:

- Original six-case baseline failed in 0.24 s (scratch test file outside source;
  actual generic null-color error and reset-to-queued retry). See baseline log.
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_segmentation_provenance.py backend/tests/test_postgres_opening_segmentation.py backend/tests/test_postgres_cutover.py backend/tests/test_canonical_repertoire_prefix.py backend/tests/test_integrity_publication_pages.py backend/tests/test_background_activity.py -q -o cache_dir=.pytest_cache --rootdir=.`: 379 passed in 20.87 s before final cleanup simplification. The initial scope command named a nonexistent file and ran zero tests; it is not counted as validation.
- After final one-run cleanup and new boundary cases, the two whole affected
  segmentation files pass 15 cases in 0.26 s. Unchanged callers were not rerun.
- `TEMPO_TEST_INSTANCE=disposable TEMPO_REDIS_URL=redis://127.0.0.1:49502/0 TEMPO_SEGMENTATION_PROOF_URL=postgresql://postgres@127.0.0.1:49501/tempo_graph_proof backend/.venv/bin/python scripts/check_postgres_segmentation_provenance.py`: passes against a fresh marked PostgreSQL 18.6 schema46 helper/Redis7 in 1.36 s including setup. Maximum complete slice 34.914 ms; foreground review 191.767 ms including cold imports. Actual background transaction/lock budgets remain 250/25 ms. No general latency claim.
- The first native run exposed an obsolete-retry descriptor being serialized
  instead of the queued task; a new named regression and the native proof protect
  the repair. A later scratch assertion used unsupported row slicing and failed
  after cleanup; supported positional access retains the original assertions.
- `git diff --check` passes. No TS/API-shape or rendering change. Complete
  current-head/current-base CI and the regular deployed segmentation workflow
  remain pending. No full local gate or release/deployment claim.

Evidence and the failed iterations are retained outside the clone in root
`test-results/analysis-activity-2026-10-09/segmentation-provenance-*.log` and
`segmentation-baseline.log`. Each marked helper database and owned admission
keys were removed. Parent task fixtures remain for following proofs; exact
identifiers and teardown are already recorded there. The earlier schema45
broad durability run passed all background work, command recreation and backup,
then failed the passive retention poll; admission PR #120 fixes its test driver.
This change is stacked after integrity PR #126; subsequent coverage recovery and
activity/notification work remain separate planned repairs.
