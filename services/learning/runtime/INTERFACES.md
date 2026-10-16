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

`POST /internal/evidence` accepts the existing discriminated Game Evidence v1/v2 contracts. The default adapter uses `tempo-learning-remediation` on the same borrowed transaction; its per-query tuple cursors do not change the shared row factory. A workspace-bound evidence credential authorizes the producer's observed facts. Learning derives publication, decision position, complete accepted alternatives and immutable card revision from its own active definitions. There is no source database import or network lookup inside this boundary.

Accepted evidence, source/semantic revisions, acknowledgement and the durable `remediation` sweep request commit together. A completed acknowledgement does not promise admission or study completion. V2 retains explicit revision-targeted withdrawals and immutable historical semantic replay. V1 retains transport-first replay/stale behavior and HTTP 409 conflicts. Current v1 observations now require coherent linkage to accepted definitions; arbitrary fixture obligations are not authority. Historical receipts in the old transport table replay unchanged, but new delivery IDs at an unconverted historical revision conflict. Old obligation rows are preserved for explicit #224 conversion and are not written or broadly settled by the adopted runtime.

Registration compatibility remains `register_evidence_receiver(handler)` with `handler(transaction, delivery, admission) -> acknowledgement`, and `register_settlement(handler)` with `handler(transaction, card_id, review_id)`. Registration rejects replacements. These hooks replace their respective default authority. The default runtime settlement additionally receives internal queue-entry/revision context, loads the exact actual review, and resolves the frontier bound at first issuance. No hook owns a connection or commit.

The stable remediation attempt identity is `queue:<entry_id>:revision:<revision>`, scoped by the database workspace. Only first authoritative head issuance binds it; retries retain its issuance time/frontier, and pre-existing issued origins are never bound retrospectively. Prepared continuations use the same issuance command. Review submission cannot create authority. A missing/incompatible binding cannot claim settlement. Qualifying reviews are unaided correct valid study; failed/guided/Again/stale work cannot satisfy it. Settlement and all required review effects share the existing transaction/savepoint; independent practice does not manufacture queue authority.

The sole admission writer has a prelocked capability for the remediation module. Runtime card/queue locks precede the module lock; callbacks acquire no additional encompassing locks. The registered essential work kinds are `daily_queue` and `remediation`, with one persisted cursor/application slice per transaction. Claims release their task-row lock before application. Final lease/generation checks roll back superseded effects, and bounded contention releases only the current lease for retry. Ordinary relative order, protected positions and immutable frozen input validation remain owned by the existing queue code; the library supplies the miss overlay and existing eligibility exception.

Migration registration remains an append-only ordered JSON manifest `{id, owner, path, depends_on}`. The optional `package` field permits only `tempo_learning_runtime` (default) or the declared `tempo_learning_remediation` dependency. `learning.remediation/0001` loads its installed module migration after `learning.runtime/0002`. Existing SQL/checksums are unchanged. Owner-only migration and grant reapplication remain explicit; HTTP startup only checks readiness. No live backfill/cutover is provided.

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

The evidence endpoint retains the background writer pool, short lock timeout and nonblocking module lock, with a 1,000 ms atomic batch transaction bound. Ordinary maintenance remains 250 ms. A transaction timeout maps to retryable HTTP 503 and rolls back acceptance/receipt/work effects. See the exact remaining full-Study teaching/routing work in [F2 handoff](../../../docs/study/f2-remediation-handoff.md).
