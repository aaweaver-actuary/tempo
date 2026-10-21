# Intelligence adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [analysis_store.py](analysis_store.py) | Intelligence-owned durable analysis. Network/engine work never holds a transaction. |
| [games_read_projection.py](games_read_projection.py) | Existing bounded Games-view reads over Intelligence-owned run/head/input/page tables; [reader policy and transaction composition](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#games-reader-storage-boundary). |
| [request_inspection.py](request_inspection.py) | Fixed request/cache/evidence/credential/provider-control reads on the owner's initial read-only snapshot. No transaction creation or application response policy. |
| [request_commands.py](request_commands.py) | Fixed refresh/retry command writes and atomic lock/replay/revision guards on the caller's existing write transaction. |
| [store.py](store.py) | Intelligence owns every transaction here; no provider executes inside this boundary. |

The existing finding merge/ranking block now lives in the component's
[`analysis/findings.py`](../../../services/intelligence/src/tempo_intelligence/analysis/findings.py),
with [composition and focused proof](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#finding-assembly).
`AnalysisStore._derive_evidence` still classifies input, reads accepted evidence
and then merges the captured facts in that order. SQL and transaction ownership
remain here.

This is a bounded extraction, not a complete removal of application policy from
these two older adapters. Remaining `Store.submit` policy includes request and
snapshot replay validation, expired-cache refresh and Maia profile/job decisions.
`inspect` now only composes the complete application inspection workflow with
the fixed `RequestInspection` reader and existing retry/credential/clock
capabilities. `_command` now composes the complete application refresh/retry
workflow with fixed guarded writes; credential/provider workflows and Maia
generation/admission still mix decisions with SQL;
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

Remaining Store application blocks are specifically `_profile`, `submit`,
`_maia_job`, `_credential_status` / `credential_status` / credential
reload/revoke, provider claim/publication decisions, `accept_maia` / `maia_status`,
and the public `evidence` envelope. Initial inspection and refresh/retry command
orchestration are no longer mixed blocks. Database startup validation, workspace binding checks,
transaction settings, lease/replay atomic guards and fixed writers remain
database responsibilities; this inventory does not propose relocating those
guards or changing their authority.

`RequestCommands` retains six fixed SQL operations and the existing receipt and
request-revision guards. It delegates request-head lookup, generation changes and
Maia work creation to the existing Store operations on the same connection;
it never opens/commits a transaction. `Store.command_transaction` supplies the
unchanged write context. The application calls the command lock before constructing
the payload, reads/replays the guarded receipt before the head/cache work, locks
caches in the same order and writes the receipt last. The eligibility callback
contains the original private-method/clock expression, preserving lazy lookup and
evaluation order. There is no generic execute/query capability.

## Complete Store responsibility inventory

This source audit accounts for all 32 current Store methods. Mixed rows remain
unfinished extraction work, not permanent exceptions or a completed-split claim.

| Classification | Methods / remaining responsibility |
| --- | --- |
| Database authority and fixed operations | `__init__`, `read_only`, `transaction`, `validate_startup`, `close`, `health`, `_head`, `_new_generation`, `_fenced`, `_release_provider`, `_append`: connection settings, schema/grant/workspace checks, guarded reads and atomic writes. |
| Composition / fixed effects | `inspection_transaction`, `inspect`, `_retry_eligible`, `command_transaction`, `_command`, `refresh`, `retry`, `fail`: wire existing application decisions/workflows to owning transactions and fixed effects. |
| Mixed source admission / generation | `_profile`, `submit`, `_maia_job`: configured profile acceptance, request/snapshot replay, expired-cache refresh and Maia generation/immutable request construction. |
| Mixed evidence response | `evidence`: public response envelope over the guarded evidence lookup. |
| Mixed credential workflows | `_credential_status`, `credential_status`, `credential_reload`, `credential_revoke`: expiry/status projection, configured material/status handling and registration/revocation orchestration. |
| Mixed provider workflows | `claim`, `publish`: credential/cooldown admission, source claim/publication and retrieval metadata decisions around the fixed lease/write operations. |
| Mixed Maia workflows | `maia_jobs`, `accept_maia`, `maia_status`: job-list response, result validation/evidence construction and status transitions around existing atomic guards/writes. |

The separate `AnalysisStore` inventory above is still outstanding. No security,
credential, grant, lease/replay guard or transaction-authority change is proposed
by this classification; this batch changes only command code location/wiring.
