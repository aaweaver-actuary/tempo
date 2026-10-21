<div id="title-block-header">

# Study protection boundary

</div>

- <a href="#tempo-study-protection-boundary--first-audit-pass"
  id="toc-tempo-study-protection-boundary--first-audit-pass">Tempo Study
  protection boundary — first audit pass</a>
  - <a href="#conclusions-that-change-the-deletion-plan"
    id="toc-conclusions-that-change-the-deletion-plan">Conclusions that
    change the deletion plan</a>
  - <a href="#source-level-serving-sequence"
    id="toc-source-level-serving-sequence">Source-level serving sequence</a>
  - <a href="#what-to-protect-first" id="toc-what-to-protect-first">What to
    protect first</a>
  - <a
    href="#first-module-splits-in-priority-order-after-safe-external-cuts"
    id="toc-first-module-splits-in-priority-order-after-safe-external-cuts">First
    module splits, in priority order after safe external cuts</a>
  - <a href="#decorators-and-abstractions"
    id="toc-decorators-and-abstractions">Decorators and abstractions</a>
  - <a href="#high-value-deletion-seams-outside-protected-study"
    id="toc-high-value-deletion-seams-outside-protected-study">High-value
    deletion seams outside protected Study</a>
  - <a href="#minimal-acceptance-before-a-deletion-batch"
    id="toc-minimal-acceptance-before-a-deletion-batch">Minimal acceptance
    before a deletion batch</a>
  - <a href="#outstanding-unknowns"
    id="toc-outstanding-unknowns">Outstanding unknowns</a>

# Tempo Study protection boundary — first audit pass

Baseline: `aaweaver-actuary/tempo`, main commit
`7d77ed1b25198dcd320e0265f777cc630f23775e`, inspected October 21, 2026.
This is static source analysis. No live process, live database, phone,
browser, build, or infrastructure test was inspected or executed. Source
files were not changed. The companion `study-reviewed.json` states
per-file review depth; this is not a claim that the entire repository
has been reviewed line by line.

## Conclusions that change the deletion plan

1.  **A small independent serving composition already exists.**
    `TEMPO_TARGET=study` selects `static/src/study-main.tsx`, which
    mounts `StudyConnectionBoundary` and the shared training controller.
    `scripts/study-composition.ts:5–17,31–44` rejects named Games,
    authoring, comparison, insights, and workspace administration roots
    in its bundle. The independent Learning image copies Learning, its
    review/remediation applications, scheduling/chess libraries,
    publication/evidence contracts, and their owned database packages.
    Its Dockerfile has no legacy backend, Redis, Celery, engine worker,
    or model runtime.
2.  **Current source does not identify the deployed composition.** The
    default `static/src/main.tsx` loads `legacy-home`, and root
    `docker-compose.yml` still describes the legacy
    API/Celery/Redis/engine stack. A directory named `backend/` is
    therefore not automatically dispensable. Before removing a legacy
    serving dependency, identify the actual desktop/phone build target,
    API origin, backend revision/image, database identity, and relevant
    launcher. This is a bounded read-only deployment check, not a
    requirement to redesign or migrate Study.
3.  **The phone paths differ materially.**
    `app/lib/offline-training.ts:59–82` retains only the first returned
    card in independent Study, while the legacy path retains the full
    prepared queue; lines 157–174 create local offline repeats only in
    the legacy path. Replacing a legacy deployment with independent
    Study solely to enable deletion could reduce the existing phone
    workflow. Preserve whichever behavior Andy currently uses; do not
    treat online desktop success as phone parity.
4.  **Remediation is in Study's current local transaction path.**
    `queue_commands.py:52–73` binds remediation when an attempt is first
    issued; `review_service.py:121–132` settles it;
    `queue_application.py:43–64` requests remediation with queue
    refresh; `teaching_commands.py:40–43` validates accepted definitions
    through its adapter. This is local Learning state, not a call to the
    Games service. Removing Games ingestion can be safe without removing
    the corresponding Learning obligations and receipts.
5.  **“No external producer dependency” does not mean “few tables.”**
    Study has durable review, issuance, teaching, recovery, frozen
    ordering, publication identity, and local obligation state. These
    exist partly to make phone/computer usage and lost-response recovery
    safe. Table presence alone is not evidence of bloat; a table may
    protect state without being directly visible in the UI.
6.  **The first large cuts should be outside the verified Study
    closure.** Preserve current Study behavior and schemas during the
    first deletion batches. The API and review adapters have clear
    future split opportunities, but starting with those would change the
    only functioning product before harvesting the safer legacy/optional
    reductions.

## Source-level serving sequence

| Step                                               | Source owner                                                                                                                                 | What actually happens                                                                                                                                                                                       | Protection implication                                                                                                                                      |
|----------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1\. Select frontend composition                    | `package.json:54`; `vite.static.config.ts:13–20`; `scripts/study-composition.ts:21–46`; `static/src/study-main.tsx:1–4`                      | Study target replaces the HTML entry and checks named optional module roots in emitted closure.                                                                                                             | Preserve target configuration and offline asset inventory. The guard is a named exclusion list, not a proof against all future optional dependencies.       |
| 2\. Connect to the correct authority               | `app/components/study-connection-boundary.tsx:7–58`; `app/lib/study-connection.ts:156–235`                                                   | Validates origin/workspace/credential, inspects retained local work, fetches bootstrap, rejects wrong authority, stores connection, reloads app lifetime.                                                   | Do not replace browser storage or change origin/workspace silently. Pending phone work is authority-bound.                                                  |
| 3\. Hydrate durable browser state                  | `app/components/study-storage-boundary.tsx:5–41`; `app/lib/study-session-runtime.ts:4–19`; `database/browser/study-intent-storage.ts`        | Training mounts only when intent storage is ready. One session owner adapts persisted command kinds to delivery.                                                                                            | Intent migration, storage-unavailable behavior, and recovery must survive cleanup.                                                                          |
| 4\. Load queue                                     | `app/views/fetchAndInitializeQueue.tsx:250–402`                                                                                              | Recovers prior commands/reviews, loads current queue, maps it to practice cards, checks that the authoritative head survived mapping/filtering.                                                             | Prefetched next rows are not automatically issued attempts. Filtering a pending head cannot authorize the next row.                                         |
| 5\. Authenticate and establish transaction context | `services/learning/runtime/src/tempo_learning_runtime/api.py:111–245`; `database/tempo_learning_database/connections.py:43–95`               | Scope/origin/credential gates precede database access; request accounting and bounded pool budgets apply.                                                                                                   | Keep security, workspace, transaction, and resource ownership explicit.                                                                                     |
| 6\. Refresh and issue queue head                   | Runtime `api.py:329–401`; `queue_commands.py:110–161,52–73`                                                                                  | Queue GET requests can write: dispatch `queue.ensure_current`, run two bounded maintenance assists, read projection, dispatch `queue.issue_head`.                                                           | Queue reads are not an independent read-only endpoint that can lose gateway/maintenance dependencies.                                                       |
| 7\. Select ordinary new cards                      | Runtime `maintenance.py:72–114`; `admission.py:19–125`; database adapter `admission.py:150–171`                                              | Durable task cursor chooses candidates; admission rechecks active definitions, due/locked state, current route, frozen identity, quota, duplication.                                                        | Preserve per-repertoire quota, introductions, current route, stored order. No Maia inference is required to read frozen ranks.                              |
| 8\. Present stable order                           | Runtime `queue_ordering.py:128–168`; `frozen_opening_order.py:16–46`; `maintenance.py:95–109`                                                | Seeded presentation spreads new/review and content families but preserves frozen new-opening relative rank and protected attempt positions.                                                                 | Do not reimplement order as a frontend shuffle or generate it afresh on reload.                                                                             |
| 9\. Record teaching and opening progress           | Runtime `teaching_commands.py:24–53`; `study_bootstrap.py:69–101`; frontend `review-outbox.ts:144–175`                                       | Teaching validates active executable revision; alternate moves use accepted definitions; completed review is retained before network dispatch.                                                              | Accepted publication content and relevant evidence records are serving inputs even when authoring is unavailable.                                           |
| 10\. Submit durable review                         | Runtime `api.py:439–471`; `gateway.py:30–99,166–170`; application `application.py:165–203`                                                   | Transport intent commits first, then a handler transaction applies required evidence and aggregate review, optionally issuing prepared continuation.                                                        | Do not introduce a decorator that commits each repository call separately. Transport identity and logical attempt identity protect different failure cases. |
| 11\. Apply schedule and settle                     | Application `application.py:47–162`; runtime `review_adapter.py:233–324,435–448`; `review_service.py:88–136`                                 | Validates current card/attempt/revision, applies scheduling, records history/receipt/origin, completes queue entry, requeues if needed, settles local obligations, records follow-up refresh before commit. | This is the core no-delete boundary. Preserve borrowed connection, card-before-queue lock order, and receipt atomicity.                                     |
| 12\. Recover or advance                            | Frontend `review-outbox.ts:234–361,363–515`; `packages/study-session/src/index.ts:42–129`                                                    | Checks original receipt or replays same immutable identity; persists settlement before advancing. Next-load retry uses separate progression checkpoint and does not resubmit grade.                         | Recovery code is active functionality, even when the happy path rarely exercises it.                                                                        |
| 13\. Continue on phone/offline                     | `app/lib/offline-training.ts:59–82,106–190,213–334`; `fetchAndInitializeQueue.tsx:458–536`; `app/lib/offline-shell.ts:18–58`; `public/sw.js` | Preserves prepared queue/attempts in IndexedDB, keeps original timestamp/revision, verifies app shell, replays on reconnect.                                                                                | Preserve saved work and current target behavior. Independent Study currently saves only the issued head; legacy can retain a larger offline queue.          |

## What to protect first

### Serving code and contracts

- Independent Learning:
  `services/learning/runtime/src/tempo_learning_runtime/**`,
  `services/learning/application/src/tempo_learning_review/**`,
  `services/learning/remediation/src/tempo_learning_remediation/**`,
  corresponding packaging and installed database inputs. Preserve the
  whole installed runtime initially; later remove optional endpoint
  families only after usage and pending-data checks.
- Owned storage: `database/tempo_learning_database/**` and
  `database/tempo_remediation_database/**`, including migration
  manifests, checksums, schemas, adapters and triggers. Directory names
  do not imply separate processes; the remediation adapter borrows
  Learning's transaction.
- Pure dependencies: `packages/scheduling`, `packages/chess-core`,
  `contracts/content-publication`, `contracts/game-evidence`. A contract
  for an optional producer is not the producer implementation.
- Browser durability: `packages/study-session`,
  `database/browser-intents`, relevant `database/browser/**`
  practice/connection/prepared-training/intent adapters, Study
  command/review/evidence outboxes, local/offline training and shell.
- Presentation path: current Study composition, `home_view.tsx`,
  training store/controller/views, board, settings, queue mapping and
  response schemas, worker computation, assets actually in the Study
  build. The current Home still statically imports tactics and saved
  endgame practice; treating these directories as optional-only would be
  wrong.
- **Conditional legacy protection:** any `backend/` and
  `database/tempo_legacy_database/` files still in the deployed Study
  call graph. The source default launchers give a concrete reason to
  check this before deletion.

### Data and database objects

For the first deletion phase, keep the complete active Learning and
remediation schema untouched. A table-level reduction should have its
own later inventory. Core examples that are visibly used by reviewed
source:

| Responsibility          | Current objects / state                                                                                                                                     | Why removing it could break Study                                                                                                                                                     |
|-------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Workspace/readiness     | `instance`, `migration_history`, `settings`                                                                                                                 | Every connection checks workspace/timezone and restricted role; readiness compares migration checksums.                                                                               |
| Content and progression | `cards`, `repertoires`, `repertoire_cards`, `repertoire_lines`, `opening_graph_publications`, `opening_graph_steps`, integrity blocks, deleted-card markers | Admission/queue/review require current executable definitions, current ownership and route eligibility.                                                                               |
| Queue                   | `daily_queue`, `daily_queue_days`, `queue_projections`, `queue_attempt_origins`                                                                             | Current queue, stable order, refresh state, introduction quota and issued-attempt recovery.                                                                                           |
| Review and teaching     | `reviews`, `review_attempt_receipts`, `review_schedule_snapshots`, `teaching_states`, schedule seeds                                                        | Replay, phone reconciliation, history and progression.                                                                                                                                |
| Frozen introductions    | `frozen_opening_runs`, `frozen_opening_ranks`, `frozen_opening_activation`                                                                                  | Persisted rank/epoch/fingerprint validation; these are not live Maia computation tables.                                                                                              |
| Accepted content        | `accepted_publications`, `serving_publications`, `content_card_aliases`, activation receipts                                                                | Stored accepted definitions are read during teaching, move membership and local obligation binding. The external Content service need not be running.                                 |
| Essential work          | `operation_receipts`, `background_tasks`, task events, logical request bindings                                                                             | Durable commands and bounded queue maintenance. The label “background” does not make queue refresh optional.                                                                          |
| Opening evidence        | `opening_evidence_*`                                                                                                                                        | Presentations, attempt identity, per-move progress and completion accompany ordinary opening study. Separate optional analysis from required submitted-evidence consistency.          |
| Local remediation       | `remediation_*`                                                                                                                                             | Locally bound obligations/attempts/settlements are touched without any producer call. Some tables can be cold while still guarding old pending effects.                               |
| Saved practice families | `tactic_*`, authored-study objects, accepted-defense objects, saved endgame templates/attempts                                                              | Current runtime and Home include these families. Which are used by Andy requires live read-only data/queue evidence; preserving saved cards differs from running optional generation. |

This table is a **conservative dependency map**, not a certified
exhaustive reachable-table set and not a statement that every listed
table is populated. No live row count, access-frequency counter or
database dependency catalog was obtained.

## First module splits, in priority order after safe external cuts

These are future organization proposals, not changes made to Study.

| Current file                                        | Actual responsibilities                                                                                                                                                                                                                       | Proposed logical owner(s)                                                                                                                                                | Constraints                                                                                                                                              |
|-----------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| Runtime `api.py` — 738 lines                        | Composition/lifecycle; authority/CORS/host/error middleware; bootstrap/settings; queue issuance/projections; aggregate review; opening evidence; authored exercises; tactics; defense; retired endgame replay; internal publications/evidence | `http/app.py` composition; `http/authority.py`; domain routers `study`, `queue`, `reviews`, `opening_evidence`, `practice_families`, `content_intake`, `evidence_intake` | Keep endpoint contract and handler/gateway identity unchanged. These are routers within one process, not new services.                                   |
| Runtime `review_service.py` — 227 lines             | Scheduling orchestration; defense schedule rebuild; queue order provenance; repeat placement; priority ordering                                                                                                                               | `reviews/submit.py`, `reviews/defense_replay.py`, `queue/order_receipts.py`, `queue/repeat.py`                                                                           | One transaction owns schedule, review, receipt, queue and settlement. Shared helper extraction should not create new authority.                          |
| Runtime `review_adapter.py` — 448 lines             | Request conversion; application port implementation; failure translation; borrowed storage effects; evidence; optional continuation; deferred queue/remediation writes                                                                        | `reviews/request_adapter.py`, `reviews/submission_store.py`, `reviews/followups.py`                                                                                      | The port implementation is already a useful seam. Avoid a class per tiny method or a generic repository that hides SQL strings.                          |
| Runtime `remediation_adapter.py` — 303 lines        | Accepted-definition projection; producer delivery; attempt binding; review settlement; candidate mapping; maintenance admission                                                                                                               | `content/accepted_definitions.py`, `remediation/intake.py`, `remediation/attempts.py`, `remediation/admission.py`                                                        | Teaching uses accepted definitions; those reads should not conceptually live under producer intake. Keep transaction borrowing and local data ownership. |
| Runtime `study_bootstrap.py` — 101 lines            | Study settings schema/read/write; tactic progress projection; opening alternate-move validation                                                                                                                                               | `study/settings.py`, `tactics/progress.py`, `openings/membership.py`                                                                                                     | Split by reason to change, not because each function needs a file. Current size is not itself a problem.                                                 |
| Runtime `queue_application.py` — 118 lines          | Presentation facts; repeat insertion; refresh scheduling; SQL compatibility delegates; queue response mapping                                                                                                                                 | `queue/read.py`, `queue/refresh.py`, `queue/repeat.py`; storage query helpers remain database-owned                                                                      | Do not keep exposing `_available_attempt_where_sql` through a domain facade as the long-term abstraction.                                                |
| `app/views/home_view.tsx` — 1,719 lines             | Board/training lifecycle, view selection, teaching, review persistence, pending recovery, notifications, appearance, offline transition, optional slots                                                                                       | Future `study/session` controller plus practice/board view, teaching controller, persistence/recovery controller                                                         | User asks not to touch working Study; record this debt now, refactor only under a distinct later decision.                                               |
| `app/views/fetchAndInitializeQueue.tsx` — 552 lines | Remote retry, pending command/review recovery, phone replay, response mapping, local queue hydration, offline preparation, notifications                                                                                                      | Future `study/queue/load`, `study/recovery`, `study/offline/prepare`, one small composition function                                                                     | Current function is operationally sensitive. Preserve cancellation/generation fencing and issued-head rule.                                              |
| `app/lib/review-outbox.ts` — 515 lines              | Intent creation/validation; receipt recovery; evidence fallback; review transport; reconciliation; flush scheduling; notifications                                                                                                            | Future `study/reviews/intent`, `delivery`, `recovery`, `flush`, with explicit notifier port                                                                              | Extract existing mechanism, do not create another generic command framework beside `study-session` and `browser-intents`.                                |

## Decorators and abstractions

Useful, small opportunities: consistent timing/error translation at HTTP
boundaries, explicit command registration, small context managers for an
already-owned transaction, one parameterized helper for genuinely
identical bounded adapter operations. These are justified only when
semantics truly match and the call remains readable.

Avoid a generic `@transactional` around each database method: the
current review flow deliberately borrows one connection so review
history, scheduling, queue completion, required settlement and operation
receipt commit together. Avoid a generic `@retry` around logical
commands unless it preserves immutable payload and original identity and
distinguishes terminal domain conflict from transient contention. Avoid
wrapping every line in abstractions simply to lower line count; reducing
navigation and hidden control flow matters more than textual DRYness.

## High-value deletion seams outside protected Study

1.  **Legacy whole-stack retirement:** if deployed Study is confirmed
    independent and rollback artifacts/data are retained, remove the
    now-unneeded legacy API/Celery/Redis/engine runtime closure and its
    exclusive adapters/tests/launchers. This has much larger leverage
    than shaving Learning code. Source-only evidence is insufficient to
    certify that condition today.
2.  **Optional producer retirement:** independent Games sync/evidence
    production, Analysis/Intelligence/engine generation can stop without
    becoming required by ordinary Learning queue consumption. Preserve
    accepted content/saved cards and local obligations. Decide
    deliberately whether each future feature is retired or merely paused
    before deleting its implementation.
3.  **Two serving targets:** make current supported target explicit in
    one short run/build guide and agent map. Do not leave default
    launcher documentation pointing to a retired stack. Delete obsolete
    target-specific tests with retired behavior only, retaining Study
    regression invariants.
4.  **Historical “reconstruction” scaffolding:** after identifying the
    supported package closure, remove exclusive qualification fixtures,
    duplicate compatibility adapters and obsolete wrappers with their
    retired stack. Keep the replay/data-protection tests that still
    describe current behavior.
5.  **Retired endgame generation:** the current Learning API already
    returns 410 for fresh probe/generation (`api.py:650–670`) but
    preserves saved attempt replay. That is a valid pattern for retiring
    computation while preserving durable records. Do not delete saved
    endgame attempts merely because generation is gone.

## Minimal acceptance before a deletion batch

Use existing owning suites and a small reproducible fixture; do not
introduce another broad qualification platform.

1.  Record desktop/phone target, serving revision, API origin and
    database identity; read only. This resolves whether the legacy path
    is still protected.
2.  Identify candidate files' reverse imports, dynamic registrations,
    build inputs, migration ownership and launcher references. Zero
    static callers is useful evidence, not final proof of unused
    dynamic/operational code.
3.  On an isolated copy/fixture, launch the same supported Study target.
    Load today's queue and introduce the next expected move from every
    active repertoire using persisted frozen order and quotas.
4.  Grade correct and wrong answers; inspect one authoritative
    review/schedule/receipt effect each; check expected repeat position
    and new-card progression. Preserve teaching state.
5.  Repeat identical transport request after response loss; verify no
    duplicate effect. Reuse logical attempt with changed answer and
    verify conflict.
6.  Reload the browser with pending review and with confirmed review but
    failed next-load. Ensure recovery does not re-grade or advance
    without confirmed settlement.
7.  Reproduce Andy's current phone prepared/offline/reconnect workflow
    with retained storage. Test actual current batch size/issuance
    behavior, not a replacement target with less capability.
8.  Check two-device revision/conflict handling and wrong-workspace
    rejection. This is relevant to existing phone/computer usage, not
    speculative multi-user infrastructure.
9.  Stop optional producers and confirm Study still loads, issues,
    reviews and progresses; bound shared resource use separately if the
    candidate includes operational changes.
10. Compare measured production-source/file/dependency reduction against
    baseline and update a concise boundary map. No schema drop in the
    first source-deletion batch.

## Outstanding unknowns

- Live desktop/phone target, service build and database identity.
- Whether actual phone use relies on legacy multi-card offline
  preparation.
- Populated tables, pending commands, durable historical obligations and
  server-installed migrations.
- Dynamic consumers and operators outside the repository.
- Which optional features Andy explicitly wants retired versus rebuilt
  later.
- Full transitive frontend build closure; the current read inspected
  entry/import chains and source guards without running the build.

The most important distinction is between retiring a producer, removing
its service code, and dropping state already accepted by Study. They are
separate changes with different evidence requirements.