# Scope refresh game-index validation plan

Changed behavior: a repertoire-only refresh can reuse an unchanged, complete,
currently published PostgreSQL game-position index. Persist a source receipt
only with successful complete index publication; verify its source and published
version before reuse. Missing, obsolete or conflicting receipts use the existing
full position-index path. Keep SQLite compatibility conservative where a
versioned publication cannot be proven.

Failure modes: source mutation during preparation/commit, obsolete receipt,
partial index, missing game, generation replacement, replay/rollback and source
fingerprinting inside a database transaction. Smallest proof: named receipt and
scope-refresh unit cases with explicit source/lease races; affected derivation,
cutover and canonical callers. A regular fresh PostgreSQL proof must exercise
actual position publication, scope refresh, restart, replay, rollback and real
foreground admission. CI owns final complete required candidate validation.
No TS/API/rendering change is planned.

The new reuse regression first failed on the existing full-index enqueue (0.34 s).
On the dirty candidate based on refresh head 561a2e3, macOS ARM64/Python 3.14.8,
the whole reuse/derivation/cutover scope passes 218 cases in 1.80 seconds. Test
doubles now include authoritative source reads and current source fields; the
existing incomplete-index visibility and foreground/replay assertions remain.
SQLite's focused handoff fixture prepares its own lease directly because its
standalone test does not start the asynchronous database writer; native claims
and lease transactions are exercised by the real PostgreSQL proof.

`TEMPO_TEST_INSTANCE=disposable TEMPO_REDIS_URL=redis://127.0.0.1:49502/0
TEMPO_GAME_INDEX_PROOF_URL=postgresql://postgres@127.0.0.1:49501/tempo_graph_proof
backend/.venv/bin/python scripts/check_postgres_game_index_reuse.py` passes on a
fresh PostgreSQL 18.6 schema44 helper with owned Redis keys. Four actual index
slices publish position version1 with proof; real branch admission starts scope
refresh, and it keeps version1 while advancing repertoire-dependent work. A
foreground review completes while the source fingerprint is paused outside all
transactions. Pool restart, stale replay and injected post-enqueue rollback
retain one review and exact job versions. Changed source selects full indexing;
a compute/commit race publishes no receipt, and between-slice source changes
withhold a mixed index until a fresh generation is admitted. A compatible
pre-upgrade cursor finishes but keeps verified_from_start=0, requiring full
indexing on its next scope refresh. Maximum observed index slice14.395 ms;
foreground review170.320 ms (includes cold command imports), without a general
latency/speedup claim. Initial proof prerequisite failure lacked review command
registration; it did not exercise the review and was repaired before this pass.

Readers copy authoritative source data under a fail-fast read budget and close
the connection before hashing/replay. Writers lock source, job and task in that
order, fence source and generation, and publish the receipt with the complete
position pointer. Existing historical indexes receive no fabricated receipts.
Additive migration044 and optional SQLite receipt shape preserve all records.

The helper database and its keys were removed; parent lightweight fixtures and
base images are retained for following work. Owning checkout and exact resource
teardown are recorded in root test-results/analysis-activity-2026-10-09; native
log game-index-native.log. `git diff --check` passes. No live study data was used.
CI owns complete current-head durability/browser gates; no complete pass or
merge/deployment readiness is claimed from these focused checks.
