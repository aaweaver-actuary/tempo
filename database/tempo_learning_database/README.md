# Learning runtime database

**Current source schema; live deployment/schema unverified.** This owner directory groups existing persistence files within the single repository-root database tree. It creates no shared database runtime and changes no SQL, transaction policy or ownership. SQL namespace: `learning`.

| Directory | Contents |
| --- | --- |
| [adapters](adapters/README.md) | Concrete persistence implementations and read projections. |
| [ddl](ddl/README.md) | Schema metadata, ordered migrations and explicit owner migration entry points. |
| [schema](schema/README.md) | Rendered Mermaid relationships and complete field/type dictionaries. |
| [connections.py](connections.py) | Existing component-owned connection/transaction routing. |

Learning alone owns queue/review/scheduling writes. Remediation DDL is supplied by its installed Learning-owned package and applied by this migration owner. There is no Games table access or Games serving prerequisite introduced here.

## Logical relationships (not SQL foreign keys)

Content publication IDs/generations and Game Evidence evidence/delivery identifiers are accepted protocol facts. They are not foreign keys into producer databases. Several local receipt/card fields deliberately have no FK so deletion/replay facts survive.

The dictionaries distinguish nullable fields, primary/composite keys, enforced FKs and uniqueness. Defaults/checks/triggers/partial or expression indexes remain authoritative in the linked DDL; they are not restated as extra relationships.
