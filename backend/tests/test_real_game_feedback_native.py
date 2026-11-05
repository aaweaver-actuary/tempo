"""Serving feedback projections and locks on the owned PostgreSQL fixture."""

import json

import pytest
import test_product_game_lapse_postgres as pg_fixture
from app.services.queue_admission_adapter import QueueAdmissionAdapter
from app.services.real_game_feedback import MISS_REASON
from app.services.real_game_feedback_store import BorrowedFeedbackStore
from psycopg.errors import LockNotAvailable
from tempo_legacy_database.adapters.real_game_feedback import BATCH_SIZE
from test_product_game_lapse import NOW

case = pg_fixture.case
owned_postgres = pg_fixture.owned_postgres


def seed_game(database, case):
    database.execute_native(
        """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
           result,start_fen,moves_json,repertoire_scope_generation)
           SELECT %s,'lichess','synthetic',%s,'rapid',1,'white','draw',start_fen,
                  moves_json,(SELECT generation FROM repertoire_game_scope WHERE id=1)
           FROM cards WHERE id=%s""",
        (case.lapse.key.game_id, NOW.isoformat(), case.card_id),
    )
    database.execute_native(
        """INSERT INTO game_derivation_jobs(game_id,status,derivation_version,
           published_repertoire_version,updated_at) VALUES(%s,'complete',1,1,%s)""",
        (case.lapse.key.game_id, NOW.isoformat()),
    )


def insert_miss(database, case, repertoire, ply):
    game = case.lapse.key.game_id
    event = f"{game}-{ply}"
    database.execute_native(
        """INSERT INTO repertoire_decision_events_staged(id,game_id,derivation_version,
           repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at)
           VALUES(%s,%s,1,%s,%s,%s,'synthetic','e2e4','d2d4','miss',%s,%s)""",
        (event, game, repertoire, case.card_id, ply, NOW.isoformat(), NOW.isoformat()),
    )
    return event


def seed_accepted_contribution(database, case):
    # A contribution persisted before the old HTTP bridge was retired still owns
    # the game/repertoire pair, including after the original delivery was withdrawn.
    key = case.lapse.key.game_id
    database.execute_native(
        """INSERT INTO remediation_incident_versions(workspace_id,incident_id,logical_hash,
           card_id,work_sequence,accepted_at) VALUES('legacy',%s,'stored',%s,1,%s)""",
        (key, case.card_id, NOW),
    )
    database.execute_native(
        """INSERT INTO remediation_contributions(workspace_id,obligation_id,obligation_revision,
           evidence_id,evidence_revision,game_id,incident_id,logical_hash,card_id,card_revision,
           publication_id,publication_generation,kind,occurred_at,payload_json,game_revision)
           VALUES('legacy',%s,1,%s,1,%s,%s,'stored',%s,1,'legacy-graph:white',1,
                  'real_game_miss',%s,%s,1)""",
        (
            key,
            key,
            key,
            key,
            case.card_id,
            NOW,
            json.dumps(
                {
                    "authority": {
                        "kind": "legacy_graph",
                        "workspace_id": "legacy",
                        "repertoire_id": "white",
                    }
                }
            ),
        ),
    )


def test_feedback_projection_and_priority_write_roll_back_together(case):
    day = NOW.date().isoformat()
    with case.transaction() as db:
        seed_game(db, case)
        db.execute_native(
            "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,0)",
            (day, case.card_id),
        )

    def assert_no_effect(observer):
        assert (
            observer.execute_native(
                "SELECT count(*) FROM repertoire_decision_events WHERE game_id=%s",
                (case.lapse.key.game_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            observer.execute_native(
                "SELECT gameplay_priority_reason FROM daily_queue WHERE queue_date=%s AND card_id=%s",
                (day, case.card_id),
            ).fetchone()[0]
            is None
        )

    with pytest.raises(RuntimeError, match="caller rollback"), case.transaction() as db:
        event = insert_miss(db, case, "white", 1)
        store = BorrowedFeedbackStore(db)
        rows = store.outstanding_cards((case.card_id, case.card_id, "missing"))
        assert [(row.card_id, row.state) for row in rows] == [(case.card_id, "mature")]
        assert (
            store.outstanding_card("missing") is None
            and store.outstanding_cards(()) == ()
        )
        with pytest.raises(ValueError, match="128"):
            store.outstanding_cards((case.card_id,) * (BATCH_SIZE + 1))
        assert list(store.missed_event_batches(case.lapse.key.game_id)) == [(event,)]
        assert [
            (row.event_id, row.card_id)
            for batch in store.repertoire_miss_batches("white")
            for row in batch
            if row.game_id == case.lapse.key.game_id
        ] == [(event, case.card_id)]
        db.execute_native(
            "UPDATE daily_queue SET gameplay_priority_reason='prior' WHERE card_id=%s",
            (case.card_id,),
        )
        store.replace_miss_priority(case.card_id, "prior", "new")
        assert (
            db.execute_native(
                "SELECT gameplay_priority_reason FROM daily_queue WHERE card_id=%s",
                (case.card_id,),
            ).fetchone()[0]
            == "new"
        )
        with case.transaction() as observer:
            assert_no_effect(observer)
        raise RuntimeError("caller rollback")
    with case.transaction() as observer:
        assert_no_effect(observer)


def test_feedback_card_and_day_locks_last_until_caller_commits(case):
    day = NOW.date().isoformat()
    keys = (f"tempo:card-edit:{case.card_id}", f"tempo:daily-queue-position:{day}")
    lock_sql = "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))"
    row_sql = "SELECT id FROM cards WHERE id=%s FOR UPDATE NOWAIT"
    with case.transaction() as db:
        store = BorrowedFeedbackStore(db)
        store.lock_card(case.card_id)
        store.lock_queue_day(day)
        with case.transaction() as observer:
            assert all(
                not observer.execute_native(lock_sql, (key,)).fetchone()[0]
                for key in keys
            )
            with pytest.raises(LockNotAvailable), observer.raw.transaction():
                observer.execute_native(row_sql, (case.card_id,))
    with case.transaction() as observer:
        assert all(
            observer.execute_native(lock_sql, (key,)).fetchone()[0] for key in keys
        )
        assert (
            observer.execute_native(row_sql, (case.card_id,)).fetchone()[0]
            == case.card_id
        )


def test_accepted_game_scope_preserves_other_repertoire_miss(case):
    day = NOW.date().isoformat()
    other = f"other-{case.card_id}"
    with case.transaction() as db:
        db.execute_native(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Other','fixture',%s)",
            (other, NOW.isoformat()),
        )
        db.execute_native(
            "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)",
            (other, case.card_id),
        )
        seed_game(db, case)
        insert_miss(db, case, "white", 1)
        store = BorrowedFeedbackStore(db)
        queue = QueueAdmissionAdapter(db)
        assert store.outstanding_card(case.card_id) and queue.has_outstanding_miss(
            case.card_id
        )
        seed_accepted_contribution(db, case)
        assert store.outstanding_card(
            case.card_id
        ) is None and not queue.has_outstanding_miss(case.card_id)
        assert all(
            row.game_id != case.lapse.key.game_id
            for batch in store.repertoire_miss_batches("white")
            for row in batch
        )
        db.execute_native(
            "UPDATE cards SET state='locked',introduced_at=NULL WHERE id=%s",
            (case.card_id,),
        )
        assert all(
            row["id"] != case.card_id
            for row in queue.opening_candidate_page(day, "").candidates
        )
        event = insert_miss(db, case, other, 2)
        assert store.outstanding_card(case.card_id) and queue.has_outstanding_miss(
            case.card_id
        )
        rows = [
            row
            for row in queue.opening_candidate_page(day, "").candidates
            if row["id"] == case.card_id
        ]
        assert rows and all(
            row["gameplay_priority_reason"] == MISS_REASON for row in rows
        )
        assert [
            row.event_id
            for batch in store.repertoire_miss_batches(other)
            for row in batch
        ] == [event]
        db.execute_native(
            "UPDATE imported_games SET adaptive_excluded=1 WHERE id=%s",
            (case.lapse.key.game_id,),
        )
        assert store.outstanding_card(
            case.card_id
        ) is None and not queue.has_outstanding_miss(case.card_id)
