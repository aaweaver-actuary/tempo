# Intelligence adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [analysis_store.py](analysis_store.py) | Intelligence-owned durable analysis. Network/engine work never holds a transaction. |
| [games_read_projection.py](games_read_projection.py) | Existing bounded Games-view reads over Intelligence-owned run/head/input/page tables; [reader policy and transaction composition](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#games-reader-storage-boundary). |
| [request_inspection.py](request_inspection.py) | Fixed request/cache/evidence/credential/provider-control reads on the owner's initial read-only snapshot. No transaction creation or application response policy. |
| [request_commands.py](request_commands.py) | Fixed refresh/retry command writes and atomic lock/replay/revision guards on the caller's existing write transaction. |
| [maia_operations.py](maia_operations.py) | Fixed profile/job/evidence reads and writes, job/cache locks and atomic replay/stale/transition guards on the owner's existing transaction. |
| [request_admission.py](request_admission.py) | Fixed request/snapshot/cache admission operations and request/snapshot replay guards on the caller's write transaction. |
| [provider_operations.py](provider_operations.py) | Fixed source selection, lease/fence, evidence publication and registration-result writes on the owner's background transaction. |
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
credential workflows still mix decisions with SQL;
`AnalysisStore` still mixes manifest/page/finalize/run admission, status and command
responses, report publication/pagination/dismissal/export, and engine execution
state transitions with their queries. Response serialization and retry policy
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

Remaining Store mixed application blocks are specifically `_credential_status`,
`credential_status`, `credential_reload` and `credential_revoke`. Request admission,
inspection, refresh/retry, Maia work, evidence response and source claim/publication
orchestration are no longer mixed blocks. Database startup validation,
workspace binding checks, transaction settings, lease/replay atomic guards and
fixed writers remain database responsibilities; this inventory does not propose
relocating those guards or changing their authority.

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

## Complete Store responsibility inventory

This source audit accounts for all 36 current Store methods (the original 32 plus
four transaction composition contexts). Mixed rows remain unfinished extraction
work, not permanent exceptions or a completed-split claim.

| Classification | Methods / remaining responsibility |
| --- | --- |
| Database authority and fixed operations (11) | `__init__`, `read_only`, `transaction`, `validate_startup`, `close`, `health`, `_head`, `_new_generation`, `_fenced`, `_release_provider`, `_append`: connection settings, schema/grant/workspace checks, guarded reads and atomic writes. |
| Composition / fixed effects (21) | `inspection_transaction`, `inspect`, `_retry_eligible`, `command_transaction`, `_command`, `refresh`, `retry`, `fail`, `_profile`, `_maia_job`, `maia_read_transaction`, `maia_write_transaction`, `evidence`, `maia_jobs`, `accept_maia`, `maia_status`, `admission_transaction`, `submit`, `provider_transaction`, `claim`, `publish`: wire existing application decisions/workflows to owning transactions and fixed effects. `fail` retains its existing fenced, ordered effect application around the extracted failure decision. |
| Mixed credential workflows (4) | `_credential_status`, `credential_status`, `credential_reload`, `credential_revoke`: expiry/status projection, configured material/status handling and registration/revocation orchestration. |

The separate `AnalysisStore` inventory above is still outstanding. No security,
credential material, grant, lease/replay guard or transaction-authority behavior
change is proposed by this classification; this batch changes only request
admission and provider-source claim/publication code location and wiring.
