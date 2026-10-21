# Game Evidence database

**Current source schema; live deployment/schema unverified.** This owner directory groups existing persistence files within the single repository-root database tree. It creates no shared database runtime and changes no SQL, transaction policy or ownership. SQL namespace: `public`.

| Directory | Contents |
| --- | --- |
| [adapters](adapters/README.md) | Concrete persistence implementations and read projections. |
| [ddl](ddl/README.md) | Schema metadata, ordered migrations and explicit owner migration entry points. |
| [schema](schema/README.md) | Rendered Mermaid relationships and complete field/type dictionaries. |

## Logical relationships (not SQL foreign keys)

`workspace_inputs.snapshot_digest` and snapshot documents describe accepted comparison authority; delivery JSON carries Learning obligation/card identities. Those JSON/identity links are logical unless an explicit local FK appears below. Outbox delivery crosses the Learning protocol, not a cross-database FK.

The dictionaries distinguish nullable fields, primary/composite keys, enforced FKs and uniqueness. Defaults/checks/triggers/partial or expression indexes remain authoritative in the linked DDL; they are not restated as extra relationships.
