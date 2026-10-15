# Explicit Learning obligation correction protocol

Artifact 2.0.0 exports `tempo_game_evidence_contracts.v1` and `.v2`. V1 schemas,
producer output and additive semantics remain unchanged. V2 consumers must select
version 2 explicitly; no payload negotiation or silent upgrade is supported.
The existing TypeScript exchange remains v1; no v2 TypeScript adoption is claimed.

V2 requires `occurred_at` (timezone-aware instant or explicit null), and each
obligation names its `encounter_id`, `miss_id` (null for a non-miss observation),
`publication_id`, `card_revision` and `obligation_revision`. Logical obligation
identity is independent of `delivery_id`. `(obligation_id, obligation_revision)`
and `(evidence_id,evidence_revision)` payloads are immutable. Keep an evidence ID
bound to one game; a meaningful source revision updates the corresponding
obligation revisions or explicitly withdraws them.

`withdrawals` targets exact current obligation revisions and records correction,
exclusion, deletion or reanalysis. A withdrawal affects only that source's
contribution, never another game's evidence or any study history. Empty encounter
lists are supported only for explicit withdrawals. A partial batch is additive;
an omitted obligation never implies withdrawal. Split larger work into distinct
bounded evidence publications; each list is capped at 128 items.

The Learning receiver must additionally verify authenticated workspace/source
authority and accepted active definitions, including the complete accepted move
alternatives at the decision position. Schema validity alone does not prove a
supported miss. Existing v1 deliveries without unambiguous linkage must be
rejected explicitly; v1 occurrence time is unknown, never inferred from import.

`completed` acknowledges durable obligation acceptance in Learning's transaction.
It does not mean the mistake was remediated, that an admission applied, or that
the queue has unlimited space. Admission deferrals and study settlement are
separate Learning states. Only an unaided correct controlled review can settle
the bound obligation frontier. Later imports need a fresh attempt; unknown-time
facts require durable acceptance before authoritative attempt issuance.

Producer fixtures: `fixtures/v2/delivery.json`, `delivery-unknown-time.json`, and
`withdrawal.json`. A/F integration interface: `services/learning/remediation/README.md`.
These are installable contract/library artifacts, not production activation.
