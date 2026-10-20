# Learning runtime adapters

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [endgame_store.py](endgame_store.py) | Saved endgame template and immutable receipt reads; no calculation/provider. |
| [postgres_opening_evidence.py](postgres_opening_evidence.py) | Bounded shadow persistence. No card, review, quota or queue mutation lives here. |
| [opening_progression.py](opening_progression.py) | Existing Learning-owned completed-practice predicate and legacy-child update, moved intact. |
| [tactic_admission.py](tactic_admission.py) | Existing Learning-owned transaction-scoped tactic admission lock, moved intact. |
| [postgres_store.py](postgres_store.py) | Learning-owned SQL compatibility adapter; no connection/commit authority. |
| [scheduling_review_store.py](scheduling_review_store.py) | Scheduling SQL adapter borrowing an existing caller-owned connection. |

- [queue_position_lock.py](queue_position_lock.py): existing queue-position advisory lock boundary.
