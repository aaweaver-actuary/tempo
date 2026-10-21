# Content database

**Current source schema; live deployment/schema unverified.** This owner directory groups existing persistence files within the single repository-root database tree. It creates no shared database runtime and changes no SQL, transaction policy or ownership. SQL namespace: `content`.

| Directory | Contents |
| --- | --- |
| [adapters](adapters/README.md) | Concrete persistence implementations and read projections. |
| [ddl](ddl/README.md) | Schema metadata, ordered migrations and explicit owner migration entry points. |
| [schema](schema/README.md) | Rendered Mermaid relationships and complete field/type dictionaries. |
| [tests](tests/README.md) | Content-owned native persistence contracts, included in the sdist only. |
| [tooling](tooling/README.md) | Existing disposable PostgreSQL runner and representative benchmark, included in the sdist only. |

## Logical relationships (not SQL foreign keys)

Publication/bridge payload identities refer to Learning command receipts over the public protocol. They do not grant SQL access to Learning tables and have no cross-database FK.

The dictionaries distinguish nullable fields, primary/composite keys, enforced FKs and uniqueness. Defaults/checks/triggers/partial or expression indexes remain authoritative in the linked DDL; they are not restated as extra relationships.
