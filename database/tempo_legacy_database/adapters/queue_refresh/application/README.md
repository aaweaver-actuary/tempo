# Legacy queue application storage

**Current source; live schema and deployment unverified.** These fixed operations
borrow the existing Learning/queue caller connection. They never open, commit,
roll back or close a transaction, and add no queue, task or settlement authority.
The legacy [application composition](../../../../../backend/app/services/queue_application.py)
keeps phase order, bounded page/selection decisions, daily allowance arithmetic,
chess validation, response construction and existing feedback callbacks.

[eligibility.py](eligibility.py) owns the three original opening-unlock SQL
constants, dialect selection, bounded candidate reads and selected-card update,
and the unchanged opening/defense block, due/Study restoration and introduction
reset operations. The native page retains one captured eligibility projection
between its read and selected-card write. Original private names remain
compatibility exports in the application; callers retain the same boundaries.

[seeding.py](seeding.py) owns the existing allowance/position/count facts, due
and published-Study candidate queries, due/Study append operations and active-miss
projection read. The application still chooses the allowance, applies remaining
quota, iterates and promotes/clears miss priorities in the same order. Legacy
feedback coupling is preserved, not claimed as newly isolated; no optional
producer dependency or cross-owner permission was added.

```mermaid
flowchart LR
  queue[Legacy queue application] -->|fixed facts and writes| storage[Root eligibility and seeding operations]
  queue -->|same policy and callback order| policy[Allowance and admission decisions]
  storage -->|borrow existing transaction| connection[Caller connection]
  storage -->|same rows and constraints| tables[Learning queue and authored-content projections]
```

| Stored facts/effects | Source fields and relationships |
| --- | --- |
| Daily queue | `daily_queue.id bigint PK`; `(queue_date text,card_id text,cycle bigint)` unique tuple; non-null `position bigint`, `status text`; nullable `card_bucket text`, `admission_kind text`, `admission_repertoire_id text`. `card_id` has an enforced card FK. Date, bucket and admitting-repertoire associations have no added FK. [All queue fields/types and constraints](../../../schema/study-queue-review-1/README.md). |
| Cards and review evidence | Existing card `id text PK`, nullable repertoire/exercise references, state/content/archive/validation/due/introduction facts; review evidence determines reviewed/unseen status. [Card fields and enforced links](../../../schema/study-queue-review-1/README.md), [review/exercise/burial dictionary](../../../schema/study-queue-review-2/README.md). |
| Opening eligibility | Current published graph generation, parent practice evidence, repertoire membership and integrity-card blocks retain their exact SQL filters. Graph card/parent/publication-generation links are logical where no FK exists. [Graph keys/dictionary](../../../schema/opening-graph/README.md), [membership and integrity fields](../../../schema/authored-content-2/README.md). |
| Study and defense eligibility | Published authored exercise/study state and per-day sibling burial; approved, non-superseded defense validation evidence. No new constraint or access is granted. [Study fields](../../../schema/study-queue-review-2/README.md), [threat fields](../../../schema/practice-families-2/README.md). |
| Daily settings and legacy miss facts | Existing `settings.id bigint PK`, `new_cards_per_day bigint`, `study_new_per_day bigint`; settings singleton use is application convention. Existing miss reason and logical queue-priority associations remain unchanged. [Settings](../../../schema/runtime-metadata/README.md), [feedback projections](../../../schema/game-projections-2/README.md). |

The linked schema READMEs are canonical for every field, primary/composite key,
enforced FK, cardinality and delete action. This extraction changes no DDL,
owner, storage format, policy, grants or persisted values.

[publication.py](publication.py) owns unchanged queue-position/bucket/kind writes,
plan publication, quarantine candidate facts and lock/skip writes, diagnostic
replacement, the blocked-count union and projection state/generation publication.
The existing exact saved-plan read is reused from [presentation](../presentation.py).
JSON parsing, chess validation, diagnostic text, randomization/frozen policy and
the timestamp captured **before** the blocked-count read stay in application order.
The byte-different original materialization start statement stays separate from
[foreground.py](../foreground.py); its existing callers and constant are unchanged.

[requeue.py](requeue.py) owns the original queue-plus-origin next-cycle query,
append/offset position reads, same-day position shift, returning insert and review
bucket assignment. The application still captures the prior order before its
first read, chooses append versus offset, clamps the offset, preserves order
only after all writes and returns the inserted identity. Same-date existing
origin rows remain cycle authority even after projection deletion. The exact
existing availability builder is reused from its root owner.

| Additional stored facts/effects | Source fields and relationships |
| --- | --- |
| Saved order and projection | `daily_queue_days(queue_date text PK,seed bigint,membership_hash text,generated_at text)`; `queue_projections(queue_date text PK,state text,generation bigint,updated_at text nullable,refresh_pending bigint,last_error text nullable,blocked_count bigint)`. Date associations are logical; neither table has an enforced queue FK. [Canonical bounded ER view and full dictionaries](../README.md#opening-and-presentation-publication). |
| Diagnostic replacement | `queue_projection_diagnostics` has composite PK `(queue_date text,card_id text)` and `message text`; no enforced FK. Existing whole-day replacement remains inside the caller transaction. [All fields and constraints](../../../schema/study-queue-review-1/README.md). |
| Repeat attempt provenance | `queue_attempt_origins.queue_entry_id bigint PK`, queue date/card/cycle/revision facts and existing head-issuance evidence retain their original authority and constraints. Queue/origin date/card/cycle relationships are unchanged. [Canonical origin dictionary](../../../schema/study-queue-review-1/README.md). |

All **39 original direct calls plus the existing sparse-selector call** from
queue application now live in root operations. The application has zero direct
DB calls, SQL literals or dialect checks; compatibility SQL names stay exported.
The separate queue admission adapter is now split as described below.
No connection or transaction lifecycle moved. Focused existing SQLite/fake cases
and exact one-time source comparison establish this mechanical extraction, not
native PostgreSQL execution, deployment or optional-feature resource isolation.

Proposal only: the parallel native/legacy queue paths and byte-different
projection statements could later share fewer fixed implementations after
separate behavioral review. This move retains their differences; no policy,
schema, storage or framework consolidation was implemented.


## Legacy admission adapter facts

[admission.py](admission.py) owns the existing 13 database sites from
[QueueAdmissionAdapter](../../../../../backend/app/services/queue_admission_adapter.py):
unseen-entry keyset pages, historical introduction counts, retain/remove writes,
priority projection and its capacity SQL transformation, candidate/repertoire
pages, current allowance, introduction append and opportunity-resolution writes.
The compatibility candidate SQL constant remains exported under its original
application name. Exact SQL bytes, predicates, parameters, query/result order and
caller-owned rollback/transaction authority are unchanged.

The application keeps bounded-page decisions and value/page construction,
miss/frozen policy and existing semantic collaborators. Its introduction path
still appends, invokes its retain hook, evaluates **two independent clock reads**
and then resolves the opportunity. It creates no new connection or commit.

The fixed borrowed capacity row operation contains the existing reader internally
and reuses [capacity.py](../capacity.py)'s same lazy bounded cursor generator.
`capacity_routes_from_rows` in the [application bridge](../../../../../backend/app/services/opening_capacity_admission.py)
contains the original pure `CapacityRoute` construction and deterministic winner
comparison. Its existing `read_capacity_routes(read, repertoire_id,
normal_parent_ids)` signature remains a compatibility wrapper. Both paths consume
rows lazily and interleave each row with the same route decision; no generic SQL
executor, reader factory or database-to-application import was added. The borrowed
path does not close a connection; the caller retains its lifetime.

| Admission facts/effects | Source fields, keys and ownership |
| --- | --- |
| Historical allowance and unseen membership | The same Learning queue/card/review facts above; `(queue_date,card_id,cycle)` distinguishes repeated attempts, and the admitting-repertoire association remains logical without a new FK. Shared card counts and old introductions keep their existing meaning despite later integrity changes. |
| Published and legacy introduction priorities | `repertoire_card_introduction_priorities(repertoire_id text PK/FK,card_id text PK/FK)` and `repertoire_card_priority_generations(repertoire_id text PK/FK,generation bigint PK,card_id text PK/FK)` store score and JSON evidence; publication chooses a `generation bigint`. The selected generation association is logical unless explicitly constrained in the [canonical fields/ER dictionary](../../../schema/discovery-priority/README.md). This move does not merge their models. |
| Repertoire opportunities | `repertoire_opportunities.id text PK`, `repertoire_id text FK`, nullable `card_id text FK`, `kind/status text`, `evidence_json text`, nullable `resolved_at text`, and non-null `updated_at text`. The same weak-known-decision resolution excludes analysis-derived evidence. [Every stored field/type and actual FK/cardinality](../../../schema/discovery-priority/README.md). |
| Legacy miss priority | Current active-miss and gameplay-priority projections retain the existing reason/date source and exclusion semantics. These legacy associations are unchanged and do not establish optional-producer isolation. [Feedback projection dictionary](../../../schema/game-projections-2/README.md). |

Both application facades now contain zero direct database calls or SQL literals.
Focused task-owned SQLite/fake checks cover shared membership, page boundaries,
retained historical counts, rollback after the retain hook, route witnesses,
lazy query/policy order and the two-clock sequence. Two previously identified
capacity-worker SQLite fixtures still reject the unchanged native active-miss
cast before this adapter runs; no assertion or fixture was changed. Native
PostgreSQL execution, live schema and deployment remain unverified.
