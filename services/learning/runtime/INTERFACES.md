# Learning runtime integration surface

Artifact: `tempo-learning-runtime` 0.1.0; workspace: one configured identity per database.
All API routes except health require `Authorization: Bearer <workspace credential>`.
Browser requests also require an exact configured Origin. Existing request bodies,
Idempotency-Key values and durable payload normalization remain legacy protocol.

Supported families: card/opening review and reconciliation, required opening
checkpoints, five typed Study specifications (including self-assessment), tactical
discovery attempts, endgame attempts/probes and defense recognition/move attempts.
GET queue issuance authenticates and commits issuance before returning an attempt.
Projection generation is never an attempt token. Pending operation responses keep
202, operation_id, state and Location; errors retain detail and review conflict codes.

Remediation registration: `register_evidence_receiver(handler)` accepts a callable
`handler(transaction, delivery: EvidenceDelivery, admission) -> EvidenceAcknowledgement`.
`admission(transaction, card_id, queue_day, reason)` is the only admission writer.
`register_settlement(handler)` accepts `handler(transaction, card_id, review_id)`;
this required effect runs before the command transaction commits. Neither callable
may acquire a database connection or commit. Registration rejects replacements.
The default retained receiver/settlement operates on Learning-owned obligations;
F may install B's module in place of these defaults, never alongside a second writer.

Migration registration is an ordered JSON array under the installed package's
`migrations/manifest.json`: `{id, owner, path, depends_on}`. Paths are package-local
SQL files. Existing entries are append-only and checked against installed checksums.
Dependencies must precede a migration. B supplies a namespaced id and SQL; F adds
its dependency registration after runtime and remediation land. Legacy migration
numbers do not carry across database boundaries.

Authoring, live import/cutover, worker credentials and producer scheduling remain
outside the Learning API. Endgame cache misses may use the configured online
provider; missing providers and uncovered defense analysis are explicit failures
or durable needs-analysis states, never successful grades.
