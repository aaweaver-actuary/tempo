# Learning remediation database

**Current source schema; live deployment/schema unverified.** This owner directory groups existing persistence files within the single repository-root database tree. It creates no shared database runtime and changes no SQL, transaction policy or ownership. SQL namespace: `learning`.

| Directory | Contents |
| --- | --- |
| [adapters](adapters/README.md) | Concrete persistence implementations and read projections. |
| [ddl](ddl/README.md) | Schema metadata, ordered migrations and explicit owner migration entry points. |
| [schema](schema/README.md) | Rendered Mermaid relationships and complete field/type dictionaries. |

This library borrows its caller’s connection and transaction. Its 12-table DDL is included by Learning runtime and separately by legacy backend migration 050; the schema is `learning` or `public` according to the including owner’s search path. No independent migration/commit authority is introduced.

## Logical relationships (not SQL foreign keys)

Card, game, incident and obligation identifiers identify accepted Learning-owned facts. In particular `remediation_cards.card_id` is not an FK to another component’s cards table. Only the composite FK constraints shown in the dictionary are enforced.

The dictionaries distinguish nullable fields, primary/composite keys, enforced FKs and uniqueness. Defaults/checks/triggers/partial or expression indexes remain authoritative in the linked DDL; they are not restated as extra relationships.
