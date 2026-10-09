# Integrity publication validation plan

Replace fixed publication caps with generation-scoped bounded pages, preserve the
previous complete generation while staging, fence obsolete sources, validate cards
conservatively, then switch one publication pointer. Remove superseded staging
through bounded cleanup. SQLite retains its atomic compatibility publisher.

Failure modes: partial issue/block visibility, one issue with many card sources,
source replacement, lost cursor, duplicate replay, crash before pointer switch,
shared card eligibility, obsolete cleanup deleting current data, and foreground
contention. Start with named page/publication unit regressions and affected graph,
integrity, queue and segmentation callers. A fresh PostgreSQL proof must publish
large scans, preserve old readers across restart/rollback, and retain fail-fast
budgets and foreground admission. CI owns final full candidate validation.

The named cap regression failed on the original publisher (0.36 s). The new
publisher stages one issue and at most 32 unique card blocks per page. Read-only
current-publication views keep legacy results visible until the first accepted
complete generation; a separate indexed eligibility view conservatively waits
through queued/running/retrying/failed scans. Readers never see partial issue or
block pages. SQLite views preserve its existing atomic compatibility publisher.
No historical issues, reviews or study records are migrated away or fabricated.

Compatibility retry retains phase/cursor and staged results when provenance is
current. A historical pre-upgrade validation cursor replays publication pages
from retained scan candidates; obsolete provenance starts a new scan only when
the current graph is ready. Final acceptance rechecks completeness, serializes
graph admission and repertoire repair, validates the saved scan identity, and
switches the publication pointer with validation state. Cleanup removes bounded
obsolete pages and records its advancing count; byte-order indexed namespace
ranges avoid locale-dependent colon/semicolon ordering. The native proof first
exposed that cleanup defect; its failure log is preserved alongside the repair.
Graph cleanup combines both block stores in one statement, retaining PR #102's
seven-command bound rather than relaxing the existing regression.

On the dirty candidate based on game-index 3d644b0, macOS ARM64/Python 3.14.8:

- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_integrity_publication_pages.py backend/tests/test_postgres_cutover.py backend/tests/test_postgres_opening_graph.py backend/tests/test_repertoire_integrity.py backend/tests/test_repertoire_statistics.py backend/tests/test_repertoire_opportunities.py backend/tests/test_daily_queue_sparse_unlock.py backend/tests/test_opening_graph.py backend/tests/test_durable_work_queue.py -q -o cache_dir=.pytest_cache --rootdir=.`: 351 passed, 16.80 s.
- After the final completeness/advisory-lock and pre-upgrade retry additions, the
  new page tests plus whole cutover/graph files pass: 222 cases, 1.73 s. The other
  unchanged compatibility callers were not redundantly rerun.
- `TEMPO_TEST_INSTANCE=disposable TEMPO_REDIS_URL=redis://127.0.0.1:49502/0 TEMPO_INTEGRITY_PROOF_URL=postgresql://postgres@127.0.0.1:49501/tempo_graph_proof backend/.venv/bin/python scripts/check_postgres_integrity_publications.py`: passed on a fresh marked PostgreSQL 18.6 schema45 helper, Redis 7, 25.61 s including setup. Maximum observed complete slice 196.695 ms; foreground review 188.149 ms including cold imports. Background writes retain the actual 250 ms transaction / 25 ms lock limits. These are local measurements, not general latency claims.
- `make plan` reviewed and `git diff --check` passed. No TS/API-shape or rendering
  changes; CI still owns the required frontend/build/browser checks. Full native
  runner and complete current-head CI validation remain pending.

The helper database and its admission keys were removed; shared base images and
parent task-owned lightweight fixtures remain for following proofs. Exact parent
container identifiers/teardown are recorded outside the clone under root
`test-results/analysis-activity-2026-10-09`; native result is
`integrity-publication-native.log`, failed cleanup evidence is
`integrity-publication-cleanup-failure.log`. No live study data was used.

Before native runner handoff, integrity source/page/card reads were explicitly
routed to the authoritative PostgreSQL pool, preventing a lagging read endpoint
from skipping validation or scan inputs. Updated read doubles require that flag.
The affected 222-case scope passes in 1.90 s; the clarified evaluation read
factory is rechecked by its named case. The native runner will validate this
final source revision, including the same large publication proof.
