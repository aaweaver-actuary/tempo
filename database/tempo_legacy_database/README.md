# Legacy backend database

**Current source schema; live deployment/schema unverified.** This owner directory groups existing persistence files within the single repository-root database tree. It creates no shared database runtime and changes no SQL, transaction policy or ownership. SQL namespace: `public`.

| Directory | Contents |
| --- | --- |
| [adapters](adapters/README.md) | Concrete persistence implementations and read projections. |
| [ddl](ddl/README.md) | Schema metadata, ordered migrations and explicit owner migration entry points. |
| [schema](schema/README.md) | Rendered Mermaid relationships and complete field/type dictionaries. |
| [connections.py](connections.py) | Existing component-owned connection/transaction routing. |

The original mixed-source checklist is closed: complete command/service SQL,builders,cursors,driver details and DDL now live in this tree. The [inventory](../INVENTORY.md) separately tracks native tooling/test placement and the subsequent application responsibility/composition work; file closure does not establish clean domain or resource independence. Frozen-order storage now lives here, with [pure Study decisions and legacy composition](../../backend/app/study/frozen_order/README.md) outside this tree. `connections.py` also retains SQLite compatibility startup DDL; [frozen-order SQLite fields and constraints](ddl/frozen-opening/README.md) are documented separately. The principal schema charts describe authoritative PostgreSQL migrations through 050, not a claim that SQLite has identical constraints/types. The legacy database co-locates Games and Study tables; this is existing coupling, not acceptance of a new cross-component dependency.

## Logical relationships (not SQL foreign keys)

Fields such as `source_ref`, publication generation, JSON payload identities and many `card_id`/game references are application facts unless an FK is explicitly listed in the schema dictionary. Legacy remediation identifies `remediation_cards.card_id` against the accepted owner’s card identity without a cross-store FK.

The dictionaries distinguish nullable fields, primary/composite keys, enforced FKs and uniqueness. Defaults/checks/triggers/partial or expression indexes remain authoritative in the linked DDL; they are not restated as extra relationships.

[Native bridge proofs](tests/legacy_bridge/README.md),[Discovery acceptance proofs](tests/discovery_acceptance/README.md),[queue admission proofs](tests/queue_admission/README.md) and their [finite qualification/resource tooling](tooling/legacy_bridge/README.md) now live under this owner. Source paths and discovery change; native execution remains unverified and is not triggered by navigation or ordinary application tests.
