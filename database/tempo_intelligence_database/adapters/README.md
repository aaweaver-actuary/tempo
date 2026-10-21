# Intelligence adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [analysis_store.py](analysis_store.py) | Intelligence-owned durable analysis composition and remaining mixed write workflows. Network/engine work never holds a transaction. |
| [analysis_reads.py](analysis_reads.py) | Fixed Analysis read queries, cursor-row limits, row/count extraction and missing/visible-generation guards on the owner's snapshot. |
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

This is a bounded extraction, not a complete removal of application policy from
these two older adapters. Complete request admission, including snapshot preparation,
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
`AnalysisStore` still mixes manifest/page/finalize/run admission, command/receipt
responses, report publication/dismissal and engine execution state transitions
with their queries. Read/status/cursor/export application work now lives in
[`analysis/read_work.py`](../../../services/intelligence/src/tempo_intelligence/analysis/read_work.py). Response serialization and retry policy
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
completion of all Intelligence separation: AnalysisStore remains substantially
mixed, as the full method audit below shows.

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

All 34 current methods are accounted for (the original 33 plus one read-context
composition). The first read/cursor/export slice moves twelve complete existing
application blocks, including pure workspace admission/export and input-page
flattening, to `analysis/read_work.py`. `AnalysisReads` retains complete query and
storage-guard authority: missing input/receipt/report/result and accepted finding
generation guards stay root. State-count cursor iteration remains in the fixed
projection as originally written; no `fetchall` substitution or cursor API leaks
into application code. One original input lookup is shared by its two original
call points, preserving each context. Public signatures, SQL/parameters, one-row
lookahead, cursor scope/hash/encoding, late ReportPage import and serialization/
return points match the original source. Source schema and all actual foreign-key
constraints are unchanged.

The remaining 15 mixed methods below are unfinished work, not permanent
exceptions. Command receipt digest/response helpers remain explicitly included
for the later command batch while atomic guards and canonical writing stay root.
`capture` fact grouping must still be reviewed as fixed projection versus
application assembly; no generic query port or new framework is justified.
See the [read/cursor/export boundary](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#read-cursor-and-export-boundary).

| Method / source lines | Classification | Remaining application or retained responsibility |
| --- | --- | --- |
| [`__init__`](analysis_store.py#L18) (18–20) | fixed | Owner/workspace binding only; retain adapter binding. |
| [`_workspace`](analysis_store.py#L22) (22–23) | composition | Delegates existing application logic to analysis/read_work.require_workspace through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`manifest`](analysis_store.py#L25) (25–34) | mixed | Workspace admission, model serialization/digest and response envelope; retain immutable input insert/read guard root. |
| [`page`](analysis_store.py#L36) (36–50) | mixed | Page serialization/reference/game-count admission and response; retain locked input lookup and immutable page insert/read guard root. |
| [`read_transaction`](analysis_store.py#L53) (53–55) | composition | Lends unchanged owner read_only=True context to fixed AnalysisReads; no independent connection/commit. |
| [`inputs`](analysis_store.py#L57) (57–58) | composition | Delegates existing application logic to analysis/read_work.read_inputs through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`_input`](analysis_store.py#L60) (60–61) | composition | Delegates existing application logic to analysis/read_work.read_input through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`finalize`](analysis_store.py#L63) (63–83) | mixed | Action/completeness/game scope decisions, command/result flow; retain page-admission lock, receipt and writes root. |
| [`_head`](analysis_store.py#L85) (85–89) | fixed | Fixed optional-lock run/head lookup and not-found guard. |
| [`submit`](analysis_store.py#L91) (91–131) | mixed | Input/evidence/ranking admission, positions/cap, generation/budget work and three-phase staging flow; retain guards and 64-position write boundaries root. |
| [`_status`](analysis_store.py#L133) (133–134) | composition | Delegates existing application logic to analysis/read_work.run_status through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`status`](analysis_store.py#L136) (136–138) | composition | Existing read-only head/status composition; wire relocated projection at the same point. |
| [`runs`](analysis_store.py#L140) (140–141) | composition | Delegates existing application logic to analysis/read_work.read_runs through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`games`](analysis_store.py#L143) (143–145) | composition | Already delegates existing GamesReader; late import and owner stay fixed. |
| [`_receipt`](analysis_store.py#L147) (147–152) | mixed | Replay response/digest projection around command advisory lock, digest binding and guarded receipt read; retain atomic guard root. |
| [`_save_receipt`](analysis_store.py#L154) (154–156) | mixed | Response request_digest mutation at writer call point plus canonical receipt write; retain one root writer. |
| [`receipt`](analysis_store.py#L158) (158–159) | composition | Delegates existing application logic to analysis/read_work.read_receipt through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`command`](analysis_store.py#L161) (161–194) | mixed | Continue/retry/resume/cancel dispatch, legacy/cancellation decisions and response/receipt; preserve each root atomic guard and ordered write. |
| [`capture`](analysis_store.py#L196) (196–204) | mixed | Captured result/dismissal fact assembly plus subsequent input snapshot composition; review fixed read-projection pieces rather than inventing abstractions. |
| [`_derive_evidence`](analysis_store.py#L206) (206–210) | composition | Existing classify -> late findings import -> accepted evidence read -> merge wiring; pure merge already moved. |
| [`derive`](analysis_store.py#L212) (212–278) | mixed | Legacy/current finding assembly, dismissal/reopen policy, validation, metadata/completeness/state, 64-item staging and before_publish callback; retain publication/revision/dismissal guards root. |
| [`report`](analysis_store.py#L280) (280–282) | composition | Delegates existing application logic to analysis/read_work.read_report through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`finding`](analysis_store.py#L284) (284–285) | composition | Delegates existing application logic to analysis/read_work.read_finding through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`dismiss`](analysis_store.py#L287) (287–307) | mixed | Two-phase receipt/finding/locked command workflow, dismissal choice and response; retain shared-user-state lock and revision/publication guards root. |
| [`export`](analysis_store.py#L309) (309–310) | composition | Delegates existing application logic to analysis/read_work.export_finding through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`claim`](analysis_store.py#L312) (312–353) | mixed | Cancellation/recovery/legacy decisions, depth/worker compatibility, pure job callback and wire/input validation, lease response; retain global capacity lock and lease writes root. |
| [`accept`](analysis_store.py#L355) (355–383) | mixed | Result validation/digest, root-versus-played stage decisions/pair comparison and response; retain replay/lease/execution guards and ordered result/position/run writes root. |
| [`failure`](analysis_store.py#L385) (385–410) | mixed | Failure-code/status event projection, attempts/cancel/retry/backoff decisions; retain fencing/event insertion/position/run writes root. |
| [`remaining`](analysis_store.py#L412) (412–413) | composition | Delegates existing application logic to analysis/read_work.read_remaining through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`derive_next`](analysis_store.py#L415) (415–418) | mixed | Fixed eligible-run read plus conditional application derivation dispatch. |
| [`mark_engine_unavailable`](analysis_store.py#L420) (420–427) | mixed | Legacy admission and conditional requested-generation effect; retain fixed writes root. |
| [`engine_result`](analysis_store.py#L429) (429–430) | composition | Delegates existing application logic to analysis/read_work.read_engine_result through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |
| [`evidence_records`](analysis_store.py#L432) (432–438) | fixed | Empty-input short circuit, complete ordered owner evidence read and identity-set completeness guard; retain as fixed owner projection. |
| [`input_status`](analysis_store.py#L440) (440–441) | composition | Delegates existing application logic to analysis/read_work.read_input_status through fixed owner reads/lazy workspace or capability lookup; original context/guard order preserved. |

The remaining mechanical batches are input/page admission/finalization; run
admission/commands/dismissal and receipt response assembly; capture/report
derivation/publication; engine claim/result/failure. This slice performs no
Analysis write, engine/provider, live state, migration or schema operation.
