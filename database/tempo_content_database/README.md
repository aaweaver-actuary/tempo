# Content database

**Current source schema; live deployment/schema unverified.** This owner directory groups existing persistence files within the single repository-root database tree. It creates no shared database runtime; the final-generation ledger and supported-writer fence are Content-owned. SQL namespace: `content`.

| Directory | Contents |
| --- | --- |
| [adapters](adapters/README.md) | Concrete persistence implementations and read projections. |
| [ddl](ddl/README.md) | Schema metadata, ordered migrations and explicit owner migration entry points. |
| [schema](schema/README.md) | Rendered Mermaid relationships and complete field/type dictionaries. |
| [tests](tests/README.md) | Content-owned native persistence contracts and separately discovered storage-only pure unit tests, included in the sdist only. |
| [tooling](tooling/README.md) | Existing disposable PostgreSQL runner and representative benchmark, included in the sdist only. |

## Logical relationships (not SQL foreign keys)

Publication/bridge payload identities refer to Learning command receipts over the public protocol. They do not grant SQL access to Learning tables and have no cross-database FK.

The dictionaries distinguish nullable fields, primary/composite keys, enforced FKs and uniqueness. Defaults/checks/triggers/partial or expression indexes remain authoritative in the linked DDL; they are not restated as extra relationships.

The [final-generation schema](schema/final-generations/README.md) records the source reservation, immutable capture and target disposition boundary. Its supported-writer proof does not establish installed administrator or direct-SQL bypass protection.
