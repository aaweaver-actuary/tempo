# Learning runtime integration surface

## Content bridge v2

`POST /internal/publications/v2/stage` accepts the service-local version-2 opening
bundle (maximum 8,192 definitions / 12 MiB). `publication-schema` exports its schema.
It retains shared Publication v1, real route occurrences, source digest, and
optional one-to-one legacy aliases. Content IDs remain authored identifiers;
validated aliases retain existing physical Learning/history identities. Adoption
requires exact legacy FEN, primary line, revision, family and learner color;
unknown historical alternative sets cannot be invented. No history importer is
provided.

`POST /internal/publications/v2/activate` uses the existing durable command gateway
with `{publication_id, publication_generation, expected_activation_revision,
activation_id}`. Initial expected activation revision is zero. Successful v2
responses are actual shared v1 ActivationAcknowledgement values. Stage acceptance
alone creates no serving cards. Replacement validates unchanged executable
revisions against their original accepted generation and preserves schedules;
changed executables require a newer revision. Route occurrences own prerequisites.

Operator-only receipt read/retry routes are
`/internal/publications/operations/{id}` and `.../{id}/retry`.
`GET /internal/publications/{id}/status` separately returns current generation,
activation revision/state, bundle digest, workspace-local date and observation
time. A historical activation acknowledgement is not current serving authority.
Study operation routes reject publication receipt/retry access. Existing v1
publication and retirement endpoints remain available; all publication routes use
the separate operator credential. No Study or evidence credential is upgraded.

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

The evidence endpoint retains the background writer pool, short lock timeout and nonblocking module lock, with a 1,000 ms atomic batch transaction bound. Ordinary maintenance remains 250 ms. A transaction timeout maps to retryable HTTP 503 and rolls back acceptance/receipt/work effects. See Study integration and remaining transition work in [F2 handoff](../../../docs/study/f2-remediation-handoff.md).

## Recovery guarantees for PR #287

Remediation uses the existing task payload `{queue_date, after_card,
rescan_requested}`. Missing markers mean false. Same-day unfinished requests set
only the marker and retain cursor, generation, lease, retry accounting and ready
ordering. A completed sweep or newer day starts a fresh generation; delayed older
days do not replace newer work. Generic daily-queue replacement is unchanged.

After each one-card application slice, finalization locks the current task late
and verifies ID/generation/token/leased state and actual-time expiry. More work
advances the cursor and retains the durable marker. At end, false completes;
true atomically consumes the marker and queues an empty-cursor follow-up pass.
Failed-owner release preserves the marker. Review refresh requests flush within
the original transaction after settlement and prepared issuance: daily task,
remediation task, then projection. No task lock precedes subsequent card/queue/module
acquisition. Intake and maintenance retain card -> queue where needed -> module ->
late task ordering. Claims remain separate committed sections.

The adapter passes trusted historical evidence revisions to the repository through
`receive(..., historical_evidence_revision=None, v1_transport_stale=False)`; the
runtime enables v1 transport-stale compatibility. These arguments are never wire
fields. Delivery identity/digest and existing immutable receipts are checked under
the module lock before stale policy. Source-stale and compatibility receipts bind
the full canonical payload and replay unchanged with null Learning revision and
empty accepted obligations. They create no evidence/work/admission authority.
V2 historical semantic replay and historical exact transport replay remain valid;
unconverted historical same-frontier requests retain HTTP 409 for either wire.
Historical tables remain read-only; no obligation conversion is introduced.

## F2 Study connection and teaching

| Interface | Contract |
| --- | --- |
| `GET /api/study/bootstrap` | Authenticated `{schema_version:1, workspace_id, timezone, local_date, settings}`; no queue acquisition. |
| `GET /api/study/settings` | Only the six Learning-owned training settings. |
| `POST /api/study/settings` | Idempotency-Key required; strict nonempty partial patch, unsupported/null fields rejected before acceptance; confirmed persisted settings. |
| `GET /api/cards/{card_id}/teaching` | Historical `{states:[{cardId,revision,ply,taughtAt}]}`; active lifecycle is not required to read history. |
| `POST /api/cards/{card_id}/teaching` | Existing `{revision,ply}`; `teaching:{card}:{revision}:{ply}` identity, exact camel-case fact response. |
| `GET /api/cards/{card_id}/move-membership` | `revision`, `fen`, `move_uci`; accepted active repertoire routes only, `{card_id,revision,move_uci,verified}`. No scheduling or teaching effect. |
| `GET /api/tactics/progress` | Existing deck-keyed strict index/clean/cleanIds/discoveredIds from persisted Learning tactic facts. |

The settings names are `study_only_mode`, `new_cards_per_day`,
`study_new_per_day`, `tactics_new_per_day`,
`include_defensive_cards_in_daily_stack`, and `light_first_interval_days`.
Tactics maps to the retained singular database column; timezone is configured and
read-only. Strict integer bounds are 0–100, or 1–90 for the light interval.
Provider, account, analysis and coverage fields are forbidden.

Teaching registers `cards.teaching.record` on the existing gateway. It borrows
that transaction/savepoint, shares the card edit lock with retirement, validates
the current accepted executable opening revision/lifecycle and learner-turn ply,
and inserts a single fact under the existing uniqueness constraint. Repeated
logical delivery preserves the first timestamp. Completed receipts replay after
lifecycle changes; a new command against retired/deleted work conflicts. Teaching
has no review, scheduling, issuance or remediation settlement effects. No migration
or mixed legacy Settings schema is introduced.

The Study client supplies bearer authentication and `X-Tempo-Workspace` only at
delivery to its configured Learning origin, rejects redirects and removes legacy
work-class headers. Browser Origin is browser generated. Credentials stay in
session storage, outside command envelopes, bundles and evidence. The existing
Host/Origin/CORS allowlists and restricted database roles remain authoritative.

## Publication receipt request binding

The operator-only `GET /internal/publications/operations/{operation_id}` adds `workspace_id`, `command_name` and the original persisted `request` for known publication operations. These let Content independently verify terminal failure against its immutable delivery checkpoint. Unknown operations remain `unknown`; Study/evidence credentials cannot read or retry publication operations. Generic Study receipt responses and shared publication wire contracts are unchanged.

Study move-membership resolves a physical historical card through its publication-scoped Content alias before reading current accepted definitions. Physical IDs, reviews, FSRS state and attempts stay with Learning; all existing executable-revision and serving-state fences still apply.

## Publication terminal fence

Operator-only `POST /internal/publications/operations/{operation_id}/abort` accepts
`{"workspace_id": WORKSPACE, "command_name": FAMILY, "request": ORIGINAL_REQUEST}`.
FAMILY is `publication.activate.v2` or `publication.retire`; ORIGINAL_REQUEST has
exactly publication_id, publication_generation, expected_activation_revision and
activation_id or retirement_id. Integers are strict, generation positive and
expected revision nonnegative. The request ID equals the path ID (1–128 characters).
Workspace must equal this installed Learning authority. Other families are denied.

The same operation advisory lock and receipt row lock as execution bind the
original command/request hash. An absent identity is inserted; unknown/queued
operations become immutable failed receipts with code
`publication_abandoned_before_execution`. Existing complete or failed receipts are
returned unchanged. Conflicting requests fail. A successful abort response may
therefore report genuine completion; consumers must reconcile that outcome.
The authenticated GET port exposes the exact original request and terminal receipt.
No independent cancellation table or new migration is used.

V2 activation rediscovers matching terminal receipts before requiring staging.
Activation and retirement reject differing header/body operation identities.
Gateway execution and maintenance re-read terminal state under the same locks,
including workers selected before fencing. Staging alone never grants serving
authority. Fencing neither deletes immutable staged bundles nor retires old serving
content; corrected sources use the ordinary new generation/revision workflow.

C3 retains the existing 2.5-second foreground transaction limit. Activation now
indexes integrity by physical card before filtering memberships, replacing a
quadratic scan. The complete-publication native regression includes 7,258
definitions, with replacement/replay/history assertions across three generations.
The expanded validated bundle and HTTP body both retain a hard 12 MiB bound;
rejection does not permit an operator to reuse a terminal failed command.
Replacement revision checks likewise index physical-card revisions, rather than
searching the full replacement list once per accepted historical card.
