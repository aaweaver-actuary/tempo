# Intelligence analysis

## Games reader storage boundary

**Implemented source in this unmerged extraction; deployment unverified.** The [Games reader](games_reader.py) retains limit checks, cursor decoding/identity validation, accepted-input decisions and response envelopes. Its existing owner-supplied `read_only=True` transaction is explicit composition: run identity, accepted-input identity and the selected game page still share one owning read snapshot.

```mermaid
flowchart LR
  api[Games-only HTTP composition] --> reader[Analysis Games reader]
  reader -->|one owner-supplied read-only transaction| queries[Intelligence Games read projection]
  queries --> tables[Intelligence run / head / input / page tables]
```

The [centralized projection](../../../../../database/tempo_intelligence_database/adapters/games_read_projection.py) owns all four complete, parameter-bound SELECT queries and row extraction. It borrows the existing transaction and never opens or commits one. Run listing remains bounded to 100 identities; game reads request at most 11 payloads, including the existing one-row lookahead. Cursor handling, request bounds, query text/parameters and transaction timing are unchanged. The projection accesses Intelligence-owned `analysis_runs`, `analysis_heads`, `analysis_inputs` and `analysis_pages`; it has no access to another owner's database or tables.

This split adds no public wire operation or new runtime framework. Existing HTTP composition and the installed service continue to supply the owning Store. It does not create a Study dependency, start a worker/provider/engine, change grants or establish live resource isolation. [Schema relationships and fields](../../../../../database/tempo_intelligence_database/schema/README.md) remain source-only PostgreSQL documentation; no DDL or migrations changed.

Focused proofs: `services/intelligence/tests/test_game_browser.py` preserves cursor fences and limits and verifies bounded query parameters in one owner read transaction; `test_games_readonly.py` retains fake-store server denial and lifecycle tests. Existing native proof `services/intelligence/integration/test_games_readonly_postgres.py` remains the real PostgreSQL/grant boundary and was not run for this extraction.

## Finding assembly

**Implemented source in this unmerged extraction; deployment unverified.**
[`findings.py`](findings.py) owns the existing source-advice merge, engine evidence
enrichment, draft response-proposal assembly, uncertain engine-profile handling
and final ranking. It receives already captured facts and mutates the same finding
lists as before; it has no connection, query, transaction or worker capability.

```mermaid
flowchart LR
  store[Intelligence AnalysisStore composition] -->|classify accepted input first| classify[Source classification helpers]
  store -->|existing background read next| capture[Intelligence evidence rows]
  store -->|captured facts then merge| merge[Finding assembly and ranking]
  merge --> classify
  merge --> comparison[Pure engine comparison]
```

The centralized `AnalysisStore._derive_evidence` remains the composition owner:
source classification precedes its existing background evidence read, which
precedes this database-free merge. No query, lock, transaction boundary, report
publication order, public wire contract or cross-owner dependency changes.
`tests/test_findings.py` uses synthetic accepted facts to check advice identity,
proposal enrichment, ranking, unknown repertoire association and incompatible
engine identity, plus fake composition to preserve that operation order.
Existing native Analysis report and publication proofs remain unexecuted here.

## Read, cursor and export boundary

**Implemented source in this unmerged extraction; deployment unverified.**
[`read_work.py`](read_work.py) owns existing workspace admission, input/status/run/
receipt/report/finding/result responses, input-page flattening, cursor policy and
context/proposal export. Public wire models, canonical JSON/cursor encoding and
nested ReportPage import remain unchanged. It has no database driver, cursor or
query API.

```mermaid
flowchart LR
  store[AnalysisStore composition] --> app[Analysis read and export application]
  app -->|unchanged owner read-only context or borrowed status connection| fixed[Fixed Analysis read projections and storage guards]
  fixed --> tables[(Intelligence input / page / run / head / position / command / report / result tables)]
  app --> wire[Existing public serialization and read contracts]
  app -->|export consults lazily in original order| capabilities[Finding and report capabilities]
```

The [fixed reader](../../../../../database/tempo_intelligence_database/adapters/analysis_reads.py)
retains full bound SQL, row/count extraction, missing-record and visible-generation
guards and existing one-row lookahead. The original state-count cursor iteration
is preserved inside this projection. `AnalysisStore.read_transaction` lends the
same `owner.transaction(read_only=True)` context; `_status` borrows its caller's
connection and never opens another one. Input flattening, input-list/result/
input-status envelopes, report cursor encoding and ReportPage validation retain
their original post-read points; pending reports and receipt/run-list responses
retain their original in-read points. Cursor scope/revision/history filters,
malformed/stale rejection timing, row limits/order and lazy workspace/finding/
report lookups are unchanged. Proposal export returns the original proposal
object; this move grants no publishing, activation or Learning authority.

`tests/test_analysis_reads.py` uses actual fixed reads/Store composition with
synthetic rows to prove these boundaries, historical paging/cursor bytes and
lookahead, borrowed run-status reads, visible-generation rejection and export
lookup order. Existing packaged schemas and legacy report contracts remain
selected owning checks. No DDL, foreign key, write workflow, deadline, provider,
engine, private credential or live resource behavior is changed or exercised.
The [complete method inventory](../../../../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
continues to identify seven mixed Analysis methods; this is a bounded read slice,
not completion of the whole owner.

## Input admission and finalization boundary

**Implemented source in this unmerged extraction; deployment unverified.**
[`input_work.py`](input_work.py) owns the existing manifest/page/finalize application
workflows. Workspace admission, model JSON/hash preparation, page-reference and
game-count choices, final game-scope checks and public responses are unchanged.
The existing public models and `payload_digest` helper are reused.

```mermaid
flowchart LR
  store[AnalysisStore transaction composition] --> app[Input admission and finalization application]
  app -->|unchanged owner write context| fixed[Fixed Analysis input operations and storage guards]
  fixed --> tables[(Intelligence input and page tables)]
  app -->|finalize prior read closes before write| snapshot[Existing input snapshot capability]
  fixed -->|same connection and original call points| receipts[Existing command receipt guard and canonical writer]
  app --> wire[Existing models and payload digest]
```

The [fixed operations](../../../../../database/tempo_intelligence_database/adapters/analysis_inputs.py)
retain full bound queries, immutable input/page replay guards and locked page-set
completeness. `AnalysisStore.input_transaction` lends the existing owner write
context. Finalize retains its prior separate input read and early completeness
check, then receipt replay before the input/page locks, final game-scope decision,
accepted-state write and final receipt. Its bound receipt/writer properties
resolve the existing private methods before command JSON construction. Manifest
and page keep both original model serializations/hash preparation before the
write and response workspace lookup after commit. Finalize keeps response/hash
mutation and return points, including receipt replay inside its write context.

`tests/test_analysis_inputs.py` exercises actual fixed operations with synthetic
models/fake rows: immutable input/page conflicts, page-reference rejection,
finalization replay/page-set/game-scope guards, early admission failures and
receipt/serialization order. Existing schema and historical optional-metadata
page-digest cases remain unchanged. Real locking, rollback durability and native
concurrency are separate proofs and were not run. This adds no new schema,
model, generic query API, transaction/receipt writer or engine/provider dependency.
The [complete inventory](../../../../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
retains seven mixed methods; completion of input admission is a bounded slice.

## Run, command and dismissal boundary

**Implemented source in this unmerged extraction; deployment unverified.**
[`run_work.py`](run_work.py) owns the existing run submission, command decisions,
receipt response/digest mutation and dismissal application workflows. It reuses
existing position ordering, model/hash helpers and public responses; it has no
query/driver API. [`AnalysisRunOperations`](../../../../../database/tempo_intelligence_database/adapters/analysis_runs.py)
retains complete bound queries, atomic guards, locks and canonical receipt writer.

```mermaid
flowchart LR
  store[AnalysisStore transaction composition] --> app[Run and command application]
  app -->|same admission / 64-position batches / promotion contexts| fixed[Fixed run and receipt operations with atomic guards]
  app -->|original command / two-phase dismissal contexts| fixed
  fixed --> tables[(Intelligence run / head / position / command / dismissal tables)]
  app -->|lazy original lookup points| facts[Existing input / evidence / finding capabilities]
  app --> pure[Existing position ordering and model/hash helpers]
```

Run admission keeps captured input/evidence decisions before staging, existing
per-run locks, generation inheritance, 64-position write batches and final guarded
head promotion. Hash and position-state callbacks evaluate the same existing
application expressions at the original query-parameter points. Commands preserve
head lock → receipt replay → revision/cancellation/legacy guard → selected effects
→ status → receipt order. Dismissal preserves its first receipt transaction,
subsequent finding read and second user-state-locked transaction with a fresh
receipt guard before revision checks/writes. No receipt or transaction authority
is split. Bound receipt/writer and status/head capabilities preserve private-method
lookup before argument/model evaluation; receipt digest mutation and original
response object identity remain at their original call points.

`tests/test_analysis_runs.py` exercises fixed operations and actual composition
with synthetic rows: generation/hash timing, 64-item batch boundary and superseded
head return, all four command actions, replay before stale revision, receipt
conflict, first/second dismissal replay and stale dismissal. Existing input
composition, position-order and packaged-schema cases remain selected checks.
Real atomicity/concurrency, engine work and live resource isolation remain
unverified; no schema/model/provider/security behavior is changed. The
[complete inventory](../../../../../database/tempo_intelligence_database/adapters/README.md#complete-analysisstore-source-audit)
identifies seven remaining mixed methods; whole-owner separation is unfinished.
