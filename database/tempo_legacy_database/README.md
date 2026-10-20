# Legacy backend database

**Current source schema; live deployment/schema unverified.** This owner directory groups existing persistence files within the single repository-root database tree. It creates no shared database runtime and changes no SQL, transaction policy or ownership. SQL namespace: `public`.

| Directory | Contents |
| --- | --- |
| [adapters](adapters/README.md) | Concrete persistence implementations and read projections. |
| [ddl](ddl/README.md) | Schema metadata, ordered migrations and explicit owner migration entry points. |
| [schema](schema/README.md) | Rendered Mermaid relationships and complete field/type dictionaries. |
| [connections.py](connections.py) | Existing component-owned connection/transaction routing. |

The legacy backend still contains SQL in command/service modules outside this directory; moving or extracting that application logic would change architecture and is outside this mechanical move. `connections.py` also retains the SQLite compatibility startup DDL; the charts describe the authoritative PostgreSQL migration sequence through 050, not a claim that SQLite has identical constraints/types. The legacy database co-locates Games and Study tables; this is existing coupling, not acceptance of a new cross-component dependency.

## Logical relationships (not SQL foreign keys)

Fields such as `source_ref`, publication generation, JSON payload identities and many `card_id`/game references are application facts unless an FK is explicitly listed in the schema dictionary. Legacy remediation identifies `remediation_cards.card_id` against the accepted owner’s card identity without a cross-store FK.

The dictionaries distinguish nullable fields, primary/composite keys, enforced FKs and uniqueness. Defaults/checks/triggers/partial or expression indexes remain authoritative in the linked DDL; they are not restated as extra relationships.
