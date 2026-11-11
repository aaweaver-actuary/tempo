# Retained backend composition

This directory contains Study, Tactics and repertoire services. Queue selection
and issuance use `queue_application.py`, `queue_next_card.py` and
`postgres_queue_refresh.py`; review effects use `learning_review_adapter.py` and
`review_service.py`. Durable commands and bounded queue progress use
`durable_tasks.py`, `background_wakes.py` and `queue_refresh_wakeup.py`.
Repertoire import and integrity and Tactics retain their existing owners.

`queue_next_card.prepare_queue_head` opens the existing read scope and prepares
the current position source before the writer. The five helpers in
`tempo_storage.study.next_exercise_reads` borrow its supplied connection; they
open and commit no additional transaction. Issuance retains the existing writer
and fences. See [Study](../study/README.md).

Minimal [Game Review](../games/README.md) reads saved games and authored lines;
it has no Study feedback callback. Provider acquisition and automatic feedback
are retired in this source. Historical records and original identities are
preserved; this description does not assert a migration or installation.
