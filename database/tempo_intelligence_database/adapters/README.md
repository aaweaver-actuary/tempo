# Intelligence adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [analysis_store.py](analysis_store.py) | Intelligence-owned durable analysis. Network/engine work never holds a transaction. |
| [games_read_projection.py](games_read_projection.py) | Existing bounded Games-view reads over Intelligence-owned run/head/input/page tables; [reader policy and transaction composition](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#games-reader-storage-boundary). |
| [request_inspection.py](request_inspection.py) | Fixed request/cache/evidence/credential/provider-control reads on the owner's initial read-only snapshot. No transaction creation or application response policy. |
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
capabilities. `_command` still orchestrates retry/refresh, and credential/
provider workflows and Maia generation/admission still mix decisions with SQL;
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
`_maia_job`, `_command`, `_credential_status` / `credential_status` / credential
reload/revoke, provider claim/publication decisions, `accept_maia` / `maia_status`,
and the public `evidence` envelope. The initial inspection workflow is no longer
one of those mixed blocks. Database startup validation, workspace binding checks,
transaction settings, lease/replay atomic guards and fixed writers remain
database responsibilities; this inventory does not propose relocating those
guards or changing their authority.
