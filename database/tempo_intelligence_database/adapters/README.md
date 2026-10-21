# Intelligence adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [analysis_store.py](analysis_store.py) | Intelligence-owned durable analysis. Network/engine work never holds a transaction. |
| [games_read_projection.py](games_read_projection.py) | Existing bounded Games-view reads over Intelligence-owned run/head/input/page tables; [reader policy and transaction composition](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#games-reader-storage-boundary). |
| [store.py](store.py) | Intelligence owns every transaction here; no provider executes inside this boundary. |

The existing finding merge/ranking block now lives in the component's
[`analysis/findings.py`](../../../services/intelligence/src/tempo_intelligence/analysis/findings.py),
with [composition and focused proof](../../../services/intelligence/src/tempo_intelligence/analysis/README.md#finding-assembly).
`AnalysisStore._derive_evidence` still classifies input, reads accepted evidence
and then merges the captured facts in that order. SQL and transaction ownership
remain here.

This is a bounded extraction, not a complete removal of application policy from
these two older adapters. Remaining mixed methods include `Store.submit`,
`inspect`, `_command`, credential/provider state transitions and Maia admission;
`AnalysisStore` still mixes manifest/page/finalize/run admission, status and command
responses, report publication/pagination/dismissal/export, and engine execution
state transitions with their queries. Response serialization and retry policy
now live in the component's
[`serialization.py`](../../../services/intelligence/src/tempo_intelligence/serialization.py)
and [`source_requests.py`](../../../services/intelligence/src/tempo_intelligence/source_requests.py).
The private `_retry_eligible` entry point delegates to the latter, which also
owns descriptor preparation and cache-identity policy; the Store
keeps the existing binding/sort/lock order and every transaction/query call point.
See the [request/cache boundary](../../../services/intelligence/README.md#request-and-cache-policy-boundary).
The Games read projection is already limited to queries and row extraction. DDL
discovery and migration execution remain database operations in `../ddl/`.
