# Learning runtime adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [card_deletion.py](card_deletion.py) | Existing deleted-card fact on the caller’s transaction; application conflict translation stays in Learning composition. |
| [frozen_order.py](frozen_order.py) | Bounded frozen-order reads, activation/exposure facts and SQL predicates; [policy/composition](../../../services/learning/runtime/src/tempo_learning_runtime/study/frozen_order/README.md). |
| [real_game_miss_projection.py](real_game_miss_projection.py) | Existing Learning-owned miss query, using remediation’s public predicate without a new connection or writer. |
| [queue_attempt_origins.py](queue_attempt_origins.py) | Retained queue-origin/current-definition reads and recovered completion write; caller transaction preserved. |
| [study_bootstrap.py](study_bootstrap.py) | Existing Study settings/progress/accepted-route reads and settings writes; [application decisions/composition](../../../services/learning/runtime/src/tempo_learning_runtime/study/README.md). |
| [teaching_states.py](teaching_states.py) | Existing card lock order and idempotent teaching-state writes/reads; validation stays outside database. |
| [durable_tasks.py](durable_tasks.py) | Original task enqueue/event/create-or-lock/fenced claim SQL; [essential-work decisions/composition](essential-work/README.md). |
| [maintenance.py](maintenance.py) | Existing task fences, retained operation retry and queue/day/projection writes; caller transaction/lifecycle remain outside database. |
| [evidence_receipts.py](evidence_receipts.py) | Historical delivery receipt and accepted revision frontier reads; receipt authority/conflicts remain in runtime intake. |
| [study_attempts.py](study_attempts.py) | Existing Study exercise completion facts and finalization/queue/sibling writes; [attempt decisions/composition](practice-attempts/README.md). |
| [study_attempt_commands.py](study_attempt_commands.py) | Existing Study identity/card/queue/attempt locks, source facts and answer insertion; grading/replay/conflicts stay in application. |
| [tactic_commands.py](tactic_commands.py) | Existing puzzle/receipt/progress/card/queue/result persistence; light/guided scheduling decisions stay in application. |
| [queue_application.py](queue_application.py) | Existing queue availability/presentation/window facts, requeue writes and pending-projection SQL; [queue decisions/composition](queue/README.md). |
| [queue_commands.py](queue_commands.py) | Existing queue command facts, dialect/locks, issuance/failure/burial and task/projection storage; conflicts/refresh decisions stay in runtime. |
| [review_service.py](review_service.py) | Existing defense replay facts/writes, queue provenance/positioning storage and priority projection; [review decisions/composition](reviews/README.md). |
| [review_reconciliation.py](review_reconciliation.py) | Existing review schedule/history/snapshot/receipt/card operations; chronological/fallback decisions and clocks stay outside database. |
| [admission.py](admission.py) | Existing complete admission facts, card/queue locks, quotas/routes and writes; [authority decisions/composition](admission-review/README.md). |
| [review_adapter.py](review_adapter.py) | Existing review facts, dialect lock operations and completion/receipt/evidence writes; contract/authority/deferred decisions stay in runtime. |
| [endgame_store.py](endgame_store.py) | Saved endgame template and immutable receipt reads; no calculation/provider. |
| [postgres_opening_evidence.py](postgres_opening_evidence.py) | Bounded shadow persistence. No card, review, quota or queue mutation lives here. |
| [opening_progression.py](opening_progression.py) | Existing Learning-owned completed-practice predicate and legacy-child update, moved intact. |
| [tactic_admission.py](tactic_admission.py) | Existing Learning-owned transaction-scoped tactic admission lock, moved intact. |
| [postgres_store.py](postgres_store.py) | Learning-owned SQL compatibility adapter; no connection/commit authority. |
| [scheduling_review_store.py](scheduling_review_store.py) | Scheduling SQL adapter borrowing an existing caller-owned connection. |

- [queue_position_lock.py](queue_position_lock.py): existing queue-position advisory lock boundary.
