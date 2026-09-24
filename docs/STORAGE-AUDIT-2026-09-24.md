# Storage audit — 2026-09-24

## Measured baseline

The local Docker `tempo-data` volume reports **88.81 GB**. Its `tempo.db` file is
88,806,961,152 bytes; the only other observed volume file was a 1.6 MB pre-tactics
backup. SQLite reports 4,096-byte pages, 21,681,387 pages, zero freelist pages,
and WAL mode. The Colima Docker filesystem was 98 GB used of 98 GB; macOS had
82 GiB available. Measurements came from read-only `docker system df -v`,
`PRAGMA` queries, and a `dbstat` aggregate scan of the stopped database.

| Driver | Measured size | Cause and disposition |
| --- | ---: | --- |
| `repertoire_card_priority_generations` | 87.78 GB (98.8% of DB) | 581,622 rows across 910 generations; only two published generations (1,790 rows). Every card repeated 118–163 KB of shared `edge_states` JSON. Superseded generations are rebuildable. |
| Legacy priority table | 183 MB | Older derived priority materialization, retained as a compatibility fallback. Review after the primary fix. |
| Integrity scan staging | 146 MB | Completed and older source-run JSON remains after issue publication. Clear completed runs in bounded slices after separate regression coverage. |
| Opening graph steps and indexes | about 147 MB | Multiple superseded graph generations remain. Retain only published and active generations after validating graph rollback and card-link behavior. |
| Game analysis candidates | 102 MB | Per-move candidate lines and positions; currently useful evidence. Consider compact encoding only if growth becomes material. |
| Docker build cache | 9.92 GB | Rebuildable cache, separate from the authoritative volume. Prune during the maintenance window for working room. |

At the audit point, the published generations contained 734 and 1,056 cards.
Their `evidence_json` alone occupied 86.8 MB and 172.3 MB respectively, so
repeated refreshes were capable of adding roughly 87–172 MB each before
indexes and other row content. The legacy table and graph staging are much
smaller contributors; neither explains the 88 GB file.

The checkout was 1.7 GB, mainly `node_modules` (898 MB), Rust `target` (409 MB),
and build outputs. Browser storage was not measured on the user's device. The
Pages demo service worker previously cached every GET, including cross-origin
and dynamic requests; local Docker Tempo does not register that worker.

## Highest-value design changes

1. **Bound generated priority history.** Publish first, then durably delete
   superseded generations in foreground-preemptible slices. Protect the
   published and currently staged generations. This removes nearly all of the
   observed 87.78 GB while retaining the visible result.
2. **Stop repeating unused edge evidence.** Scoring uses edge provenance in
   memory. Persist only the aggregate status fields and card-specific miss
   evidence that current readers need. The two published generations alone
   contained about 259 MB of `evidence_json` before this change.
3. **Compact after deletion.** With zero freelist pages at baseline, the
   database file can shrink only after stale rows are deleted and offline
   `VACUUM` completes. Verify a full backup and authority fingerprints first.
4. **Bound browser caches.** Restrict the Pages worker to scoped static assets
   and cap persistent workspace API responses by entry and total bytes.
5. **Later derived-data cleanup.** Integrity source runs, old opening graph
   generations, and legacy priority rows together occupy hundreds of MB. They
   are lower-value work than the primary fix and need separate lifecycle tests.

## Acceptance measurements

After recovery, `scripts/reclaim_priority_storage.py --report` should show only
the published and any active priority generation for each repertoire, zero
unused `edge_states` in published rows, and a compacted database below 2 GB.
SQLite integrity and foreign-key checks, non-priority table fingerprints, and
exact queue order must match the pre-maintenance baseline. Recheck file and
Colima disk usage after several subsequent priority refreshes to confirm that
growth remains bounded.

## Recovery outcome

Tempo was stopped for the maintenance window. A full `tempo-data` archive was
written outside the checkout at
`/Users/andy/tempo-backups/tempo-volume-2026-09-24.tar.zst` (1.8 GB), and
`zstd -t` passed. The decompressed `tempo.db` SHA-256 matched the source;
the adjacent manifest records both archive and database checksums. This backup
remains retained for recovery.

Pruning disposable Docker build cache reported 11.48 GB reclaimed and gave the
VM 5.6 GB of working room. The resumable cleanup deleted 579,832 superseded
priority rows, leaving exactly the two published generations (734 and 1,056
rows). It removed `edge_states` from every published row. Published card counts,
priority-score totals, and status fields matched the pre-cleanup values. The
non-priority table fingerprints and exact queue-order SHA-256
(`022de10f4b82d45b71f7b592e9221b3e168830d2017eda9d109907c510a6d22a`)
matched before and after compaction.

After WAL checkpoint and `VACUUM`, `tempo.db` was **891,523,072 bytes** (0.89 GB),
with zero freelist pages. SQLite integrity and foreign-key checks passed.
Filesystem trim released 92.4 GiB of guest blocks. The Colima disk allocation
fell from about 100 GB to 8.0 GB; the Docker filesystem went from full to
5.5 GB used with 88 GB available. macOS available space rose from 82 GiB to
164 GiB. `tempo-data` measured 893.1 MB in `docker system df -v`.

The restored Docker API returned healthy status, both repertoires with unchanged
priority status, and today's 117-card queue. At startup the database was
892,305,408 bytes. Two consecutive live refreshes of the 734-card repertoire
published generations 520 and 521; each retention task completed and left one
generation for that repertoire. The file measured 893,222,912 bytes after the
first refresh and 893,263,872 bytes after the second, an incremental **40,960
bytes** on the second refresh. The database remained below the 2 GB target.
After the full release suite and production rebuild, disposable build cache had
regrown to 4.19 GB. Pruning it again and trimming the VM left zero build cache,
the running Tempo services healthy, `tempo-data` at 899.1 MB, the Colima disk
allocation at 7.8 GB, 88 GB free inside the VM, and 159 GiB available on macOS.
