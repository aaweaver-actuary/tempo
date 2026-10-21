# Legacy backend adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [database_executor.py](database_executor.py) | Single-owner SQLite writer with foreground-priority scheduling. |
| [discovery_acceptance_store.py](discovery_acceptance_store.py) | PostgreSQL Discovery acceptance adapter; never opens or completes a transaction. |
| [defensive_analysis.py](defensive_analysis.py) | Existing defensive settings read and SQL admission/recommendation predicates; task policy and HTTP errors stay with application composition. |
| [frozen_order.py](frozen_order.py) | Frozen-order reads, staging/activation and conditional rollback on the caller's transaction; [Study policy and composition](../../../backend/app/study/frozen_order/README.md). |
| [legacy_game_evidence_scope.py](legacy_game_evidence_scope.py) | Native accepted-scope exclusion owned by feedback persistence. |
| [opening_progression.py](opening_progression.py) | Existing completed-practice SQL predicate and legacy-child update, moved intact. |
| [tactic_admission.py](tactic_admission.py) | Existing daily-introduction count and PostgreSQL transaction-scoped admission lock, moved intact. |
| [postgres_store.py](postgres_store.py) | PostgreSQL connection and SQL compatibility seam for the cutover. |
| [real_game_feedback.py](real_game_feedback.py) | Feedback persistence and legacy queue effects on a borrowed transaction. |
| [real_game_miss_projection.py](real_game_miss_projection.py) | Persistence-only miss projections shared by explicit queue read adapters. |
| [repertoire_lines.py](repertoire_lines.py) | Existing authored-line snapshot query; [Studies conflict computation](../../../backend/app/studies/README.md) lives outside database. |
| [scheduling_review_store.py](scheduling_review_store.py) | Legacy adapter exports; Learning owns the shared implementation. |
| [snapshot_reads.py](snapshot_reads.py) | Existing SQL snapshot/evidence capture helper, relocated intact with native values, C collation, batch and digest behavior preserved. |

- [queue_position_lock.py](queue_position_lock.py): existing queue-position advisory lock boundary.
- [postgres_readiness.py](postgres_readiness.py): existing PostgreSQL/worker readiness adapter.

[Command persistence](commands/README.md) groups the existing account/activity/annotation/statistics/endgame/teaching operations. [Control/read projections](controls/README.md) groups settings, review reconciliation, opening evidence and coverage operations. Their application call-site composition and transaction authority stay outside these helpers.

[Legacy Games command storage](games/README.md) groups game/sync/finding/analysis lease/publication operations. Existing legacy shared-card/queue/feedback coupling remains disclosed at its original composition callers; this move adds no owner permissions or Study dependency.


[Repertoire and tactical projections](projections/README.md) own complete comparison input/publication, statistics and tactical-opportunity queries. Existing application algorithms, cache/state and connection composition remain outside database.


[Queue provenance and lifecycle storage](lifecycle/README.md) owns retained-attempt facts, refresh publication/intents and metric persistence/buffering. Existing recovery, coalescing, clocks and finite metric policy remain application decisions.


[Foreground settings and authoring controls](foreground_controls/README.md) own existing settings/provider-account persistence, main-repertoire locks/edits and prefix-split reads/writes on the original command transaction. Validation and follow-up choices remain in their application owners.


[Redis admission and policy storage](redis_control/README.md) owns the original cached clients, control keys/Lua/pipelines and Study-mode query. Lifecycle, clocks, identity, cancellation, local fallback and policy validation stay at their existing application composition points.


[Derived publications](derived_publications/README.md) contains the next-opponent profile and daily-chess-statistics reads and guarded writes. Existing time/eligibility/math/insight/presentation decisions remain in their application compositions.


[Scheduling storage](scheduling/README.md) owns existing engine/durable turn-state, candidate SQL and dialect/translation mechanics. Class/turn/fairness decisions remain in their original application compositions; lease and turn persistence share the caller's transaction.


[Repertoire scope and saved moves](repertoire_scope/README.md) contains existing generation/freshness SQL, game-refresh queries and bounded line/digest/dialect operations. Application freshness validation, chess/deadline/cursor and task policy remain outside.


[Authored repertoire commands](authored_repertoires/README.md) contains existing canonical-prefix/branch reads, locks and writes. Payload/chess/HTTP validation, revision/route/callback policy and responses remain in the original command compositions.


[Statistics storage](statistics/README.md) contains existing game-feature/dimension projections, daily snapshot/insight writes and job claims. Math/chess/time-zone/day/threshold and response decisions remain in the original application source; the whitelisted projection is captured once before its clock/read scope.


[Authored exercise attempts](study_attempts/README.md) holds existing attempt/replay/card/queue/exercise/review/burial operations. Grading, guided/self-assessed outcomes, dates, requeue and refresh composition stay outside database; legacy Studies content and Study practice boundaries remain distinct.


[Bounded prefix reads](prefix_reads/README.md) contains existing diagnostic graph/evidence projections and the complete native-typed transition state capture, preserving one read-only MVCC scope, query/size/payload order and resource/stale-link guards. Algorithms, typed responses and existing planning authority remain unchanged outside database.


[Introduction-priority storage](introduction_priorities/README.md) holds complete coverage/personal-evidence/card/settings projections, priority job claims and generation publication operations. Scoring, bounded read/page/chunk loops, clocks, foreground yielding, stale-generation decisions and callbacks remain in the existing application owner.
