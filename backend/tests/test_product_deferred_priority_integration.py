"""Concrete retained-history intake and independent Study pickup, no live data."""

from threading import Event

from test_product_game_lapse_postgres import owned_postgres, case as current_card, NOW
from test_study_encounter_intake_boundary import (
    representative,
    state,
    without_games_producer,
    assert_one_again_preserves_human_history,
)


def test_real_shape_history_intake_commits_pending_then_study_refresh_without_games(
    current_card, monkeypatch
):
    from app.product_composition.encounters import receive_admitted_encounter
    from app.product_composition import feedback_maintenance
    from app.services import durable_tasks, queue_priority_promotion
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore,
    )
    from tempo_legacy_database.adapters.study.game_feedback_authority import (
        current_owner_encounter_intake,
    )

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    stopped = Event()
    with representative(current_card) as (capture, snapshot, command):
        with current_card.transaction() as database:
            before = state(database, current_card)
            assert len(before["reviews"]) == len(before["receipts"]) == 9
            assert len(before["protected"]) == 25

        def unavailable(*args, **kwargs):
            raise AssertionError("History intake attempted queue preparation")

        with monkeypatch.context() as intake_guard:
            intake_guard.setattr(
                queue_priority_promotion, "prepare_priority_promotion", unavailable
            )
            intake_guard.setattr(
                PostgresGameFeedbackStore, "promote_encounter", unavailable
            )
            with current_card.transaction() as database:
                database.execute_native("SET LOCAL transaction_timeout='250ms'")
                accepted = receive_admitted_encounter(
                    database,
                    command=command,
                    capture=capture,
                    snapshot=snapshot,
                    operation_stopped=stopped.is_set,
                    clock=lambda: NOW,
                )
                assert (
                    accepted.observation_recorded
                    and accepted.reason == "first_departure_qualification_pending"
                )
                assert accepted.progress is None
                assert (
                    accepted.admission_status == "pending"
                    and accepted.available_by is None
                )
            with current_card.transaction() as database:
                replay = receive_admitted_encounter(
                    database,
                    command=command,
                    capture=capture,
                    snapshot=snapshot,
                    operation_stopped=stopped.is_set,
                    clock=lambda: NOW,
                )
                assert (
                    replay.owner_receipt_id == accepted.owner_receipt_id
                    and replay.outcome == "duplicate"
                )
                assert state(database, current_card) == before
                assert (
                    PostgresGameFeedbackStore(database).get(command.key).status
                    == "pending"
                )

        # Exercise the actual wake's discovery/claim and returned payload shape.
        def background_write(operation, **kwargs):
            with current_card.transaction() as database:
                return operation(database)

        monkeypatch.setattr(durable_tasks, "_now", lambda: NOW)
        monkeypatch.setattr(durable_tasks, "submit_background_write", background_write)
        monkeypatch.setattr(
            feedback_maintenance, "background_read_connection", current_card.transaction
        )
        monkeypatch.setattr(
            feedback_maintenance,
            "connection",
            lambda **kwargs: current_card.transaction(),
        )
        with without_games_producer(monkeypatch):
            claimed = feedback_maintenance.claim_feedback_sweep()
            assert (
                claimed is not None
                and "payload" in claimed
                and "payload_json" not in claimed
            )
            assert (
                feedback_maintenance.execute_feedback_sweep(
                    claimed,
                    read=current_card.transaction,
                    write=current_card.transaction,
                    capture=lambda *_: capture,
                    clock=lambda: NOW,
                )
                is False
            )
        with current_card.transaction() as database:
            receipt = current_owner_encounter_intake(
                database,
                retained_owner_capture=capture,
                snapshot=snapshot,
                clock=lambda: NOW,
            ).encounter_status(command.key)
            assert (
                receipt.admission_status == "available"
                and receipt.owner_receipt_id == accepted.owner_receipt_id
            )
            assert (
                database.execute_native(
                    "SELECT position FROM daily_queue WHERE card_id=%s AND queue_date=%s",
                    (current_card.card_id, NOW.date().isoformat()),
                ).fetchone()[0]
                == 1
            )
            effect = PostgresGameFeedbackStore(database).get(command.key)
            assert (
                effect.encounter == command and effect.disposition == "introduced_again"
            )
            after = assert_one_again_preserves_human_history(
                database, current_card, before, effect
            )
            assert (
                database.execute_native(
                    "SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s",
                    (current_card.card_id,),
                ).fetchone()[0]
                == 1
            )
        # Repeating the stale sweep cannot consume another observation or rewind history.
        assert (
            feedback_maintenance.execute_feedback_sweep(
                claimed,
                read=current_card.transaction,
                write=current_card.transaction,
                capture=lambda *_: capture,
                clock=lambda: NOW,
            )
            is False
        )
        with current_card.transaction() as database:
            assert state(database, current_card) == after
