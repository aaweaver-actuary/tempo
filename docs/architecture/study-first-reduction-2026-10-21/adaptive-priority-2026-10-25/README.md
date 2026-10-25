# Adaptive priorities: bounded source transition candidate

**Preparation only, 2026-10-25. No production changes or approved deletions.**
Inspected main is `782bf5374b87f223a023568305056415d5b63cea`, tree
`8896b0bcdbe360d1ff609030234b22a0cd803b4f`. The supplied `NARROW-NEXT.md`
and its six core hashes match this source. [SOURCE-MAP.json](SOURCE-MAP.json)
pins that input, every core definition span, and the retained consumer sources.
The separate opportunity feed/refresh and guided-review packets are outside this
packet's edit ownership. Historical FEATURES, retirement manifests and audit
receipts are unchanged.

The six files contain **1,407 comparable physical lines**, including comments and
blanks. That is an inspected footprint, not removable or saved lines. This PR
removes **zero** production lines. The [selected direction](../DECISION.md) retires
future adaptive ranking while preserving current scheduling, eligibility, quota,
history and pending recovery. It does not establish that existing scores, ranks,
jobs or refresh intents are empty.

| Core path | Lines | Current responsibility |
| --- | ---: | --- |
| [introduction_priorities.py][calculator] | 933 | Evidence selection, calculation, enqueue/claim/retry, legacy and staged publication, evidence readback |
| [postgres_priority.py][worker] | 193 | Fenced resumable preparation, staging and publication; compatibility source-version wrapper |
| [adapter namespace][namespace] | 1 | Package documentation |
| [inputs.py][inputs] | 102 | Borrowed coverage, card, personal-game and settings reads; evidence-status error read |
| [jobs.py][jobs] | 105 | Generation enqueue/claim/status/retry and completion operations |
| [publications.py][publications] | 73 | Legacy/generation writes, publication pointer and saved-evidence reads |

## Which Study path depends on which facts

These are source branches, not an assertion about which one serves Andy today.

| Serving path | Existing dependency and fallback |
| --- | --- |
| Legacy synchronous queue | `queue_application.seed_queue` → `queue_admission.plan_opening_admissions` → the saved-priority projection and `capacity_frontier.plan_prioritized_opening_admissions`. The SQL joins the pointer-selected **current** generation and the **current** legacy table. Each of score/completed-line JSON/frontier JSON uses `COALESCE(published, legacy)`. Due reviews are admitted before new openings. |
| Legacy PostgreSQL queue, ordinary mode | `postgres_queue_refresh._prepare_prioritized_openings` → `queue_refresh.reads.priority_opening_rows` → `openings._PRIORITY_OPENING_PAGE_SQL`, then the same pure selection kernel. Capacity expansion also reuses this projection and selector behind its existing route/quota gates. |
| Legacy PostgreSQL queue, Study-only mode | `execute_postgres_queue_refresh_slice` lines 597–608 redirects saved `prioritized_openings`/`prioritized_opening_item` checkpoints to `study_opening_page`. `_prepare_study_opening_page` and `reads.study_opening_queries` read graph routes, optional frozen preference and a stable repertoire/line/decision/card tuple; they do **not** read adaptive priority publications. Existing graph, opportunity/miss eligibility, quotas and frozen validity remain dependencies. A mode switch is not a deletion authorization. |
| Selected independent Learning runtime | Its [frozen policy][learning-frozen] consumes captured ranks, definition fingerprints, membership blockers, legacy protection and exposure facts. Valid missing ranks remain unranked and use position/id after finite ranks in movable slots. `review_adapter.refresh_priority` writes a Learning-local `advisory_notifications` fact through its [database adapter][learning-review]; it does not call this legacy calculator. No adaptive-score fields or imports were found in `services/learning/runtime/src` or `database/tempo_learning_database`. This is source evidence, not proof of installed authority or migration. |

The legacy readers are [application admission SQL][admission], [native SQL][openings]
and [bounded reads][reads]. Their owners remain the existing queue/application
writers, not the optional producer. No new queue, serving or ranking system is
proposed. [#231](https://github.com/aaweaver-actuary/tempo/issues/231) concerns the
independent Learning unranked/frozen contract; it does not authorize erasing legacy
saved scores or replacing their breadth selector with position/id.

Independent Learning's initial candidate selection is separately owned by
`database/tempo_learning_database/adapters/admission.py::next_candidates`: existing
introduced/type tier, frozen ordinal, then repertoire/line/decision/card route
tuple. Position/id above describes movable **queue slots**, not a replacement for
that admission query. Neither path consults this packet's legacy saved-score
tables.

## Historical fallback is still consumed; calculation is still wired

* The compatibility `repertoire_card_introduction_priorities` table is a historical
  representation with **active readers**, not dead storage. A missing published
  card can fall back to its current legacy row even when a publication pointer
  exists. An empty published generation also does not suppress that fallback.
* The pointer selects a generation; merely staged or numerically newer rows do not
  become visible. Both generated and legacy rows pass the current-view canonical
  prefix/scope/game-generation fences in [migration 035][freshness] and the existing
  [SQLite compatibility DDL][sqlite-freshness]. Keeping raw rows is not permission
  to bypass those views. Stale rows may remain as history while their score is
  absent from serving.
* Frozen opening ranks/activation are a different saved model. In the legacy
  [selector][selector], mandatory misses precede valid frozen ranks; valid unranked
  cards then recurse through the ordinary saved-score/breadth path. Definition,
  legacy-origin and membership protection, global card deduplication, quotas and
  issued/exposed-slot protection must survive. A saved rank does not make all
  saved priorities irrelevant.
* `priority_status` reads saved published evidence, otherwise saved legacy
  evidence, without calculating. Its `fallback` label describes evidence quality;
  it is **not** proof that no score rows exist. It can return a stored timestamp
  while reporting `fallback`. It also reads current-scope coverage errors.
* The calculator uses coverage/personal evidence, line completion, weighted
  frontiers and a real-game-miss bonus. These are selected for eventual producer
  retirement, but are not unused in current source: the coordinator calls
  load/calculate/publish and `tasks.py` registers the PostgreSQL slice handler.
  Synchronous `rebuild_introduction_priorities` and `execute_priority_refresh`
  retain named parity, feedback, freshness and replay test consumers; the latter
  also remains imported by the coordinator. No isolated unused helper is proved
  and no algorithm/regression is removed here.

## Minimum compatibility surfaces before any core removal

This is a preservation contract for **existing** operations, not implementation
of new ports. Keep the definitions/import paths until every retained caller is
explicitly rebound in a separate approved patch. A future move must preserve the
same inputs, results, transaction ownership and errors; a successful import alone
is insufficient.

| Existing surface | Consumers and required semantics |
| --- | --- |
| Saved admission facts: nullable `priority_score`, `completed_line_ids_json`, `frontier_decisions_json`, plus existing candidate/eligibility fields | The two queue projections above, ordinary selector and capacity selector. Preserve per-card/per-membership published-first fallback, current-view filtering and publication pointer. Null scores become zero only in the existing selection key. Preserve JSON line breadth and frontier ply tie-breaks; do not replace all scores with zero. |
| `priority_status(database, repertoire_id) -> dict` | `main.py:1583` repertoire listing and `app/domain/schemas/index.ts` response shape. Preserve `state`, rounded `personal_games`, `explorer`, `maia`, nullable `updated_at`/`error`; published-evidence-first then legacy lookup. Preserve `inputs.coverage_error` or its exact existing read if this reader ever moves. |
| `SCORING_VERSION = 2` and `_priority_source_version(database, repertoire_id, *, lock=False) -> str \| None` | [refresh_requests.input_version][refresh] imports **both** scoring constants unconditionally, even for opportunity refresh; it uses the epoch wrapper for both kinds. [durable_tasks.complete_task][completion] imports the wrapper when completing an opportunity task. Retained completion can fail on a missing import even with priority recomputation disabled. The wrapper delegates to lifecycle `source_version`; keep that implementation/compatibility binding independently of the calculator. |
| Lifecycle `source_version` epoch contract | [preparation.py][preparation] reads/optionally locks shared epoch **first**, ensures a per-repertoire epoch only for an existing repertoire, then reads/optionally locks it. Returns `shared:repertoire`, or `None` when absent. This is not a pure read: it can insert the epoch row. It neither computes priorities nor opens/commits a transaction. Native refresh requests lock before job/task/intent operations. Preserve the lock flag and source-first order. |
| `publications.publish_generation(database, repertoire_id, generation, updated_at)` | Re-exported by lifecycle preparation and called by the PostgreSQL priority worker. It only upserts the publication pointer. The caller owns source/job/task lease checks, completeness checks, transaction and timestamps. Do not move a pointer write outside that commit or mistake this primitive for a generation fence. |
| `jobs.current_job(database, repertoire_id)` | Re-exported by [lifecycle retention][retention] and used in the SQLite retention branch; returns generation/status or no row. Native retention uses the separately locked job read. [priority_retention.py][retention-app] preserves published and active generation rows, protects queued/running preparation, and deletes at most the existing bounded slice. Deleting a job row or import can invalidate that protection. |
| Existing enqueue/claim/handler names and generation return | Accepted work must retain honest recovery. Enqueue coalesces unchanged intent, returns the known/new generation and borrows the caller's transaction. Native enqueue also schedules retention. Keep pending payload cursor/phase, attempts, failure, lease and publication facts until a separately reviewed drain/retain/cancel disposition exists. Disabling new producers is not fabricated completion of old work. |

Refresh identity currently serializes `[scoring_version, source_epoch,
[scope_source_revision, canonical_prefix_revision, canonical_prefix_preview_id],
[graph_generation, state] or null, published_priority_generation or 0, UTC_day]`.
The priority-generation member is read for opportunity refresh only; it is zero
for priority refresh. Preserve this exact ordering and change behavior. No-op
intent coalescing, five-second quiet window, sixty-second maximum deferral and
terminal failure semantics belong to the existing refresh owner.

Opportunity completion takes the source lock, conditionally completes by task
generation/lease, and only on accepted completion records
`published_input_version=input_version` in that same write. A stale completion
must not satisfy the replacement intent. The source helper, scoring identity and
publication/job primitives must be retained or explicitly rebound **before** a
future patch deletes their importing modules. This packet changes none of the
opportunity implementation or shared completion/refresh files.

## Incoming producers and control surfaces

| Source at the pinned revision | Edge requiring an explicit future disposition |
| --- | --- |
| `canonical_prefix_api.py:62–67` | Clears old pointer/legacy rows and enqueues after prefix refresh. Preserve canonical invalidation and its caller transaction. |
| `coverage_maia_commands.py:100`; `postgres_coverage_explorer.py:120`; `repertoire_coverage.py:552,708` | Coverage completion triggers new priority work. These are proposed cessation sites, not unused code. |
| `learning_review_adapter.py:204–210` | Shared review application calls `refresh_priority` after real review results; legacy native enqueue is in the review transaction, SQLite follow-up is deferred until its connection exits. Keep the independent Learning adapter's local notification separate. |
| `postgres_game_priorities.py:37`; coordinator `:146` | Game derivation hands off priority work. Preserve its own phase/lease/completion obligations when deciding to stop this optional effect. Opportunity handoff is owned by the other packet. |
| Coordinator imports/legacy claim and `:454–464`; `tasks.py:94,390` | Calculation/publication remain reachable worker behavior. Native slices own task settlement; removing just the mapping produces an unsupported-handler error. |
| Core enqueue `:687–716`; publication `:759–900`; native worker `:178–193` | New priority work schedules retention; successful publication records refresh acceptance and enqueues opportunity refresh. The other packet owns the opportunity side; do not edit it independently. |
| `database/tempo_legacy_database/adapters/controls/activity/storage.py`; `backend/app/services/durable_scheduling.py`, `background_metric_kinds.py`, `background_diagnostics.py`; `app/domain/schemas/background-diagnostics.ts` | Work identifiers remain visible/controlable; do not erase labels while pending work still needs truthful status. These references are not proof every handler is running. Full paths/hashes, plus the response schema `app/domain/schemas/index.ts`, are pinned in SOURCE-MAP. |

The map's import in `main.py` does not imply a dedicated priority-refresh route:
the two enqueue names are imported there without a direct invocation; saved
status readback is invoked. The externally reachable automatic sources above
still suffice to keep this core active. Saved-table users also include
`database/tooling/frozen-maia-order.py` and `reclaim_priority_storage.py`.
Scope DDL/triggers, prefix invalidation, activity reset and export/inspection
consumers stay; neither source absence nor tool-only use permits a table drop.

## Synthetic counterexamples to unsafe deletion

These are **source-traced examples**, not user data, fixtures executed in this PR,
or observations of storage. Candidates are otherwise eligible, due, unintroduced
openings with valid membership/content, no gameplay reason and equal move length
unless stated. Ordinary-selection examples share one repertoire, start with no
consumed allowance, inactive frozen preference and empty completion/frontier facts
unless the row specifies otherwise. `A`, `B`, `C` and generations below are invented
symbols.

| Synthetic facts | Required current result and source |
| --- | --- |
| Pointer 7 selects A=.9/B=.2; unpublished generation 8 has A=.1/B=.95; allowance 1 | Ordinary selection chooses A. Staging cannot switch visible generation; [queue joins][openings]. |
| Pointer-selected generation lacks A; current legacy A=.6; published B=.4 | A retains .6. An existing pointer is not an all-or-nothing veto on legacy rows; per-field `COALESCE`. |
| Published A=0; legacy A=.9 | Effective score is 0, not .9: zero is non-null. An empty published generation can still fall back per card. |
| Raw historical A=.9 fails a current-view scope fence; B has no saved score; shorter moves belong to B | A's raw .9 is not served. Null score/frontier become 0 and ordinary length/id ties apply. Do not expose stale rows to preserve apparent rank. |
| A=.9 and B=.8 complete line L; C=.2 completes M; allowance 2 | A then C: breadth excludes already-covered L while a different line remains. Deleting only JSON changes behavior even if scores survive; [selector][selector]. |
| No frozen activation; all score/line/frontier fields absent | Ordinary fallback uses zero score/depth, then shorter `moves_json` length, then card id within the repertoire. It does not wait for calculation. This case is not asserted to describe stored data. |
| Active frozen rank A=1; valid unranked B=.8/C=.2; allowance 2 | A then B, through existing unranked recursion. A legacy-protected unranked B is held unless its existing mandatory-miss rules apply. Preserve quota/global-dedup and validity checks. |
| Study-only native queue resumes a `prioritized_opening_item` checkpoint | It goes to graph `study_opening_page`, without score reads. Its unranked graph tuple differs from the ordinary saved-score selector. No blanket policy replacement follows. |
| Independent Learning movable slots: A rank 2, B unranked at position 1, C rank 1 | C,A,B in those slots; issued/exposed slots remain fixed. Its existing position/id fallback does not require legacy calculation; [Learning policy][learning-frozen]. |
| Opportunity lease generation 4 completes after replacement generation 5; priority producer disabled | Keep the epoch import and lock contract; stale completion does not record publication for replacement input. Stopping calculation cannot be treated as permission to erase task/intent facts. |
| Publication 7, active job 8, stale generation 6 | Existing retention may reclaim 6 within its bound, preserving 7 and 8. Removing the current-job read cannot safely stand in for retiring calculation. |

## Exact remaining gate and next bounded change

No whole-file deletion or independently safe helper repair is justified here.
Before an implementation candidate, the reviewer/parent must freeze an exact
**stop-new-priority-recomputation** edit manifest and the disposition of issued
legacy jobs, native tasks/preparations and refresh intents. The manifest must name
every producer/control edge above, preserve stored-reader semantics and the
minimum compatibility bindings, and coordinate publication's opportunity callback
with that packet's owner. Until then, retain all six core files and lifecycle
owners. A declaration that ranks are frozen is insufficient because it does not
settle valid unranked legacy fallback or pending recovery.

An approved implementation then reuses the smallest affected checks: legacy
introduction-priority and frozen/unranked/new-card-limit/real-game-feedback cases;
refresh coalescing and stale opportunity completion; source-epoch lock order;
priority preparation/replay/retention and native freshness/publication contracts.
Concrete existing owners are listed in [CHECKS.md](CHECKS.md). No new framework or
policy model is needed. Required native proof applies if SQL, lock, publication,
receipt or recovery behavior changes; SQLite/fakes cannot certify PostgreSQL.

[#390](https://github.com/aaweaver-actuary/tempo/issues/390)'s exact reviewed
deletion cohort and retained #382/#386 qualification, plus #359/#267/#369
actual-authority/history/normal-launcher/cutover gates, remain distinct. This
packet neither changes their current status nor replaces their evidence. No live
Study/database, migration, table/data deletion, secrets, host settings, deployment,
CI, merge or full-system test is authorized by this candidate.

Validation for this documentation-only patch: source/hash/definition/link
correspondence, conflict scan and whitespace checks; no runtime tests. Architecture
impact: none — all actual interfaces, diagrams, schema relationships and runtime
ownership remain unchanged. The tables above describe current coupling and a
future preservation condition; they do not claim optional-feature/resource
isolation or deployed independent Learning authority.

[calculator]: ../../../../backend/app/services/introduction_priorities.py
[worker]: ../../../../backend/app/services/postgres_priority.py
[namespace]: ../../../../database/tempo_legacy_database/adapters/introduction_priorities/__init__.py
[inputs]: ../../../../database/tempo_legacy_database/adapters/introduction_priorities/inputs.py
[jobs]: ../../../../database/tempo_legacy_database/adapters/introduction_priorities/jobs.py
[publications]: ../../../../database/tempo_legacy_database/adapters/introduction_priorities/publications.py
[admission]: ../../../../database/tempo_legacy_database/adapters/queue_refresh/application/admission.py
[openings]: ../../../../database/tempo_legacy_database/adapters/queue_refresh/openings.py
[reads]: ../../../../database/tempo_legacy_database/adapters/queue_refresh/reads.py
[selector]: ../../../../backend/app/study/openings/capacity_frontier.py
[freshness]: ../../../../database/tempo_legacy_database/ddl/migrations/035_canonical_publication_freshness.sql
[sqlite-freshness]: ../../../../database/tempo_legacy_database/ddl/canonical_scope_schema.py
[refresh]: ../../../../backend/app/services/refresh_requests.py
[completion]: ../../../../backend/app/services/durable_tasks.py
[preparation]: ../../../../database/tempo_legacy_database/adapters/priority_lifecycle/preparation.py
[retention]: ../../../../database/tempo_legacy_database/adapters/priority_lifecycle/retention.py
[retention-app]: ../../../../backend/app/services/priority_retention.py
[learning-frozen]: ../../../../services/learning/runtime/src/tempo_learning_runtime/study/frozen_order/policy.py
[learning-review]: ../../../../database/tempo_learning_database/adapters/review_adapter.py
