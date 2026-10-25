# Exact withdrawal candidate and issued-work disposition

**Documentation only; source current main `ff8a7dc99d9be4f4d644e7d8fa285957d5eb196f`.**
The canonical candidate is `adaptive-priority-stop-new-01` appended to
[RETIREMENT-BATCHES.json](../RETIREMENT-BATCHES.json). Its seven future edit paths
and source hashes correspond to [WITHDRAWAL.patch.txt](WITHDRAWAL.patch.txt).
The patch is text for review: it has **not** been applied to production sources.
It is 23 removed / 2 added lines across seven paths, zero deleted files, and no
implemented source savings. All six 1,407-line core paths remain necessary for
the retained readers, compatibility bindings and possible issued-work recovery.

This adds an actionable first-stage diff to the [source contract](README.md).
It does not approve retirement, resolve actual pending state or assert that a
producer shutdown happened. No schedule, registration, handler, command, live
control, queue, data or runtime behavior changes in this PR.

## Exact producer boundary and mechanical change

The proposed first stage withdraws **external callbacks requesting new priority
work**. It deliberately retains internal recovery re-enqueue, existing workers,
publication, retention, status and saved-score readers. It therefore cannot be
called an immediate stop of all recalculation or a finite drain.

| Source entry / immediate registered origin | Exact future edit; surviving effects |
| --- | --- |
| `canonical_prefix_api.save_prefix`: `repertoire.canonical_prefix.save`, `PUT /api/repertoires/{identifier}/canonical-prefix` | Remove only the priority import and call at 62/67. Retain prefix revision/source-epoch advance, coverage request, original pointer/legacy invalidation and opportunity callback. After invalidation no fresh priority is promised; ordinary current-row/no-score fallback remains. Never expose stale rows to compensate. |
| `coverage_maia_commands.submit_maia_node`: `coverage.maia.submit`, `POST /api/repertoire-coverage/maia/submit` | Remove priority import/call at 19/100 only. Keep lease/scope validation, candidate aggregation, completion/progress, operation receipt and opportunity callback. Heartbeat/release/failure commands do not directly enqueue priorities. |
| `postgres_coverage_explorer._publish_node`: `coverage_explorer` slice | Remove priority import/call at 19/120. Keep source/task/node fences, source publication and checkpoint; opportunity callback stays with its owner. |
| `repertoire_coverage.execute_coverage_node` and `submit_maia_coverage`: legacy coordinator coverage claim and the SQLite branch of the same Maia-submit HTTP route | Remove the imports/calls at 550–552 and 706–708, plus the resulting empty `if repertoire_id` at 705. Keep coverage completion/progress, error handling and opportunity callbacks. |
| `postgres_game_priorities.execute_game_priority_handoff_slice`: registered `game_derivation_priorities`, issued after features reach phase 6 | Remove priority import/call at 15/37 only. Keep one-repertoire cursor advance, job/task generation fences, opportunity call at 38 and final derivation completion. Removing the handler would strand accepted game derivation. |
| `game_sync_coordinator._enqueue_game_repertoire_refreshes`: legacy derivation's `enqueueing_priorities` phase | Remove its priority import/call at 31/146 only. Keep legacy derivation phases, matching-repertoire/opportunity work and completion. |
| `learning_review_adapter.LearningReviewAdapter.refresh_priority`: shared review application calls through `ReviewStore`; `cards.review` / `cards.review.reconcile`, `POST /api/cards/{identifier}/review[/reconcile]`, and local review invocation | Replace only this legacy adapter's optional advisory effect with the explicit void return shown in the patch. Preserve the shared method signature, review transaction, schedule/history/receipt, progression/ordinary queue effects and the independent Learning adapter's local advisory notification. This is an explicit effect retirement requiring the decision below; no enqueue function is changed to return a fabricated generation or success receipt. |

Coverage-request ingress remains `coverage.refresh.request` at
`POST /api/repertoires/{identifier}/coverage/refresh`; native seed/explorer and
legacy coverage completion reach the callbacks above. Source/settings/authoring
can request coverage too. Games enter via `games.sync.enqueue` and
`games.exclusion.set`, then sync/derivation/features/handoff. These upstream
commands remain supported; the seven immediate callback edits cut their priority
effect without retiring their unrelated commands or source facts. No separate
priority HTTP refresh route, `priority.*` registered command or dedicated
priority Celery task was found. Main's two enqueue imports are unused there and
are omitted from this minimum diff.

## Exact execution, schedule and recovery registrations to retain

| Location | Registration / cadence and withdrawal disposition |
| --- | --- |
| `backend/app/celery_app.py:31–58` | Generic `app.tasks.poll_background_tasks` / `execute_background_slice` route to background. Beat polls durable tasks every 1 second, recovers operations every 5 seconds and active coverage every 30 seconds; `ensure_daily_queue` every 60 seconds is essential queue work. No priority-specific beat item exists. Keep the schedules: deleting a shared poll would affect Study and other accepted work. |
| `backend/app/tasks.py:143,320–343,356–469` | `_SUPPORTED_BACKGROUND_KINDS` contains `repertoire_priority`; `_execute_claimed_background_slice` maps it to `execute_repertoire_priority_slice` and excludes it from generic completion because the slice owns settlement. Study-only polling admits only `daily_queue`; already-delivered optional slices defer at execution. Keep all these bindings until the tail has a separately approved disposition. |
| `backend/app/services/durable_scheduling.py:9–18` | Shared scheduling turn `priority` includes `repertoire_priority`, `repertoire_opportunity`, `priority_retention`, daily statistics and discovery recommendation. Keep the family/identifiers; they are not recurring priority producers by themselves. |
| `backend/app/services/game_sync_coordinator.py:331–338,358–387,452–464,513–521` | Local coordinator startup requeues interrupted jobs; its round-robin `priority` source claims queued priority jobs, then load/compute/publish. Keep source/claim/process-pool publication and restart recovery while legacy work may exist. Do not remove only its registration/import. |
| `backend/app/main.py:1225`; `tasks.py:133,378` | Legacy and native `priority_retention` registrations stay. Enqueue/publication schedules retention at priority 200. Retention performs bounded cleanup, not score calculation, and protects published/current generations. |
| `main.py:939–955,990–1004`; `activity_commands.py` | Preserve `activity.control` and `activity.task.retry`, plus `/api/system/activity/control` and `/api/system/tasks/{task_id}/retry`. Controls pause/resume/promote known rows; they do not delete or settle them. Task retry clears terminal failure/attempt budget and resets pause; it can restart optional computation when admission permits. |
| `main.py:247–282`; `tasks.py:302–319` | Preserve operation status, original-identity blocked retry and `recover_operations`. Recoverable queued/retrying/expired-executing source commands are redelivered with original saved payload. Complete receipts replay saved outcomes; blocked/failed/pending are not ordinary automatic drain successes. |

Keep the neutral epoch/version binding, both scoring constants, lifecycle
publication/current-job imports and opportunity completion lock/fence/publication
contracts listed in [README.md](README.md). None is a withdrawal edit. Diagnostics,
activity sources, schema/views, exports, saved priorities, ranks and queue/history
remain retained and truthful.

## Decisions that source cannot supply

**D1 — issued priority tail: drain or hold.** The selected Study-only/frozen
requirements establish that Study must proceed without waiting for this work.
They do not authorize enabling optional admission to finish it, canceling it, or
declaring it complete. If the actual authority is already Study-only, preserve
that hold; do not switch it off just to drain. A reviewer/operator must select
normal admitted recovery versus explicit hold for actual outstanding work.

**D2 — not-yet-applied source operations at the withdrawal boundary.** The seven
callback sites do not distinguish a new source command from an older saved
command first executing/retrying after deployment. The candidate patch retires
their optional priority effect for **both**, while retaining their primary
source/review result and receipt. This includes the explicit void legacy review
hook and future prefix invalidation without replacement scores. If old unexecuted
commands must still produce priorities, do not apply those callback hunks until
that source-command tail is settled or explicitly held. There is no existing
issuance-version cutoff to claim otherwise; adding one is outside this candidate.

**D3 — downstream opportunity tail.** Retain all four priority-owner callbacks:
`introduction_priorities.execute_priority_refresh:809`,
`publish_priority_records:898`, `postgres_priority.execute_repertoire_priority_slice:190`
and game handoff `postgres_game_priorities:38`. The seven-path patch removes only
the neighboring priority call at game handoff 37. Opportunity cessation therefore
stays contingent on observed priority/downstream settlement or a selected hold.
No independent deletion of these callbacks is proposed.

## Every issued-work category: existing settlement or explicit hold

The table describes **existing** recovery behavior, not actions performed here.
Hold means preserve rows, original identities and handlers without claiming
completion. Pause is not a new terminal state or a hard fence for an in-flight
publisher.

| Issued category / state | Admitted recovery / completion | Hold / unresolved condition |
| --- | --- | --- |
| Legacy `repertoire_priority_jobs`: queued | `claim_priority_refresh` conditionally changes queued→running, increments attempts and clears pending-since only after claim. Coordinator loads/calculates then `publish_priority_records` publishes only matching running generation. | Existing Study-only claim predicate or `background_activity(source='priority', work_id=repertoire_id).paused` prevents a later claim. No generation/receipt deletion. |
| Legacy running or process-pool calculation | Current job/generation check rejects stale publication; accepted final transaction publishes pointer, completes job, schedules retention, records refresh publication and enqueues opportunity. Startup `requeue_interrupted_priorities` returns running→queued for replay. | Pause does not revoke computation already running or fence final publication. Preserve the original process/connection/task owner until it stops or settles; no automatic quiescence claim. |
| Legacy current coordinator load/compute/publish exception | `_run` catches/logs priority exceptions at 466–485, but does not requeue that priority job there (the special requeue branch is integrity only). The job may remain running until existing startup recovery or another enqueue. | This path does not use `execute_priority_refresh`'s compatibility retry loop. A drain cannot be promised from its presence. Do not restart a live coordinator or invent a retry just for this packet; report the affected running ledger to its recovery owner. |
| Legacy failed / compatibility execution | `execute_priority_refresh`'s retained compatibility failure path records an error and, after its attempt threshold, leaves failed rather than silently completing; the current coordinator uses the separate path above. | Activity control rejects failed priority rows. There is no direct legacy-priority manual-retry handler in this packet. Preserve the failure; parent must decide re-scope/repair if it must drain. A new/changed refresh request can otherwise advance a generation, which is not a terminal settlement. |
| Legacy complete / stale staged rows | Complete job and pointer/evidence remain history/serving facts. Existing retention can reclaim only superseded generated rows. | Keep pointer/current rows even when there is no currently queued work. No inference of empty state from complete status. |
| Native `background_tasks(kind='repertoire_priority')`: queued/retrying | Generic poll claims with task lease; payload retains repertoire/job generation and cursor. Preparation/staging uses source→job→task locks, durable ordinals and bounded batches. | Existing Study-only native polling excludes it; `source='durable', work_id=task_id` pause blocks later claims. A priority-ledger pause is not a substitute for the durable-task pause. |
| Native leased / already broker-delivered / expired lease | Execution checks foreground/mode/current delivery. Foreground or Study-only deferral preserves payload/phase and returns to retrying; expired lease reclamation is conditional and preserves generation. Current work stages/finalizes under its lease. Old deliveries cannot publish for a replacement lease. | A lease remains real until the existing fence rejects it or the owner settles/defers it. A per-item pause affects claims, not an arbitrary already-running publication. Do not purge the broker or clear leases to assert a hold. |
| Native `preparing` manifest, partial prepared rows, cursor-zero/nonzero staging | Existing worker reuses ready preparation, computes/checkpoints missing work, validates counts/source/scoring/order and advances generic slices without inventing completion. Final transaction publishes pointer/completes job, schedules retention, completes slice and records accepted refresh/opportunity effects. | Source/scoring mismatch, old `source_signature`, unverifiable nonzero cursor or old ordering can invoke enqueue again at worker lines 62,70,80,95,107,138,156,159. **Issued recovery can create replacement generations; no finite drain deadline follows.** Keep manifests/rows and both generations' fences. |
| Native failed / contention / transaction deadline | Generic failure budget retains failed state and error; explicit task Retry can reset it. Contention/foreground defers without consuming ordinary retry budget; deadline episodes retain backoff/checkpoint. | Do not auto-retry failures or remove settings/foreground controls for retirement. Native task failure and priority-job status are separate ledgers; require paired observation before calling work settled. |
| Native complete or stale task delivery | Handler's fenced final slice is authoritative. An old delivery returns no accepted new publication; completed task alone does not certify the refresh input was accepted. | Preserve completed/error/event facts and compare accepted input identity, not only task counts. |
| `analysis_refresh_requests` for priority | Existing enqueue compares input identity; unchanged useful work or accepted publication coalesces. Claim clears `pending_since`; accepted publication alone updates `published_input_version`. | Pending intent with no job/task is not proven satisfied. No dedicated periodic priority-intent repair scan was found; a later enqueue can recreate required work. Clearing pending or stamping published to retire it would fabricate acceptance. |
| `priority_retention` task / preparation and staged rows | At most the existing 16-row slice; protect published/active generation and queued/running preparation. Generic handler settles/requeues according to existing return value. | Study-only also holds this optional kind. Retention is not permission to delete active job, pointer or source epoch. Retained active preparations may prevent full cleanup. |
| Upstream coverage, game derivation and foreground source commands | Keep lease/phase/operation handlers. After D2 approval, the seven-path patch lets their primary accepted work finish without requesting new priority work; opportunity side stays separate. | If D2 requires original optional effects, their delayed commands/callbacks remain a source of new work until explicitly settled/held. Coverage Maia submit is not on the Study-only background callback allowlist; heartbeat/release/failure are. Do not block essential review/reconcile to force quiescence. |
| Downstream opportunity task / accepted discovery or browser recovery | Remains with [opportunity PR #429](https://github.com/aaweaver-actuary/tempo/pull/429); source epoch lock, generation/lease completion, input-version acceptance, saved eligibility, train/accept/status and admitted recovery stay. | Preserve all four callbacks above. Opportunity storage's own intermediate advance increments generation; generic durable priority slice advance does not. Do not confuse those fences or declare a stale opportunity completion accepted. |
| Broker envelopes, browser pending reviews/source operations, blocked operations | Retain original task/operation identifiers and existing replay/reconciliation paths; completed receipts return their original results. No direct browser adaptive-priority request was found, but source commands can carry the downstream effect. | Unknown serialized payloads, pending browser storage, reserved/in-flight deliveries and old builds cannot be disproved by source scanning. Do not clear, rewrite, reissue with new IDs or label them complete. |

## Minimal read-only authority observation checklist — not executed

An authorized authority owner can collect the following summaries. This is an
observation request, not a connection script or permission to access live state.
Use existing least-privilege read access and bounded consistent snapshots; do not
initialize the application or call enqueue/source-version helpers to inspect it
(`source_version` can insert an epoch row). Keep sensitive payloads, game/PGN
content, credentials, URLs with tokens and raw browser storage out of reports.

| Observation | Minimum non-secret output; decision it settles |
| --- | --- |
| Serving/launch authority for each actual device and normal start/restart/update | Authority type (legacy SQLite/native or independent Learning), deployed artifact/revision and schema version, expected workspace-match boolean, registered launcher target. This selects the serving row in README; source/Compose presence alone is insufficient. Do not start/update/restart anything or invoke an issuance/reconciliation GET. |
| Existing admission/frozen controls | Study-only boolean, control availability, frozen run/reader compatibility and rank-count summary, counts of paused priority-ledger and durable-task items. Inspect native and legacy owners separately. Do not change a control or fetch a secret-bearing environment dump. |
| Saved serving facts | Counts of current pointer-selected generation rows, current legacy fallback rows, stale-hidden rows and unranked/legacy-protected cards; schema/reader versions. Use aggregate scoped facts, not moves or identities. This disproves an unsupported empty-storage assumption and confirms the serving fallback to preserve. |
| Issued priority ledgers and preparation | By state/phase: counts, max age, expired/current lease counts, job/task generation-match counts, manifests by status/scoring/ordering version, prepared/staged expected-count mismatch counts. Pair ledgers using an owner-local opaque key, reporting only aggregate/hash correspondence. Determines which table rows above need D1 disposition. |
| Intent/publication identity | Counts of priority/opportunity intents whose input differs from published input, pending-since present/absent, missing required job/task, pointer/job/preparation mismatches. Read raw epoch tables without insert/locks; compare two bounded observations to see whether source identity changes. No clock/wakeup/enqueue call. |
| Original commands and transport work | Counts/states/ages for `cards.review`, `cards.review.reconcile`, `repertoire.canonical_prefix.save`, `coverage.maia.submit`, coverage request, Games source commands and relevant activity retry/control operations. Broker owner reports scoped ready/reserved/unacknowledged/in-flight/redelivery counts and known-kind correspondence without consuming/acking/purging. Unreadable or unclassifiable work remains unknown. Settles D2 and delayed-delivery risk. |
| Original browser/Study/history obligations | Owner-local counts/types and original-identity correspondence for pending browser review/source envelopes and issued/exposed attempts; minimal queue/review/history preservation summary. Do not export payloads or modify local storage. Confirms that primary Study recovery is preserved rather than traded for optional drain. |
| Opportunity tail and handoff | Opportunity owner reports retained tail category/state totals, whether any of the four callbacks can still run, and accepted-publication identity correspondence. Record exact source/artifact inputs for existing #382/#386/#359/#267/#369 receipts. This resolves D3/authority correspondence; observations alone do not approve deletion or live cutover. |

No one snapshot proves future quiescence while ingress or recovery can enqueue.
The owner must state the observed time window and the selected D1/D2/D3 policy;
an unknown count remains unknown. A later implementation needs its owning checks,
including native proof if transaction/recovery behavior changes. This documentation
adds no runtime tests, new scheduler, cancellation state or migration framework.

Coordination receipt: opportunity owner `01a173c5-ad62-73bd-acc4-13d72eb2b0e3`
confirmed retained callbacks/identity contracts and the non-finite recovery tail
in #429, final communicated head `26544be0a8e3adf18701996efc9649ffd146288d`,
base `ff8a7dc99d9be4f4d644e7d8fa285957d5eb196f`. That owner edits only its
`opportunity-feed-refresh-01` manifest entry and Discovery FEATURES row; this
packet edits only its adaptive entry and adaptive-ranking FEATURES row. Integration
must preserve both appended candidates and all eight prior cohorts/top metadata.
No other-owner candidate is copied or represented as approved.
