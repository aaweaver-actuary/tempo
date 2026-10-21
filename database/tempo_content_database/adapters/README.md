# Content adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [store.py](store.py) | Content-only PostgreSQL authority; computation happens between short sections. Existing [source policy](../../../services/content/src/tempo_content/policy/README.md), [worker phases](../../../services/content/src/tempo_content/worker/README.md) and [publication workflows](../../../services/content/src/tempo_content/publication/README.md) live in Content application folders; this adapter supplies their facts/operations. Route, exercise, binding and publication reads/atomic sections are concrete root storage methods. Source staging/admission sequencing remains extraction work. |
| [bridge.py](bridge.py) | Existing bridge SQL, JSON driver encoding, source-scope locks and row projections; mutations borrow the original transaction. [Application/composition](../../../services/content/src/tempo_content/bridge/README.md). |

<a id="store-responsibility-audit-after-exercise-phase-extraction"></a>

## Store responsibility audit

This current source audit accounts for all 53 `ContentService` methods and four
top-level helpers. It does not inspect live data/schema or claim the whole store
is application-free. The remaining source workflow is future extraction
work rather than permanent exceptions.

| Group | Methods | Responsibility and remaining work |
| --- | ---: | --- |
| Pool/context/query lifecycle | 8 | Initialization/close/context entry/exit, `_transaction`, `_read_snapshot`, `_query`, `_one`; connection, isolation, timeout and transaction-metric ownership. |
| Source/record/integrity reads | 8 | `_source`, `_lock_source`, `_manifest`, `_item`, `_snapshot`, `_exercise_revisions`, `_publication_task`, `_publication_payload`; complete queries, locks, immutable hydration/count/digest integrity and bounded version reads. The identical payload SELECT serves its two original invocation sites. |
| Consistent read projections | 14 | `source_show`, `repertoire_show`, `request_inventory`, `inventory_status`, `_inventory_status`, `publication_show`, `publication_status`, paging helpers, `positions`, `replies`, `routes`, `coverage`; query disambiguation, source/generation/fingerprint consistency, snapshot pagination and row envelopes. Pure status/coverage labels live outside the database tree. |
| Native storage authority | 15 | Claim/fence/advance/failure/transition; route cache/register/commit/attach/occurrence writes; exercise commit; binding target/commit; publication build-time reservation/immutable insert. These retain exact native guards, batching, identities, locks and singular transaction authority. |
| Existing composition entry points | 6 | `work_once`, `_route_slice`, `_exercise_slice`, `_binding_slice`, `publication_build`, `store_publication`; wire fixed owner operations into application functions. |
| Storage collector | 1 | `collect_once`; bounded orphan/reference rechecks, artifact/collector locks and storage-lifecycle pending/idle bookkeeping. |
| Remaining source workflow | 1 | `load_source`; preflight, staged pages, manifest seal, hook and reserved admission sequencing still mix application and storage. Next split must retain those original native sections and their order. |

Top-level `_json`/`_digest` encode and verify stored/cache/cursor identities;
`_decode_inventory_cursor` owns versioned reader-cursor integrity;
`_hook` is unchanged callback instrumentation supplied by composition. Full
method-by-method source locations/reasons are retained with this slice's
validation evidence. Consistent read projections are explicitly distinguished
from independent application/domain algorithms; this classification does not
authorize redesign of queries or acceptance of remaining mixed workflows.
