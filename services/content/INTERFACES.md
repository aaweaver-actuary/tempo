# Content interfaces and ownership

## Authoritative bridge operator surface

`schema --model import-manifest|response-proposal` exports service-local schema 1
without database access. `proposal inspect FILE` is also pure. Import planning
persists an immutable draft; it cannot publish or activate. The approved workflow
and serialized consumer boundary are in
[A bridge](../../docs/analysis-restoration/a-bridge.md).

| Input field | Content mapping / authority |
| --- | --- |
| workspace/source/entity keys | Existing stable UUID identities; titles and input order do not identify cards |
| PGN SetUp/FEN and ordered paths | Exact authored starting FEN and legal UCI routes; every variation requires a manifest assignment |
| explicit repertoire/policy/color | Authored memberships and policy-scoped learner decisions; never inferred from titles |
| structured exercise/decision keys | Retained unchanged; PGN derives decision keys from explicit policy and exact decision FEN |
| accepted lines | Every authored learner alternative, preserved in order within each line |
| binding/re-entry manifest | Exact route/policy/ply/card binding; re-entry defaults false |
| source SHA/completeness/provenance | Retained original export evidence; incomplete scope cannot be approved for replacement |
| legacy_aliases | Exercise-key to historical Learning-card linkage, validated by Learning; missing history remains absent |

Draft IDs deduplicate semantic source permutations while retaining the first
original-byte provenance. Draft digest approval binds that exact retained record.
Source admission, approval and cancellation share a source-scope advisory lock.
Approved/applying operations reserve the whole source. Cancellation is effective
only before the committed `applying` transition; afterwards it returns
`application_started` and requires reconciliation. Runtime cannot modify drafts.

`operation record` is a local operator port requiring the original Learning origin
and private operator credential file. It independently reads the original
authenticated Learning operation receipt before recording matching bytes. Merely
asserting an `activated` status or transport label cannot confirm completion.
Delivery requests and snapshot observations are immutable Content checkpoints.
No operator credential is supplied to Game Evidence or serialized into documents.

`publication export ID --generation N` returns source/publication lineage, actual
routes/bindings, policy-scoped complete legal reply support, provenance and digest.
Coverage conditions replies on established route prefixes, preserving unknown
terminal scopes and distinct policies. Completed operation exports remain readable
after source/cache replacement. Shared Publication v1 itself is unchanged.

## Structured authored source v1

`tempo_content.SourceSnapshot` is the supported authored-input model. It rejects
unknown fields and inconsistent references. `fixtures/source.json` demonstrates
all supported executable kinds and both learner colors.

The top-level fields are `schema_version: 1`, `workspace_id`, `source_key`,
`title`, `repertoires`, `policies`, `routes`, `memberships`, `exercises`, and
`bindings`. Entity collections are unordered; executable route/accepted-line
moves remain ordered. Repeated entity or membership identities are invalid.

| Entity | Required identity and authored fields |
| --- | --- |
| Repertoire | `key`, `title`, `learner_color` (`white` or `black`) |
| Policy | `key`, `repertoire_key` |
| Route | `key`, exact `starting_fen`, ordered UCI `moves` |
| Membership | `repertoire_key`, `policy_key`, `route_key` |
| Exercise | `key`, `decision_key`, `policy_key`, exact `starting_fen`, `prompt`, shared v1 `specification`; optional `supersedes_key` |
| Binding | `route_key`, `policy_key`, zero-based `ply`, `exercise_key` |

A binding needs a matching route/policy/repertoire membership. The exercise's
policy must match that binding. Chess legality is separately validated by bounded
work, because contract shape validation alone does not prove legal play.

Stable public source, repertoire, policy, route, card, and decision IDs derive
from explicit workspace/source/entity keys. Renaming, input permutation,
publication rebuilding, or transposition does not create a new card. Policy and
decision identities remain distinct. Exercise revision tracks changed executable
content; `supersedes_key` makes an intentional replacement explicit. Publication
generation, source revision, and exercise revision are separate authorities.

The legal-reply inventory keys the validated legal chess state: pieces, turn,
castling rights, and legally available en-passant. It excludes move counters for
this cache only. Exact FEN remains part of authored/executable input. Every legal
opponent move is retained, including unauthored moves and all promotion identities;
terminal positions have no replies. Inventory contains no opponent probability,
learning maturity, review outcome, or admission decision.

## Operations and immutable publication

The CLI groups source loading/inspection, repertoire inspection, bounded inventory
request/work/status and paged position/reply/route/coverage reads, plus publication
build/show/status. Each result is JSON suitable for an independent caller. The
installed command's help is authoritative for arguments.

Sources are replaced through full snapshots. Their semantic canonical digest
makes replay idempotent. Inventory work uses durable source revisions, generations,
task leases and checkpoints; it reads bounded authoritative input, computes chess
facts without an open transaction, then commits a short fenced update and yields.
Traversal/accepted-line validation uses slices of at most 16 plies. Cleanup
collects orphan occurrences incrementally and respects the same lock order as
reconciliation. A shared route artifact survives while any current membership
still references it.

Publications are actual shared v1 `Publication` values, with workspace,
publication ID, source revision, publication generation, build time, durable
executable identities, exercise revisions and complete specifications. Canonical
payload/digest records are immutable. Replaying the same identity and payload is
idempotent; changing an existing immutable identity's payload conflicts. A newer
generation cannot be superseded by a stale worker.

`PublicationValidation` describes Content's build result. `PublicationAcceptance`
is Learning's acknowledgement of acceptance, separate from validation.
`ActivationRequest` and `ActivationAcknowledgement` belong to the
external acceptance/activation interface: Content publication is not Learning
serving activation. Empty publication retirement and Learning's current 128-fact
limit require #193's coordinated follow-up; see the README.

Learning's current legacy opening adapter additionally requires its old FEN/moves
card digest. Source-derived Content IDs remain unchanged in the compatible
`study_exercise` consumer proof. The opening identity mapping and any Learning
history continuity require a coordinated #193 integration; the separate legacy
hash fixture does not authorize rewriting ordinary Content identities.

Game Evidence's `ComparisonSnapshot` is its existing service-local interface,
not a newly accepted shared schema. The test-only adapter binds matching
publication/source identities, route moves, exact learner color and bindings.
Its activation remains null. Formalizing that producer/consumer surface requires
Content, Game Evidence, and shared-contract ownership agreement.

## PostgreSQL authority

Content exclusively owns its separate `content` database and `content` schema.
It owns input/source revisions, stable identities, route artifacts/memberships,
canonical positions/legal replies, occurrences/responses, generation tasks,
collector progress and immutable publications. It has no Learning review,
scheduling, attempt, queue, quota, settlement, or receipt tables.

`tempo_content/migrations/manifest.json` defines the ordered migration inventory.
The owner credential applies it explicitly and grants the runtime role bounded
application permissions. Runtime reads the migration ledger but cannot change
it, create objects, update immutable publication records, impersonate the owner,
or connect to foreign component databases. Runtime owns its own connection pool;
no shared ORM, cross-service SQL or implicit startup migration exists.

Native test credentials are generated inside one disposable owned cluster.
`TEMPO_CONTENT_TEST_FOREIGN_DATABASES` maps the three foreign component names to
connection strings using the Content runtime credential; those connections must
fail. It never identifies live component databases. The native runner's admin
credential exists solely for fresh provisioning, targeted test assertions and
owned resource recovery.

## Inventory snapshots and continuation cursors

`inventory_status`, source-scoped `positions`/`routes`, and `coverage` use one
short PostgreSQL `REPEATABLE READ READ ONLY` transaction per operation. Every
contributing query uses that connection, with the established five-second lock
and ten-second statement timeouts. Readers take no source/worker/artifact row
locks. A read begun against G1 can finish with coherent G1 data after G2 replaces
it and collection deletes G1's mutable cache. A fresh invocation selects G2.
Incomplete current coverage remains unavailable; missing mandatory records in a
complete inventory raise `inventory_incomplete` rather than successful emptiness.

Position/route pages return `source_revision`, `generation`, `status`, `stale`,
`items`, and `next_cursor`. Available and unavailable coverage include revision
and generation. `stale` retains its existing comparison against the last published
complete revision; reconciliation completion and publication remain distinct.
Only complete inventories provide stable traversal; pending inventory may grow
within its generation and should be paginated again once complete.

Treat cursors as opaque strings and return them unchanged. Their canonical JSON
is versioned and binds workspace, source, reader kind, revision and generation:

```json
{"generation":2,"kind":"positions","last":"position-key","source_key":"source-key","source_revision":2,"version":1,"workspace_id":"workspace"}
```

For `kind="routes"`, `last` is the ordered `["route-key","policy-key"]` pair.
Missing fields, incorrect primitive types, unsupported versions, wrong reader or
workspace/source identity, malformed JSON and old unversioned cursors raise
`invalid_cursor`. Revision/generation mismatch raises `stale_inventory_cursor`;
restart without `--after`. Canonical global `replies()` retains its UCI cursor.

## Admission, repertoire lookup and readiness

Source loading validates shape/workspace and computes the canonical digest before
a coherent read-only revision preflight. Distinct already-stale edits and unknown
sources with a nonzero expected revision raise `stale_source_revision` before any
input write. Identical current content returns replay even with an old expected
revision and creates no stage/task/generation. Final source-locked admission still
rechecks digest/revision after bounded staging, fencing concurrent editors.
Interrupted stages remain resumable. Crash/race-loser stage reclamation is a
separate lifecycle task, [#292](https://github.com/aaweaver-actuary/tempo/issues/292).

Repertoire keys belong to sources. An unscoped lookup inspects at most two current
matches in a consistent snapshot: zero returns `repertoire_not_found`, one returns
its ordinary authored fields/stable ID/source key, and multiple return
`ambiguous_repertoire` directing the caller to `--source-key`. Explicit selection
preserves source identity; no full authored snapshots are reconstructed.

One table privilege map drives owner grants and readiness. Metadata requires
SELECT; immutable runtime tables require SELECT/INSERT; mutable runtime tables
require SELECT/INSERT/UPDATE/DELETE. Readiness checks each required privilege
individually, schema USAGE, and all existing prohibited role/schema/database and
immutable-table powers. Missing grants fail before service operations, naming the
table/privilege without credentials. Explicit owner migration restores the grants;
runtime startup never migrates. Historical migration SQL/checksums are unchanged.

## Terminal Learning failure reconciliation

`tempo-content operation record-failure OPERATION_ID --learning-origin ORIGIN --operator-token-file FILE` independently retrieves the original operator-authenticated Learning operation. No caller-provided failure document is accepted. The receipt must bind the original workspace, exact request, command family, publication/generation and delivery authority, and be durably `failed`. Under source/operation locks, Content records a fingerprint and sanitized error status/code then changes `applying` to terminal `failed`. Identical replay is valid after restart or a successor approval; conflicting terminal outcomes are refused. Source revisions are never rolled back.

Timeouts, HTTP errors, unknown/queued operations and stage-only rejection retain the reservation. The stage endpoint has no durable terminal operation receipt; reconcile the original delivery after restoring its original authority/staged inputs. An unresolved stage rejection remains an explicit operator recovery boundary, not permission to cancel an already authorized operation.

Migration `0004_bridge_proposal_identity` adds database uniqueness for `(proposal.workspace_id, source_key, proposal.proposal_id)` on proposal drafts. Existing duplicates stop migration without deletion. Runtime grants do not expand. Identical proposals return the durable winner; changed proposal bytes raise `proposal_identity_conflict`. A semantic draft ID with changed immutable provenance raises `immutable_draft_identity_conflict`, preserving both stable exercise identity and the original draft.

## A pre-activation recovery

`operation show` additionally returns the source-owned `publication_id`, even
before source admission. `operation prepare` checks the frozen draft's A scope
before application authorization: complete, at most 2,048 reference-graded opening
move lines, real bindings within authored routes, and distinct physical aliases.
General Content v1 still accepts typed exercises including explanation.
Unsupported A scope commits `failed` with a fixed error code and no source revision
advance. Correct a draft at the actual current revision; never reuse a failed ID.

`tempo-content operation fail-before-delivery OPERATION_ID` durably abandons an
`applying` operation only while its document has no delivery checkpoint or receipt.
It shares the source-scope and operation locks with checkpointing. Identical replay
returns the same failure, including after successor approval. Source admission
rechecks still-applying authority under those locks so stale preparation cannot
advance the source after release. Already admitted source revisions are retained.

Checkpointed operations require the original authenticated Learning terminal
receipt, including a Learning publication fence. `record-failure` independently
fetches and binds that receipt; no caller-supplied HTTP error or failed-state claim
releases a reservation. No new state, migration or shared wire contract is added.

Completion verification applies the same exact original envelope binding as
terminal failure. Retirement response fields alone cannot identify the retired
publication; reconstruction is permitted only after the authenticated receipt's
workspace, command, original request, publication, generation and ID all match.

Exact request comparisons preserve canonical JSON primitive types: integer,
boolean and floating-point substitutions do not compare equal.

## Captured legacy authority (C3)

The offline `tempo-a-legacy-export` consumer translates an explicitly selected,
complete route scope into the existing authored-import documents. It does not
assign physical Learning identity to normalized opponent-prelude exercises.
`historical_mapping_unknown` lists source exercise keys whose historical physical
identity is unresolved; Game Evidence must return unknown at these learner
bindings. This does not weaken Learning's exact alias validation.

A source route may declare `authority_start_ply` (default 0, null means never
established). This is an ordered-route policy-selection boundary, included in the
source and activation route digests. Opponent-response completeness and learner
comparisons cannot become authoritative before it. Default zero is omitted from
serialization to preserve previous route identities. Re-entry still requires its
existing explicit binding authority.

Complete bridge scope now permits 8192 definitions; the Learning execution bundle
is bounded at 12 MiB and the immutable export at 64 MiB. Authored CLI inputs
are bounded at 16 MiB. Large immutable internal bridge checkpoints use bounded,
hash-verified compression; public operation/export/snapshot documents retain their
original canonical contents. Existing inline checkpoints remain readable.
