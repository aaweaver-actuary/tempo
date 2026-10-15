# Learning runtime integration surface

Artifact: `tempo-learning-runtime` 0.1.0; workspace: one configured identity per database.
All API routes except health require a workspace-scoped bearer credential. Study routes use the study token; `/internal/publications/*` requires the separate operator token and `/internal/evidence` requires the separate evidence token. Missing integration tokens disable those endpoints.
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

Default evidence ordering uses `(workspace_id, evidence_id)`. Transport identity
is checked first, so a completed delivery always replays its original result.
Semantic identity is sorted-key canonical JSON of every delivery field except
`delivery_id`. A new ID with identical current revision/content returns a completed
acknowledgement with that ID and the previously accepted obligations, without
repeating effects. Conflicting current content returns HTTP 409. Older revisions
return `status=stale_revision`, a reason, empty accepted obligations and null
`learning_revision`; their transport acknowledgement is persisted. New revisions
(even zero-obligation corrections) and all effects commit together. Newer
acceptance does not retract older obligations; richer correction belongs to B/F.

Migration registration is an ordered JSON array under the installed package's
`migrations/manifest.json`: `{id, owner, path, depends_on}`. Paths are package-local
SQL files. Existing entries are append-only and checked against installed checksums.
Dependencies must precede a migration. B supplies a namespaced id and SQL; F adds
its dependency registration after runtime and remediation land. Legacy migration
numbers do not carry across database boundaries.

`learning.runtime/0002` adds default receiver revision authority after
`learning.runtime/0001`. Existing completed receipts establish their maximum
revision with a null semantic digest because the original semantic payload is
unavailable. Exact historical transport retries remain valid; unverifiable new
IDs at that revision return HTTP 409. A newer revision establishes a digest.
Reapply `grant_runtime` after migration so restricted roles can use the new table.

Authoring, live import/cutover, worker credentials and producer scheduling remain
outside the Learning API. Endgame cache misses may use the configured online
provider; missing providers and uncovered defense analysis are explicit failures
or durable needs-analysis states, never successful grades.


The checked file adapter accepts the same JSON as the publication HTTP commands:
`tempo-learning stage --file bundle.json`, `activate --file activation.json`, and
`retire --file retirement.json`. The latter two require immutable command IDs and
expected activation revisions. Each opening repertoire has one active publication
owner. A publication contains at most 128 definitions; newer executable revisions
retain family, Study identity and source identity. Frozen reader version and exact
content/membership fingerprints are rechecked on activation. No ordering scores
are recalculated by Learning.

Execution memberships must be unique (maximum 100 per fact). Opening route
positions `(repertoire_id, route_order)` must be unique in a bundle; omitted route
orders retain default zero and are rejected if they collide. Every accepted
opening line ends on a learner move. Activation verifies complete graph identities
and payloads, including multi-membership routes. Previously staged malformed
bundles terminate with a failed operation receipt instead of retrying forever.

Activation and retirement acquire publication identity locks, then sorted opening
repertoire ownership locks (old and new memberships), then sorted card edit/row
locks, then existing queue effects. The owner check runs after the repertoire
lock and observes a committed competing activation. A conflicting owner returns
HTTP 409; database contention remains retryable. Publications are complete
generations, not append-style chunks: multiple active publications cannot be used
to cumulatively import an opening repertoire larger than 128 definitions.

Tablebase cache identity is `tb-fen-v2:<complete normalized FEN>`. It includes the
halfmove clock and never reads old four-field keys. Offline cached answers and
explicit HTTP 503 for an unavailable uncached provider retain their existing API.

`fixtures/legacy-protocol.json` publishes authentication, queue, review, transport,
conflict and unavailable envelopes. The existing strict client schemas and review
outbox consume these HTTP shapes in the native Chromium suite. Native tests also
cover all family-specific requests/responses against their existing Pydantic
models. Successful migration/install/startup is not wire-v1 or product acceptance.
