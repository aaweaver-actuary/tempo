# Data ownership

Study owns accepted attempts, reviews, schedules and queue issuance. Game Review supplies evidence through the existing contracts. The installed backend stores both in the [serving PostgreSQL database](../database/README.md#installed-database).

The [game review contracts](../packages/game-review-contracts/README.md) define game and repertoire inputs, comparison reports and injectable protocols. The [disposable fixtures](../database/ownership/README.md) exercise PostgreSQL backup comparison and remediation.

## Retained-history transfer values

The [Learning transfer contracts](learning-transfer/README.md) hold pure versioned
identity, capture, receipt, fence and recovery records shared by runtime and
database owners. This package owns no database or lifecycle and imports neither
owner's implementation. Original source authority is retained in every fact; a
wire evidence reference is not a verified migration or operational authorization.
