# Explicit backend contracts

**Current unmerged source; deployment unverified.** These modules define request/fact/interface types with their existing owners and semantics. They contain no sibling domain implementation or runtime composition. This is an explicit contract directory, not a wildcard public barrel or a new architecture framework; the package initializer performs no registration or eager imports.

| Module | Existing contract owner / purpose |
| --- | --- |
| [transport](transport/README.md) | All 62 former `app.models` classes in 17 owner groups; exact validation, defaults, aliases and schemas. |
| [next_opponent.py](next_opponent.py) | Opponent profile immutable values. |
| [opening_evidence.py](opening_evidence.py) | Opening manifests, decision events and attempt checkpoints. |
| [opening_move_probability.py](opening_move_probability.py) | Existing probability evidence, fusion and summaries over supplied facts. |
| [prefix_diagnostics.py](prefix_diagnostics.py) | Prefix diagnostic response values. |
| [prefix_evaluation.py](prefix_evaluation.py) | Prefix evaluation requests and structural comparison results. |
| [prefix_transition.py](prefix_transition.py) | Frozen transition requests/plans/results. |
| [studies.py](studies.py) | Authored Studies request envelopes and identical installed Learning executable aliases. |
| [review_conflicts.py](review_conflicts.py) | Existing HTTP-aware review conflict exception; retains its FastAPI dependency/status/payload. |
| [defense.py](defense.py) | The existing canonical Learning defense aliases, unchanged by identity. |
| [opening_graph.py](opening_graph.py) | One frozen 13-field GraphStep fact declaration; old workflow uses this same class. |
| [runtime_timing.py](runtime_timing.py) | One existing response-timing ContextVar/getter, shared by application measurement and PostgreSQL instrumentation. |

Some established contract modules contain their existing pure validation/calculation helpers; this move does not consolidate or redesign them. Contracts are not all framework-free: request models use Pydantic and ReviewConflict is the existing HTTP exception. The probability contract uses the unchanged neutral position-identity function, whose `chess.Board(...).fen()` normalization differs from other similarly named helpers. None is substituted.

The `organized-contracts` local import-linter contract forbids imports of sibling implementation, composition and the compatibility `models.py` facade; only explicit contracts and neutral chess support are allowed within `app`. External packages retain their declared ownership. Consumers import the named module/symbol they need. Models compatibility exports are the same canonical objects, not duplicate implementations. Cross-domain implementations are connected only by outer composition.

The timing extraction preserves one ContextVar/getter object, nesting/reset/error semantics and PostgreSQL instrumentation. It does not move admission policy, create a registry, change writer-thread propagation or alter default gates. Those remaining technical seams stay in the [database/backend checklist](../../../database/INVENTORY.md#additional-storage-candidates-found-after-the-baseline).

See [the backend progress and validation limits](../../ORGANIZATION.md) and the one [proposal-only simplification list](../../../database/SIMPLIFICATION.md). No runtime-schema/data change or deployment is implied.
