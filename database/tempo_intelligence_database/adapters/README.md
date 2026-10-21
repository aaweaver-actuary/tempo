# Intelligence adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [analysis_store.py](analysis_store.py) | Intelligence-owned durable analysis composition and fixed head/evidence reads. Network/engine work never holds a transaction. |
| [analysis_reads.py](analysis_reads.py) | Fixed Analysis read queries, cursor-row limits, row/count extraction and missing/visible-generation guards on the owner's snapshot. |
| [analysis_inputs.py](analysis_inputs.py) | Fixed manifest/page admission and locked page-set finalization guards/writes; borrows the owner's input write and existing receipt authority. |
| [analysis_runs.py](analysis_runs.py) | Fixed run staging, command/receipt/user-state queries and atomic guards/writes on the owner's unchanged write contexts. |
| [analysis_publication.py](analysis_publication.py) | Fixed report snapshots, legacy/next reads, staged findings, user-state locking, publication/dismissal fences, conditional reopen propagation and report/head writes on borrowed owner contexts. |
| [analysis_engine.py](analysis_engine.py) | Fixed engine-work queries and writes, atomic capacity/lease/replay/execution guards on the unchanged owning background transaction; no engine execution. |
| [games_read_projection.py](games_read_projection.py) | Existing bounded Games-view reads over Intelligence-owned run/head/input/page tables; [reader policy and transaction composition](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#games-reader-storage-boundary). |
| [request_inspection.py](request_inspection.py) | Fixed request/cache/evidence/credential/provider-control reads on the owner's initial read-only snapshot. No transaction creation or application response policy. |
| [request_commands.py](request_commands.py) | Fixed refresh/retry command writes and atomic lock/replay/revision guards on the caller's existing write transaction. |
| [maia_operations.py](maia_operations.py) | Fixed profile/job/evidence reads and writes, job/cache locks and atomic replay/stale/transition guards on the owner's existing transaction. |
| [request_admission.py](request_admission.py) | Fixed request/snapshot/cache admission operations and request/snapshot replay guards on the caller's write transaction. |
| [provider_operations.py](provider_operations.py) | Fixed source selection, lease/fence, evidence publication and registration-result writes on the owner's background transaction. |
| [credential_operations.py](credential_operations.py) | Fixed credential row/head/registration operations and atomic generation/retirement guards on the owner's read/write contexts. |
| [store.py](store.py) | Intelligence owns every transaction here; no provider executes inside this boundary. |

The existing finding merge/ranking block now lives in the component's
[`analysis/findings.py`](../../../services/intelligence/src/tempo_intelligence/analysis/findings.py),
with [composition and focused proof](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#finding-assembly).
`AnalysisStore._derive_evidence` still classifies input, reads accepted evidence
and then merges the captured facts in that order. SQL and transaction ownership
remain here.

The initial mixed-method audit of Store and AnalysisStore is now closed at source
level, with fixed authority and composition explicitly retained below. A separate
pre-existing driver subclass in application runtime remains to be moved; this is
not whole-owner centralization or deployed independence. Complete request admission,
including snapshot preparation,
sorted cache work and expired-cache refresh, now lives in `source_requests.submit_request`.
`inspect` now only composes the complete application inspection workflow with
the fixed `RequestInspection` reader and existing retry/credential/clock
capabilities. `_command` now composes the complete application refresh/retry
workflow with fixed guarded writes. Maia profile/job/result/status application
workflows now live in [`maia_work.py`](../../../services/intelligence/src/tempo_intelligence/maia_work.py),
and the evidence response lives in `source_requests.read_evidence`. Source claim and
publication workflows now live in [`provider_work.py`](../../../services/intelligence/src/tempo_intelligence/provider_work.py);
credential status/registration workflows now live in
[`credential_work.py`](../../../services/intelligence/src/tempo_intelligence/credential_work.py);
Engine execution application workflows now live in
[`analysis/engine_work.py`](../../../services/intelligence/src/tempo_intelligence/analysis/engine_work.py);
capture/derivation/publication application work lives in
[`analysis/publication_work.py`](../../../services/intelligence/src/tempo_intelligence/analysis/publication_work.py). Read/status/cursor/export application work now lives in
[`analysis/read_work.py`](../../../services/intelligence/src/tempo_intelligence/analysis/read_work.py);
manifest/page/finalize application work lives in
[`analysis/input_work.py`](../../../services/intelligence/src/tempo_intelligence/analysis/input_work.py).
Run admission, command/dismissal and receipt response/digest work now live in
[`analysis/run_work.py`](../../../services/intelligence/src/tempo_intelligence/analysis/run_work.py). Response serialization and retry policy
now live in the component's
[`serialization.py`](../../../services/intelligence/src/tempo_intelligence/serialization.py)
and [`source_requests.py`](../../../services/intelligence/src/tempo_intelligence/source_requests.py).
The private `_retry_eligible` entry point delegates to the latter, which also owns
request/revision admission, snapshot preparation, descriptor/cache identity,
captured-source inspection and failure transitions. The Store
keeps the existing binding/sort/lock order and every transaction/query call point.
See the [request/cache boundary](../../../services/intelligence/README.md#request-and-cache-policy-boundary).
The Games read projection is already limited to queries and row extraction. DDL
discovery and migration execution remain database operations in `../ddl/`.

`RequestInspection` exposes only the existing fixed reads: `request(request_id)`,
`cache(cache_id)`, `evidence(evidence_id)`, `credential_status()` and
`provider_control()`. Request-head and credential-row reads reuse their existing
Store implementations; the three moved SELECTs retain their bound parameters.
`Store.inspection_transaction` owns the initial `read_only=True` context and lends
its connection to this projection. The application builds the envelope before
that context closes and invokes the existing final credential status capability
afterward. It has no generic query/execute port or cross-owner table access.

The four previously inventoried mixed Store credential workflows now delegate
the existing status/expiry/configuration and registration orchestration outside
this tree. Store retains transaction composition, startup/workspace validation,
fixed guards/writers and the existing ordered failure effects. This is not
completion of all Intelligence centralization: the driver-specific runtime edge
below remains. The full Store/Analysis method audits distinguish retained fixed
authority/composition from the completed application moves.

`RequestCommands` retains six fixed SQL operations and the existing receipt and
request-revision guards. It delegates request-head lookup, generation changes and
Maia work creation to the existing Store operations on the same connection;
it never opens/commits a transaction. `Store.command_transaction` supplies the
unchanged write context. The application calls the command lock before constructing
the payload, reads/replays the guarded receipt before the head/cache work, locks
caches in the same order and writes the receipt last. The eligibility callback
contains the original private-method/clock expression, preserving lazy lookup and
evaluation order. There is no generic execute/query capability.

`MaiaOperations` borrows the same connection for profile admission and job creation
inside existing submit/command transactions. Store's `maia_read_transaction` and
`maia_write_transaction` lend the existing read/write contexts for job/evidence
reads and result/status acceptance. Configuration is consulted before the fixed
profile read, even when configuration rejects the descriptor. An execution retry
revalidates the persisted job payload instead of consulting new workspace/default
inputs. Result serialization precedes the write transaction; status serialization
follows the locked job read. The application keeps binding iteration, semantic
validation, evidence construction and failed/cancelled cache decisions at their
original call points. The adapter retains unknown/stale/replay/transition guards,
job-before-cache locking and append-before-completion writes. Its bound append
property resolves the existing writer before application payload construction.
The shared fixed job lookup serves both original lock call points; no generic
SQL/execute port or new transaction authority is introduced. See the
[Maia/evidence boundary](../../../services/intelligence/README.md#maia-work-and-evidence-boundary).

`RequestAdmission` borrows the unchanged submit write transaction for the request
advisory lock, existing receipt read/replay guard, head read, immutable snapshot
write/read guard, cache initialization/locking, accepted-time read and request/head
writes. Profile/generation/Maia callbacks reuse the existing Store operations on
that same connection. Application admission retains descriptor preparation,
sorting, per-cache decisions and the final inspection only after commit, with
lazy workspace/configuration/inspection/clock lookup at the original call points.

`ProviderOperations` borrows the unchanged background transaction. Its fixed
provider/cache lock, origin-bound cache selection, lease updates, fence lookup,
evidence writer and registration-result update/release retain their existing
order. Application claim still observes the clock immediately after the control
lock, including no-control/early-exit cases; source status is consulted only after
cache selection. Publication resolves the existing evidence writer before UUID
and clock metadata, appends before registration acceptance and releases last.
No network, private credential file or new lease/transaction authority is added.
See the [provider-source boundary](../../../services/intelligence/README.md#provider-source-claim-and-publication-boundary).

`CredentialOperations` owns the fixed row read, locked generation check,
registration lookup/replay retirement guard, registration/head/resume writes and
conditional revoke write. It delegates the existing status projection on the
same connection; it never opens or commits a transaction or loads private files.
Application `project_status` preserves the original expiry short circuit and
public value conversion. `credential_status` keeps the private-loader import and
configured lookup inside the original active-status branch, after the initial
read closes. Registration replay returns status under the original lock without
consulting incoming expiry; new registration/revocation returns after the same
ordered writes. Store supplies unchanged read/write contexts and late clock/
configuration lookup. No configuration, credentials/security behavior or actual
private material was changed or accessed. See the
[credential workflow boundary](../../../services/intelligence/README.md#credential-workflow-boundary).

## Complete Store responsibility inventory

This source audit accounts for all 38 current Store methods (the original 32 plus
six transaction composition contexts). The seven previously inventoried mixed
workflows have been separated. Retained fixed-effect and validation code is
listed explicitly; this does not establish complete owner separation or live
resource isolation.

| Classification | Methods / remaining responsibility |
| --- | --- |
| Database authority and fixed operations (11) | `__init__`, `read_only`, `transaction`, `validate_startup`, `close`, `health`, `_head`, `_new_generation`, `_fenced`, `_release_provider`, `_append`: connection settings, schema/grant/workspace checks, guarded reads and atomic writes. |
| Composition / fixed effects (27) | `inspection_transaction`, `inspect`, `_retry_eligible`, `command_transaction`, `_command`, `refresh`, `retry`, `fail`, `_profile`, `_maia_job`, `maia_read_transaction`, `maia_write_transaction`, `evidence`, `maia_jobs`, `accept_maia`, `maia_status`, `admission_transaction`, `submit`, `provider_transaction`, `claim`, `publish`, `_credential_status`, `credential_read_transaction`, `credential_write_transaction`, `credential_status`, `credential_reload`, `credential_revoke`: wire existing application decisions/workflows to owning transactions and fixed effects. `fail` retains its existing fenced, ordered effect application around the extracted failure decision. |

## Complete AnalysisStore source audit

All 40 current methods are accounted for (the original 33 plus seven transaction
contexts). Existing read, input, run/command/dismissal, capture/derivation/publication
and engine application workflows now live outside this tree. Fixed operations
retain full queries, atomic guards and canonical writers on the original owner
contexts. No generic query API, dispatcher, new transaction/lease authority or
engine/provider execution was introduced.

Receipt advisory locking and payload binding remain root; response digest mutation
still precedes the canonical writer's own digest binding. Commands retain replay
before revision/effect work. Dismissal retains receipt-only write → finding read →
user-state-locked write, including both replay return points. Publication retains
snapshot/evidence reads → 64-finding staging → metadata/callback → guarded
publication. Engine work retains head lock, cancellation recovery, eligible-row
lock, workspace capacity read/lock/recheck, pure job callback, model/input checks,
UUID and execution/lease writes. Result validation/digest remain before the
background write; replay precedes lease/clock/execution guards and stage writes.
Failure retains its lease/generation guard before status validation, event write
before attempts/backoff/transition writes, and final cancellation settlement.
Application transition/hash/backoff bindings evaluate only at their original
query-parameter points. Public signatures, clock short circuits, locks, complete
SQL/parameters and transaction authority are preserved.

No mixed application/SQL method remains in this original Store/Analysis audit.
Retained fixed read/identity/lease/replay guards and startup/workspace/grant checks
are database authority; the source table below names composition explicitly. This
is not a whole-owner or whole-backend completion claim: the pre-existing runtime
driver edge remains below. See the
[read](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#read-cursor-and-export-boundary),
[input](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#input-admission-and-finalization-boundary),
[run](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#run-command-and-dismissal-boundary),
[publication](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#capture-derivation-and-publication-boundary)
and [engine](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#engine-work-state-boundary)
boundaries.

| Method / source lines | Classification | Remaining application or retained responsibility |
| --- | --- | --- |
| [`__init__`](analysis_store.py#L19) (19–21) | fixed | Owner/workspace binding only; retain adapter binding. |
| [`_workspace`](analysis_store.py#L23) (23–24) | composition | Delegates existing application logic to analysis/read_work.require_workspace through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`input_transaction`](analysis_store.py#L27) (27–29) | composition | Lends unchanged owner write context to fixed AnalysisInputs; finalize retains its prior separate input read and receipt replay return point. |
| [`manifest`](analysis_store.py#L31) (31–32) | composition | Delegates existing model/hash/admission/response workflow to analysis/input_work.admit_manifest; fixed AnalysisInputs retains original SQL/immutability/page-set guards and owner receipt/writer callbacks. |
| [`page`](analysis_store.py#L34) (34–35) | composition | Delegates existing model/hash/admission/response workflow to analysis/input_work.admit_page; fixed AnalysisInputs retains original SQL/immutability/page-set guards and owner receipt/writer callbacks. |
| [`read_transaction`](analysis_store.py#L38) (38–40) | composition | Lends unchanged owner read_only=True context to fixed AnalysisReads; no independent connection/commit. |
| [`inputs`](analysis_store.py#L42) (42–43) | composition | Delegates existing application logic to analysis/read_work.read_inputs through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`_input`](analysis_store.py#L45) (45–46) | composition | Delegates existing application logic to analysis/read_work.read_input through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`finalize`](analysis_store.py#L48) (48–49) | composition | Delegates existing model/hash/admission/response workflow to analysis/input_work.finalize_input; fixed AnalysisInputs retains original SQL/immutability/page-set guards and owner receipt/writer callbacks. |
| [`_head`](analysis_store.py#L51) (51–55) | fixed | Fixed optional-lock run/head lookup and not-found guard. |
| [`run_transaction`](analysis_store.py#L58) (58–60) | composition | Lends unchanged owner write context for run staging, commands and dismissal; no new commit authority or lifecycle. |
| [`submit`](analysis_store.py#L62) (62–64) | composition | Delegates complete existing application/receipt response logic to analysis/run_work.submit_run; fixed AnalysisRunOperations retains atomic guards, SQL and canonical writing on the same connection. |
| [`_status`](analysis_store.py#L66) (66–67) | composition | Delegates existing application logic to analysis/read_work.run_status through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`status`](analysis_store.py#L69) (69–71) | composition | Existing read-only head/status composition; wire relocated projection at the same point. |
| [`runs`](analysis_store.py#L73) (73–74) | composition | Delegates existing application logic to analysis/read_work.read_runs through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`games`](analysis_store.py#L76) (76–78) | composition | Already delegates existing GamesReader; late import and owner stay fixed. |
| [`_receipt`](analysis_store.py#L80) (80–81) | composition | Delegates complete existing application/receipt response logic to analysis/run_work.receipt_result; fixed AnalysisRunOperations retains atomic guards, SQL and canonical writing on the same connection. |
| [`_save_receipt`](analysis_store.py#L83) (83–84) | composition | Delegates complete existing application/receipt response logic to analysis/run_work.save_receipt_result; fixed AnalysisRunOperations retains atomic guards, SQL and canonical writing on the same connection. |
| [`receipt`](analysis_store.py#L86) (86–87) | composition | Delegates existing application logic to analysis/read_work.read_receipt through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`command`](analysis_store.py#L89) (89–90) | composition | Delegates complete existing application/receipt response logic to analysis/run_work.command_run; fixed AnalysisRunOperations retains atomic guards, SQL and canonical writing on the same connection. |
| [`capture_transaction`](analysis_store.py#L93) (93–95) | composition | Lends unchanged owner foreground read_only=True context to fixed AnalysisPublicationOperations; no independent connection/commit or new publication authority. |
| [`publication_read_transaction`](analysis_store.py#L98) (98–100) | composition | Lends unchanged owner background=True,read_only=True context to fixed AnalysisPublicationOperations; no independent connection/commit or new publication authority. |
| [`publication_transaction`](analysis_store.py#L103) (103–105) | composition | Lends unchanged owner background=True write context to fixed AnalysisPublicationOperations; no independent connection/commit or new publication authority. |
| [`capture`](analysis_store.py#L107) (107–108) | composition | Delegates complete existing capture/publication application flow to analysis/publication_work.capture_run; fixed AnalysisPublicationOperations retains snapshot queries, publication guards and ordered writes in the original contexts. |
| [`_derive_evidence`](analysis_store.py#L110) (110–114) | composition | Existing classify -> late findings import -> accepted evidence read -> merge wiring; pure merge already moved. |
| [`derive`](analysis_store.py#L116) (116–118) | composition | Delegates complete existing capture/publication application flow to analysis/publication_work.derive_report; fixed AnalysisPublicationOperations retains snapshot queries, publication guards and ordered writes in the original contexts. |
| [`report`](analysis_store.py#L120) (120–122) | composition | Delegates existing application logic to analysis/read_work.read_report through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`finding`](analysis_store.py#L124) (124–125) | composition | Delegates existing application logic to analysis/read_work.read_finding through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`dismiss`](analysis_store.py#L127) (127–128) | composition | Delegates complete existing application/receipt response logic to analysis/run_work.dismiss_finding; fixed AnalysisRunOperations retains atomic guards, SQL and canonical writing on the same connection. |
| [`export`](analysis_store.py#L130) (130–131) | composition | Delegates existing application logic to analysis/read_work.export_finding through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`engine_transaction`](analysis_store.py#L134) (134–136) | composition | Lends unchanged owner background=True write context to fixed AnalysisEngineOperations; no independent commit, engine/provider execution or new lease authority. |
| [`claim`](analysis_store.py#L138) (138–140) | composition | Delegates complete existing job/result/status/transition application workflow to analysis/engine_work.claim_engine_work; fixed AnalysisEngineOperations retains full SQL, atomic capacity/lease/replay/execution guards and ordered writes on the same background transaction. |
| [`accept`](analysis_store.py#L142) (142–143) | composition | Delegates complete existing job/result/status/transition application workflow to analysis/engine_work.accept_engine_result; fixed AnalysisEngineOperations retains full SQL, atomic capacity/lease/replay/execution guards and ordered writes on the same background transaction. |
| [`failure`](analysis_store.py#L145) (145–146) | composition | Delegates complete existing job/result/status/transition application workflow to analysis/engine_work.fail_engine_work; fixed AnalysisEngineOperations retains full SQL, atomic capacity/lease/replay/execution guards and ordered writes on the same background transaction. |
| [`remaining`](analysis_store.py#L148) (148–149) | composition | Delegates existing application logic to analysis/read_work.read_remaining through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`derive_next`](analysis_store.py#L151) (151–152) | composition | Delegates complete existing capture/publication application flow to analysis/publication_work.derive_next_report; fixed AnalysisPublicationOperations retains snapshot queries, publication guards and ordered writes in the original contexts. |
| [`mark_engine_unavailable`](analysis_store.py#L154) (154–155) | composition | Delegates complete existing job/result/status/transition application workflow to analysis/engine_work.mark_engine_work_unavailable; fixed AnalysisEngineOperations retains full SQL, atomic capacity/lease/replay/execution guards and ordered writes on the same background transaction. |
| [`engine_result`](analysis_store.py#L157) (157–158) | composition | Delegates existing application logic to analysis/read_work.read_engine_result through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`evidence_records`](analysis_store.py#L160) (160–166) | fixed | Empty-input short circuit, complete ordered owner evidence read and identity-set completeness guard; retain as fixed owner projection. |
| [`input_status`](analysis_store.py#L168) (168–169) | composition | Delegates existing application logic to analysis/read_work.read_input_status through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |

## Remaining runtime driver edge

A bounded source scan found [`runtime.py`](../../../services/intelligence/src/tempo_intelligence/runtime.py#L15)
`BackgroundConnection(Connection)` (lines 15–33), with its Psycopg import at line 8,
still outside the root tree. It shares a file with application `DatabaseRunner`
and deadline orchestration. This was outside the original two-entry SQL checklist
(`games_reader.py`, now split; `worker.py`, whose execute is an engine-client call).
It is unfinished physical organization, not an accepted exception. The next small
split should move the existing driver subclass root and retain runner/context
orchestration outside, preserving the single shared deadline and wait/close/error
behavior. No runtime split was included in this engine workflow batch.

The retained Store is byte-identical to its credential-workflow integration: all
38 methods remain classified as 11 database authority/fixed and 27 composition/
ordered fixed effects. Neither audit proves whole-backend/domain independence,
native concurrency or deployment/resource isolation. No actual engine/provider,
live state, migration or schema operation was performed by these source moves.
