"""Synthetic actual-scale protected promotion; existing disposable PG owner only."""

import json
import time

from test_product_game_lapse_postgres import (
    NOW,
    owned_postgres,
    case,
    current_encounter_case,
    _qualify_case,
)
from tempo_legacy_database.adapters import postgres_store


def scaled_queue(case, count=805):
    captured, snapshot, command = current_encounter_case(case, unintroduced=False)
    day = NOW.date().isoformat()
    with case.transaction() as database:
        database.execute_native(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type)
            SELECT c.id||':'||n,c.repertoire_id,c.kind,c.start_fen,c.moves_json,c.state,c.due_date,
                c.introduced_at,c.revision,c.interval_days,c.fsrs_card_json,c.first_correct_at,c.stability,'tactic'
            FROM cards c CROSS JOIN generate_series(0,%s) n WHERE c.id=%s""",
            (count - 2, case.card_id),
        )
        database.execute_native(
            """INSERT INTO daily_queue(queue_date,card_id,position,admission_kind)
            SELECT %s,%s||':'||n,n,'review' FROM generate_series(0,%s) n""",
            (day, case.card_id, count - 2),
        )
        database.execute_native(
            """INSERT INTO daily_queue(queue_date,card_id,position,admission_kind)
            VALUES(%s,%s,%s,'review')""",
            (day, case.card_id, count - 1),
        )
        database.execute_native(
            """UPDATE queue_attempt_origins SET issued_as_head=1
            WHERE queue_entry_id IN (SELECT id FROM daily_queue WHERE queue_date=%s AND position<25)""",
            (day,),
        )
        database.execute_native(
            """INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
            internal_rating,guided,source_kind,source_ref)
            SELECT %s,'correct',%s,10,20,'good',0,'study',%s||':scale:'||n FROM generate_series(1,8) n""",
            (case.card_id, NOW.isoformat(), case.card_id),
        )
    # Queue/history setup follows the initial single-card preparation. Obtain
    # current owner proof for this exact final fixture shape before intake.
    _qualify_case(case, captured, command)
    return captured, snapshot, command


from contextlib import contextmanager
import pytest
from app.services.queue_priority_promotion import (
    prepare_priority_promotion,
    StalePriorityPromotion,
)
from app.study.encounter_feedback import EncounterIntroductionPolicy
from tempo_legacy_database.adapters.study.game_feedback_authority import (
    current_owner_encounter_feedback,
)
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore


@contextmanager
def scale(case, count):
    capture, snapshot, command = scaled_queue(case, count)
    try:
        yield capture, snapshot, command
    finally:
        with case.transaction() as database:
            database.execute_native(
                "DELETE FROM daily_queue WHERE card_id=%s OR card_id LIKE %s",
                (case.card_id, case.card_id + ":%"),
            )
            database.execute_native(
                "DELETE FROM cards WHERE id LIKE %s", (case.card_id + ":%",)
            )


def receiving(database, capture, snapshot, plan, case):
    return current_owner_encounter_feedback(
        database,
        retained_owner_capture=capture,
        snapshot=snapshot,
        clock=lambda: NOW,
        introductions=None,
        introduction_policy=EncounterIntroductionPolicy("encounter_exception"),
        prepared_priority=plan,
        prepared_decisions=case.qualified.prepared.prepared_decisions,
    )


def readback(database, case):
    repertoire = case.qualified.command.decision.repertoire_id
    return dict(
        card=dict(
            database.execute_native(
                "SELECT * FROM cards WHERE id=%s", (case.card_id,)
            ).fetchone()
        ),
        reviews=[
            tuple(row)
            for row in database.execute_native(
                "SELECT * FROM reviews WHERE card_id=%s ORDER BY id", (case.card_id,)
            ).fetchall()
        ],
        protected=[
            tuple(row)
            for row in database.execute_native(
                """SELECT row_to_json(queue)::text,row_to_json(origin)::text
            FROM queue_attempt_origins origin JOIN daily_queue queue ON queue.id=origin.queue_entry_id
            WHERE origin.card_id LIKE %s AND origin.issued_as_head=1 ORDER BY origin.queue_entry_id""",
                (case.card_id + ":%",),
            ).fetchall()
        ],
        source=dict(
            game=dict(
                database.execute_native(
                    "SELECT * FROM imported_games WHERE id=%s",
                    (case.lapse.key.game_id,),
                ).fetchone()
            ),
            publication=tuple(
                database.execute_native(
                    "SELECT * FROM opening_graph_publications WHERE repertoire_id=%s",
                    (repertoire,),
                ).fetchone()
            ),
            repertoire=tuple(
                database.execute_native(
                    "SELECT * FROM repertoires WHERE id=%s", (repertoire,)
                ).fetchone()
            ),
            lines=[
                tuple(row)
                for row in database.execute_native(
                    "SELECT * FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id",
                    (repertoire,),
                ).fetchall()
            ],
            graphs=[
                tuple(row)
                for row in database.execute_native(
                    "SELECT * FROM opening_graph_steps WHERE repertoire_id=%s ORDER BY generation,line_id,decision_index",
                    (repertoire,),
                ).fetchall()
            ],
            memberships=[
                tuple(row)
                for row in database.execute_native(
                    "SELECT * FROM repertoire_cards WHERE card_id=%s ORDER BY repertoire_id",
                    (case.card_id,),
                ).fetchall()
            ],
            qualified=tuple(
                database.execute_native(
                    "SELECT source_key,version,payload::text FROM product_feedback_sources WHERE source_key=%s",
                    (case.qualified.source.source_key,),
                ).fetchone()
            ),
        ),
    )


@pytest.mark.parametrize("queue_count", [805, 1005])
def test_native_scaled_prepared_promotion_meets_budget_and_preserves_original_history(
    case, monkeypatch, queue_count
):
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with scale(case, queue_count) as (capture, snapshot, command):
        with case.transaction() as database:
            before = readback(database, case)
        with case.transaction() as database:
            accepted = receiving(
                database, capture, snapshot, None, case
            ).accept_qualified(
                command, qualification=case.qualified.prepared.qualification
            )
            assert accepted.admission_status == "pending"
        plan = prepare_priority_promotion(
            NOW.date().isoformat(), case.card_id, read=case.transaction
        )
        assert plan is not None and len(plan.changes) == queue_count - 25
        with case.transaction() as database:
            database.execute_native("SET LOCAL transaction_timeout='250ms'")
            database.execute_native("SET LOCAL statement_timeout='250ms'")
            started = time.perf_counter()
            receipt = receiving(
                database, capture, snapshot, plan, case
            ).refresh_encounter(command.key)
            elapsed = (time.perf_counter() - started) * 1000
            print("SCALED_RECEIVE_MS", queue_count, round(elapsed, 2))
            assert (
                receipt.admission_status == "available"
                and receipt.disposition == "introduced_again"
            )
        with case.transaction() as database:
            after = readback(database, case)
            assert len(before["reviews"]) == 9 and len(before["protected"]) == 25
            assert (
                len(after["reviews"]) == 10
                and after["reviews"][:-1] == before["reviews"]
            )
            assert (
                after["protected"] == before["protected"]
                and after["source"] == before["source"]
            )
            scheduling_fields = {
                "due_date",
                "interval_days",
                "fsrs_card_json",
                "first_correct_at",
                "reinforcement_pending",
                "stability",
                "guided_review",
                "state",
                "scheduling_mode",
                "hard_correct_streak",
                "recent_attempts_json",
            }
            assert {
                key: value
                for key, value in after["card"].items()
                if key not in scheduling_fields
            } == {
                key: value
                for key, value in before["card"].items()
                if key not in scheduling_fields
            }
            assert after["card"]["fsrs_card_json"] != before["card"]["fsrs_card_json"]
            assert (
                database.execute_native(
                    "SELECT position FROM daily_queue WHERE card_id=%s", (case.card_id,)
                ).fetchone()[0]
                == 25
            )
            assert (
                database.execute_native(
                    "SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s",
                    (case.card_id,),
                ).fetchone()[0]
                == 1
            )
            effect = PostgresGameFeedbackStore(database).get(command.key)
            from app.study.game_feedback import effect_id

            assert effect.progress.scheduling_review_id == effect.review_id
            assert tuple(
                database.execute_native(
                    "SELECT internal_rating,guided,source_kind,source_ref FROM reviews WHERE id=%s",
                    (effect.review_id,),
                ).fetchone()
            ) == ("again", 0, "game_lapse", effect_id(command.key))
            assert (
                database.execute_native(
                    "SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",
                    (case.card_id,),
                ).fetchone()[0]
                == 1
            )
            original = effect.encounter
            replay = receiving(
                database, capture, snapshot, plan, case
            ).accept_qualified(
                command, qualification=case.qualified.prepared.qualification
            )
            assert (
                replay.outcome == "duplicate"
                and replay.owner_receipt_id == receipt.owner_receipt_id
            )
            assert (
                PostgresGameFeedbackStore(database).get(command.key).encounter
                == original
                == command
            )
            assert readback(database, case) == after


@pytest.mark.parametrize("changed", ["position", "issued", "eligibility"])
def test_native_scaled_stale_preparation_preserves_intake_and_rolls_back_refresh_cursor(
    case, monkeypatch, changed
):
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with scale(case, 805) as (capture, snapshot, command):
        with case.transaction() as database:
            accepted = receiving(
                database, capture, snapshot, None, case
            ).accept_qualified(
                command, qualification=case.qualified.prepared.qualification
            )
            assert accepted.admission_status == "pending"
        plan = prepare_priority_promotion(
            NOW.date().isoformat(), case.card_id, read=case.transaction
        )
        cursor = case.card_id + "-scaled-cursor"
        with case.transaction() as database:
            if changed == "position":
                database.execute_native(
                    "UPDATE daily_queue SET position=9999 WHERE card_id=%s",
                    (case.card_id + ":26",),
                )
            elif changed == "issued":
                database.execute_native(
                    "UPDATE queue_attempt_origins SET issued_as_head=1 WHERE card_id=%s",
                    (case.card_id + ":26",),
                )
            else:
                database.execute_native(
                    "UPDATE cards SET archived=1 WHERE id=%s", (case.card_id + ":26",)
                )
            database.execute_native(
                """INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,
                payload_json,next_attempt_at,created_at,updated_at) VALUES(%s,'game_feedback_history',%s,1,'leased',
                '{"next_index":0}',%s,%s,%s)""",
                (cursor, cursor, NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
            )
            before = readback(database, case)
        with pytest.raises(StalePriorityPromotion, match="presentation_changed"):
            with case.transaction() as database:
                database.execute_native("SET LOCAL transaction_timeout='250ms'")
                database.execute_native(
                    "UPDATE background_tasks SET payload_json=%s WHERE id=%s",
                    (json.dumps({"next_index": 1}), cursor),
                )
                receiving(database, capture, snapshot, plan, case).refresh_encounter(
                    command.key
                )
        with case.transaction() as database:
            assert (
                PostgresGameFeedbackStore(database).get(command.key).status == "pending"
            )
            assert (
                database.execute_native(
                    "SELECT payload_json FROM background_tasks WHERE id=%s", (cursor,)
                ).fetchone()[0]
                == '{"next_index":0}'
            )
            assert (
                database.execute_native(
                    "SELECT gameplay_priority_reason FROM daily_queue WHERE card_id=%s",
                    (case.card_id,),
                ).fetchone()[0]
                is None
            )
            assert readback(database, case) == before
