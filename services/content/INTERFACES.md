# Content interfaces and ownership

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
