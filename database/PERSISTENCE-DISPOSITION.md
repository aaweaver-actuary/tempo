# Graph-preserving fresh-start disposition for #362

Revised 2026-10-25 after the user's explicit fresh-start decision. Source names
below were checked at `1a0fd5c582c13f9d0de428d2f66d7e7cb358c4bf`; they do not
claim to identify the actual installation. **No live deletion, reset, migration,
file removal or cutover has run.** There is no target live table count.

The retained outcome is a **usable opening graph, necessary repertoire
definitions and any new verified graph recovery copy**. Old reviews/schedules,
server receipts, background work, browser old envelopes and old backups need not
migrate, drain, replay or be restored. Their presence is not an indefinite
retirement blocker. Do not resume the four historical converters or require an
accepted full-history transfer receipt for this design.

The [original disposition at `4e0029a5`](https://github.com/aaweaver-actuary/tempo/blob/4e0029a5767a83da210f73fb844e45640ffac11b/database/PERSISTENCE-DISPOSITION.md)
remains dated authored work. Its history/drain requirements, and the corresponding
requirements in #267/#389 or older issue bodies, are superseded for this fresh
start. The old source inventories and tests may still identify concrete coupling;
their old policy gates are not imported as current acceptance.

## Owners and shared contract

| Packet | Owner | Writable boundary / shared contract |
| --- | --- | --- |
| Opening graph and repertoire dependency closure | `retained_artifact_correspondence` | Owns exact graph keep set, source fields/relationships and graph-preservation fixture. #362 consumes that public, versioned keep set; it must not infer aliases or import converter internals. |
| Persistence disposal disposition | `persistence_362` | Owns only this document, `database/README.md` and `database/SIMPLIFICATION.md` in existing PR #441. No SQL executor or live cleanup is implemented. |
| Exact source removal and retained artifact checks | #390/#391 owners | Source-only manifests and bounded implementation/absence proofs; old-work drain is not required. Resolve graph-serving imports, build/launcher consumers and plugin failure behavior before removing their source. |
| Actual graph recovery copy and irreversible execution | Designated operator/executor | Identify actual paths/database authority and preserve the new verified graph recovery copy. Exact irreversible actions remain pending final confirmation; this document does not delegate execution. |

## Keep boundary before choosing disposal targets

The closure owner must freeze the exact graph/repertoire source tables, fields,
keys, active generation/pointer relationships and public export/restore shape.
That keep set takes precedence over every disposal row below. It must distinguish
structural card/definition/membership payload from old schedule/exposure columns
when they share rows. Do not reset or drop an entire mixed table to clear its old
learning fields. Retain stored graph facts even if a projection is theoretically
rebuildable until its usable recovery path is verified.

The closure owner's preliminary source check protects `repertoires`,
`repertoire_lines`, `repertoire_line_training_depths`, structural `cards`,
`repertoire_cards`, `opening_graph_steps`, `opening_graph_publications`,
`prefix_splits` and their referenced cards, `deleted_cards` exclusions and the
required integrity issues/blocks or an explicit verified rebuild disposition.
Also protect active `canonical_prefix_previews`, `canonical_prefix_results` and
`canonical_prefix_positions` selected by the repertoire's preview/revision
identity; a preview/scratch name does not make those facts disposable. This is a
preliminary source keep list, to be replaced by the exact versioned closure.

The [migration 035](tempo_legacy_database/ddl/migrations/035_canonical_publication_freshness.sql)
`advance_repertoire_game_scope` trigger on `repertoires` updates both
`repertoire_game_scope` and `priority_source_epoch`. Keep those relations while
that trigger remains; dropping a nominal priority table would break graph/source
edits. Preserve canonical line/card/membership source-revision triggers and their
provenance flags. Removing that dependency needs a separate exact trigger change,
not a blanket priority cleanup. The six optional callback withdrawal hunks retain
epoch advance and canonical invalidation.

The existing [portable snapshot reader](tempo_legacy_database/adapters/http_composition/snapshots.py)
omits `repertoire_line_training_depths`, `opening_graph_steps`,
`opening_graph_publications`, `opening_graph_legacy_mappings` and `deleted_cards`.
Do not certify that snapshot alone as the new graph recovery copy. Resolve the
omitted definitions/relationships in the graph-only export/restore contract.

A new graph-only target can start with empty old-work/history stores. Its owning
writer creates new reviews/receipts/queue state normally; no retired producer
becomes a hidden serving dependency. Keep current Study intact until the exact
fresh-start transition is separately confirmed. Source preparation does not
switch the current authority or adopt a new launcher.

## Bounded disposal candidates

Names are exact **source** scopes, not executable SQL or a live deletion list.
Each row is disposable only after it is excluded from the frozen graph/repertoire
keep set and its removal cannot cascade into that set. Unknown actual targets
must be resolved before the specific irreversible action is presented.

These are **old-row reset candidates, not DROP lists**. Current graph/recovery
code reads shared `background_tasks`, `reviews`, `daily_queue`,
`queue_attempt_origins`, `settings` and frozen activation. Old learning/work rows
may be discarded, but those relations and any required graph task rows remain
until their retained code dependencies are explicitly resolved. The exact
old-authority/cutoff predicate is an execution input, not guessed here.

| Family | Exact source scope / existing correspondence | Fresh-start disposition and remaining separation condition |
| --- | --- | --- |
| Old generic execution/control | Legacy `public.operation_receipts`, `background_tasks`, `background_task_events`, `background_activity`; [initial DDL](tempo_legacy_database/ddl/migrations/001_initial.sql). | Old payloads, results, retries, leases, errors, pauses and completion history may be discarded. No drain/replay or original-operation reconciliation gate. First separate any graph build/publication state identified by the closure owner; retained source must not depend on a deleted handler/registry while serving the graph. |
| Old queue and learning history | Legacy `public.reviews`, `review_attempt_receipts`, `review_schedule_snapshots`, `daily_queue`, `daily_queue_days`, `queue_projections`, `queue_projection_diagnostics`, `study_attempts`; [initial DDL](tempo_legacy_database/ddl/migrations/001_initial.sql), [review receipt DDL](tempo_legacy_database/ddl/migrations/016_phone_review_receipts.sql). | Old reviews, scheduling, attempts, queue issuance and diagnostics do not need migration. Mixed `cards` rows/columns are **excluded** from this whole-table candidate list: structural graph/definition/membership fields require the closure owner's exact split. Incoming FKs/triggers must be checked against the keep set; do not use cascading deletion. |
| Guided Games review | Legacy `public.guided_review_sessions`, `guided_review_attempts`; [#423 source candidate](https://github.com/aaweaver-actuary/tempo/pull/423). | Old sessions/attempts and corresponding old command/browser work may be discarded after graph separation. The old active/completed/broker/browser settlement requirements are superseded. Reuse #423's exact source paths without treating ordinary Study guided scheduling or graph-serving code as the Games capability. |
| Adaptive-priority old computation | Legacy `public.repertoire_priority_jobs`, `repertoire_priority_preparations`, `repertoire_priority_prepared_rows`; [preparation DDL](tempo_legacy_database/ddl/migrations/021_priority_preparations.sql), [#427 packet](https://github.com/aaweaver-actuary/tempo/pull/427). | Old queued/running/failed/prepared work may be discarded; no finite-drain or hold decision is required. Saved priority rows/publication pointers, shared epochs and frozen-order rows are **not** included in this bounded work-only list until the closure/retained Study owner resolves their graph/serving dependency. #431 already removed one hook; the other source hunks still need their own concrete review. |
| Opportunity/recommendation/admission old work | Legacy `public.discovery_admission_intents`, `discovery_recommendation_requests`, `coverage_discovery_recommendation_requests`; [revisioned DDL](tempo_legacy_database/ddl/migrations/028_revisioned_discovery_admissions.sql), [#429 packet](https://github.com/aaweaver-actuary/tempo/pull/429). | Pending and historical work may be discarded rather than completed. Keep any already materialized repertoire definitions/lines/cards selected by the graph closure; deleting their old intent is not permission to delete the authored result. `repertoire_opportunities` and `current_repertoire_opportunities` are not added to the disposal list while graph/retained serving dependencies remain unresolved. |
| Daily statistics and old results | Legacy `public.daily_statistics_jobs`, `daily_chess_snapshots`, `daily_chess_insights`; [initial DDL](tempo_legacy_database/ddl/migrations/001_initial.sql), [#428 packet](https://github.com/aaweaver-actuary/tempo/pull/428). | Old jobs/results/insights may be discarded; no status-preservation, waiting-result acceptance or historical-freeze gate remains. Keep graph/repertoire consumers outside this list. Source removal still preserves unrelated retained ingestion/feature interfaces unless its own exact manifest retires them. |
| Browser old work | Guided pending v1/v2 records already listed by #423; opportunity-refresh/action adapters and discovery-admission outbox listed by #429; original-authority review/prepared/offline envelopes. | Old envelopes and replay obligations need not survive the fresh start. This is not blanket origin/IndexedDB/localStorage clearing: actual database/store/key names must be enumerated and graph/repertoire/connection configuration separated first. No browser inspection or clear has run. |
| Old scratch and backups | Old test/build scratch, abandoned migration snapshots and old backup artifacts; actual file paths remain UNKNOWN here. | They may be disposed of without restoring or validating their historical contents, subject to exact path ownership and graph separation. Never expand this to all backup directories or guessed globs. Protect any graph source and every new verified graph recovery copy explicitly before presenting the exact deletion action. No file deletion has run. |

Compatibility `_legacy` tables, staged/publication tables, `learning_obligations`,
shared service stores and the complete current database are not automatically
included. A shared suffix, noun or old schema count does not establish graph
separation. Their source maps remain useful only for resolving the exact keep and
disposal sets; no cross-owner schema consolidation is proposed.

## Next executable slice and final confirmation boundary

1. Consume the closure owner's exact versioned graph/repertoire keep contract and
   verify its export/restore fixture preserves usable graph structure and required
   repertoire definitions. Prepare source-only checks for intersection and
   dependency/cascade mistakes; do not query or mutate the live target here.
2. Bind the reviewed keep/disposal sets to the actual target and identify the new
   verified graph recovery copy. No full-history map, old receipt reconciliation,
   old job drainage or old-backup restoration is required.
3. Present one exact action: named database/schema/table/row-field reset **or**
   named file/store/key deletion, target identity, keep-set exclusions, graph-copy
   location and verification receipt, expected empty/new state, affected runtime
   dependencies and transition sequence. Obtain final confirmation for that
   irreversible action before executing it. Do not substitute “delete old data”
   or “drop the database” for this bounded proposal.

Current code/build/plugin isolation and a usable fresh graph must be proven at
that boundary. No old migration/drain requirement is retained by calling it a
launcher gate. A composition root may wire public contracts; no cross-domain
internal imports, cycles, shared mutable authority or hidden optional service
are introduced. Optional plugin failure must leave unrelated workflows usable.

Architecture impact: none. These three documents revise policy/prerequisites;
implemented schemas, interfaces, transactions and runtime ownership are unchanged.
