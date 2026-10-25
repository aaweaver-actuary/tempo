# Optional graph translation copy and fresh import preparation

A native 14-table `pg_dump` custom archive, successful native consistent-snapshot
invocation/tool evidence, independently reread copy hash, and disposable
`pg_restore`/count/usability proof is an **equivalent accepted recovery path**.
It needs neither this JSON representation nor a newly invented snapshot-token
receipt. This helper's capsule/receipt/source pack is optional downstream offline
translation and import preparation after native recovery, or an alternative
authorized SQL export. It must not delay native capture or its verification.

This standalone helper verifies supplied files and returns plans. It opens no
database, runs no process, issues no HTTP request and imports no feature
implementation. The full source recovery copy remains independent of the new
Learning serving projection. Historical reviews, schedules, background work and
browser state are not fresh-transfer requirements.

The [fixed closure](../opening_graph_preservation/closure.json), owned by the
graph-source component, pins source `86d11afe…` and 14 complete public tables.
The optional `capture_query_plan()` returns its fixed `SELECT *` statements, primary-key
ordering, one `REPEATABLE READ READ ONLY` transaction and `ROLLBACK` as **data**
for the sole authorized executor. Stream all rows and original columns once;
derive counts and column inventories from those streams. No count rescan,
database-wide discovery, inferred selected IDs or independent capture is needed.

The raw capsule has exactly `tables`, containing all 14 named arrays. Retain all
original columns privately, including incidental nonopening/learning fields.
The separate public graph-only bridge CLI receives the existing `tables` plus
the selected captured publication generation's `graphs` projection. A newer
staged generation must not replace that pointer.

## Capture receipt and verification

For this optional SQL-to-JSON adapter only, its private receipt contains exactly:

- `schema_version` (integer 1), `source_commit`, explicit `source_target_id`;
- unique confirmed `selected_repertoire_ids` for the fresh serving scope;
- `capture_sha256` of exact raw capsule bytes and pinned `closure_sha256`;
- `consistent_read`: `read_only: true`, `isolation: "repeatable_read"`, exact
  observed `snapshot_id`, `all_queries_same_snapshot: true`;
- `tables`: all 14 names, each with `row_count` and original `columns` from its
  single captured stream. An empty array still needs its observed columns.

`validate_graph_capture(capture, receipt, closure_bytes, *,
expected_capture_sha256, expected_source_target_id,
expected_selected_repertoire_ids)` checks those bindings, minimum graph field
types, complete observed columns, PKs and source references. It reports exact
selected publication generations, graph/route/definition counts, source depth,
split/tombstone counts and usability blockers. A failed or missing publication
is retained and reported; it is not replaced with a guessed graph. Structural
copy validation is not chess legality, target activation or executor identity
authentication. Original graph-only bridge and target owners retain those checks.

## Recovery and existing import ports

`create_recovery_manifest()` describes exact bytes for `capture`,
`capture_receipt`, `source` (existing Content SourceSnapshot v1) and
`import_manifest` (existing `authored_import`). Store its canonical digest before
independently rereading the copy. `verify_recovery_copy()` checks that retained
digest, all exact file digests/sizes, the raw capture receipt binding and injected
owner validators. Complete scope and empty historical aliases are required.

The capture inspector closes over the **original raw byte digest**, independently
confirmed source target and selected IDs, then calls `validate_graph_capture`.
The authored-import inspector validates the existing public Content contracts and
returns the existing canonical source digest. Neither JSON labels nor fake test
inspectors establish actual capture or restore evidence.

`prepare_graph_import()` also requires the fresh-target owner's explicit
disposable/initialized/empty target observation. Its injected planner receives
the original source bytes, original manifest facts, explicit target and
`expected_revision=0`. It prepares the existing public Content import-plan input;
the composition root subsequently uses the existing approved operation and whole
`tempo-a-bridge ... apply OPERATION_ID` workflow. There is no private serializer,
second activation writer or direct table importer here.

`verify_imported_graph()` compares the independently observed **full Content
source** canonical digest. Learning card/route rows cannot substitute for that
source or the retained raw files. The fresh-target owner separately verifies
completed activation receipts, new default state and actual target graph facts.
The returned manifest/source digests, workspace/source identifiers and graph
facts are serialized evidence for outer composition; no caller must import
another feature's private implementation.

Architecture impact: no runtime boundary changes. This offline composition
helper uses supplied bytes and injected public capabilities only. Source and
synthetic tests do not authorize actual import, reset, deletion or cutover.
