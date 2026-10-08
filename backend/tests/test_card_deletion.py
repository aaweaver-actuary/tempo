"""Permanent deletion and retained scheduling contracts for SQLite compatibility."""
from datetime import date
import json
import sqlite3

import chess
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import database, main
from app.card_deletion import (
    RETAINED_REPERTOIRE_ID, cancel_repertoire_tasks, delete_repertoire_data,
    permanent_delete_card, omit_deleted_graph_steps,
)
from app.services.durable_tasks import enqueue_task_in_transaction
from app.services.opening_graph import GraphInput, build_graph
from app.models import ReviewRequest
from app.review_conflicts import ReviewConflict


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "deletion.db")
    database.initialize()
    with database.connection() as connection:
        for identifier in ("old", "other"):
            connection.execute("INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?, ?,?)", (identifier, identifier.title(), "fixture", "2026-10-01", int(identifier == "other")))
        for identifier, introduced, archived in (("learned", "2026-10-01", 0), ("unlearned", None, 0), ("archived", "2026-10-01", 1), ("shared", "2026-10-01", 0)):
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,archived,interval_days) VALUES(?,'old','prefix',?,'[\"e2e4\"]','mature','2026-11-01',?,?,7)", (identifier, chess.STARTING_FEN, introduced, archived))
            connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('old',?)", (identifier,))
            if introduced:
                connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct','2026-10-01',0,7)", (identifier,))
            connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_repertoire_id) VALUES(?,?,?,'old')", (date.today().isoformat(), identifier, len(identifier)))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('other','shared')")
    yield


def test_repertoire_delete_ignores_unrelated_null_job_payload_and_fences_exact_tasks(workspace):
    with database.connection() as connection:
        exact = enqueue_task_in_transaction(connection, "opening_graph_rebuild", "old", {"repertoire_id": "old"})
        unrelated = enqueue_task_in_transaction(connection, "coverage", "other", {"repertoire_id": "other", "position": "old\0position", "nested": {"repertoire_id": "old"}})
        connection.execute("UPDATE background_tasks SET state='leased',lease_token='stale' WHERE id=?", (exact["id"],))
        cancel_repertoire_tasks(connection, "old")
        assert tuple(connection.execute("SELECT state,phase,lease_token FROM background_tasks WHERE id=?", (exact["id"],)).fetchone()) == ("complete", "cancelled", None)
        assert connection.execute("SELECT state FROM background_tasks WHERE id=?", (unrelated["id"],)).fetchone()[0] == "queued"


@pytest.mark.parametrize("policy", ["keep", "delete"])
def test_repertoire_delete_policies_preserve_shared_history_and_non_main_selection(workspace, policy):
    with database.connection() as connection:
        shared_before = dict(connection.execute("SELECT * FROM cards WHERE id='shared'").fetchone())
        queue_before = tuple(connection.execute("SELECT id,position FROM daily_queue WHERE card_id='shared'").fetchone())
        delete_repertoire_data(connection, "old", policy)
        assert connection.execute("SELECT is_main FROM repertoires WHERE id='other'").fetchone()[0] == 1
        assert connection.execute("SELECT 1 FROM cards WHERE id='unlearned'").fetchone() is None
        shared = dict(connection.execute("SELECT * FROM cards WHERE id='shared'").fetchone())
        assert shared == {**shared_before, "repertoire_id": "other"}
        assert tuple(connection.execute("SELECT id,position FROM daily_queue WHERE card_id='shared'").fetchone()) == queue_before
        assert connection.execute("SELECT COUNT(*) FROM reviews WHERE card_id='shared'").fetchone()[0] == 1
        assert connection.execute("SELECT 1 FROM deleted_cards WHERE card_id='shared'").fetchone() is None
        with pytest.raises(ReviewConflict) as delayed:
            main._apply_review("unlearned", ReviewRequest(outcome="correct"), database=connection)
        assert delayed.value.code == "card_deleted"
        if policy == "keep":
            assert tuple(connection.execute("SELECT repertoire_id,interval_days,due_date,archived FROM cards WHERE id='learned'").fetchone()) == (RETAINED_REPERTOIRE_ID, 7, "2026-11-01", 0)
            assert connection.execute("SELECT archived FROM cards WHERE id='archived'").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM reviews WHERE card_id='learned'").fetchone()[0] == 1
        else:
            assert connection.execute("SELECT 1 FROM cards WHERE id='learned'").fetchone() is None
            assert connection.execute("SELECT 1 FROM reviews WHERE card_id='learned'").fetchone() is None
            assert connection.execute("SELECT 1 FROM deleted_cards WHERE card_id='learned'").fetchone()
    if policy == "keep":
        assert RETAINED_REPERTOIRE_ID in [item["id"] for item in main.list_repertoires()["repertoires"]]
        assert all(RETAINED_REPERTOIRE_ID not in [rep["id"] for rep in card["repertoires"]] for card in main.comparison_cards()["cards"])


def test_individual_permanent_deletion_removes_all_memberships_history_and_rejects_delayed_review(workspace):
    with database.connection() as connection:
        with pytest.raises(HTTPException, match="changed"):
            permanent_delete_card(connection, "shared", 2)
        assert permanent_delete_card(connection, "shared", 1) == {"deleted": True, "card_id": "shared"}
        for table, column in (("cards", "id"), ("reviews", "card_id"), ("repertoire_cards", "card_id"), ("daily_queue", "card_id")):
            assert connection.execute(f"SELECT 1 FROM {table} WHERE {column}='shared'").fetchone() is None
        assert set(dict(connection.execute("SELECT * FROM deleted_cards WHERE card_id='shared'").fetchone())) == {"card_id", "deleted_at"}
        with pytest.raises(ReviewConflict) as conflict:
            main._apply_review("shared", ReviewRequest(outcome="correct"), database=connection)
        assert conflict.value.code == "card_deleted"
        assert connection.execute("SELECT COUNT(*) FROM repertoires WHERE id IN ('old','other')").fetchone()[0] == 2
    database.initialize()  # Restart keeps the content exclusion.
    with database.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="permanently deleted"):
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES('shared','other','prefix',?,'[]','new','2026-10-08')", (chess.STARTING_FEN,))


def test_deleted_graph_prerequisites_preserve_source_and_usable_descendants(workspace):
    source = {"id": "line", "start_fen": chess.STARTING_FEN, "moves_json": json.dumps(["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]), "trained_color": "white"}
    steps = build_graph(GraphInput("old", (source,), 1))
    assert len(steps) == 3
    filtered = omit_deleted_graph_steps(steps, {steps[0].card_id, steps[1].card_id})
    assert len(filtered) == 1 and filtered[0].parent_card_id is None
    assert json.loads(source["moves_json"])[0] == "e2e4"


def test_retained_introduced_cards_survive_queue_refresh_and_next_day_without_a_review(workspace):
    from datetime import timedelta
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with database.connection() as connection:
        connection.execute("DELETE FROM reviews WHERE card_id='learned'")
        connection.execute("UPDATE cards SET state='learning',introduced_at=?,due_date=?,interval_days=0 WHERE id='learned'", (today, today))
        connection.execute("UPDATE settings SET new_cards_per_day=0,tactics_new_per_day=0,study_new_per_day=0 WHERE id=1")
        delete_repertoire_data(connection, "old", "keep")
        for study_day in (today, tomorrow):
            main.seed_queue(connection, study_day)
            assert tuple(connection.execute("SELECT state,introduced_at,due_date FROM cards WHERE id='learned'").fetchone()) == ("learning", today, today)
            assert connection.execute("SELECT 1 FROM daily_queue WHERE card_id='learned' AND queue_date=? AND status='queued'", (study_day,)).fetchone()


def test_card_archive_api_remains_compatible_and_permanent_requires_revision(workspace):
    from app.services.database_executor import database_writer
    database_writer.start()
    try:
        client = TestClient(main.app)
        assert client.delete("/api/cards/learned?permanent=true").status_code == 422
        assert client.delete("/api/cards/learned").json()["archived"] is True
        with database.connection() as connection:
            assert connection.execute("SELECT 1 FROM cards WHERE id='learned'").fetchone()
            assert connection.execute("SELECT 1 FROM deleted_cards WHERE card_id='learned'").fetchone() is None
    finally:
        database_writer.stop()


def test_retained_deck_is_protected_and_hidden_after_its_last_card_is_deleted(workspace):
    with database.connection() as connection:
        delete_repertoire_data(connection, "old", "keep")
        with pytest.raises(HTTPException, match="system repertoire"):
            delete_repertoire_data(connection, RETAINED_REPERTOIRE_ID, "delete")
    with pytest.raises(HTTPException, match="Repertoire not found"):
        main.make_main_repertoire(RETAINED_REPERTOIRE_ID)
    with database.connection() as connection:
        for identifier in ("learned", "archived"):
            permanent_delete_card(connection, identifier, 1)
        assert connection.execute("SELECT is_main FROM repertoires WHERE id='other'").fetchone()[0] == 1
    assert RETAINED_REPERTOIRE_ID not in [item["id"] for item in main.list_repertoires()["repertoires"]]


def test_automatic_tactic_admission_skips_deleted_content_without_consuming_allowance(workspace, monkeypatch):
    from app.services import tactical_catalog
    from app.services.cards import card_id
    from app.services.puzzles import validate_puzzle_record
    pack_id = "hangingPiece-easy-01"
    records = tactical_catalog.pack_records(pack_id)[:2]
    identifiers = [card_id(*validate_puzzle_record(record)) for record in records]
    monkeypatch.setattr(tactical_catalog, "pack_records", lambda _: records)
    today = date.today().isoformat()
    with database.connection() as connection:
        connection.execute("INSERT INTO deleted_cards(card_id,deleted_at) VALUES(?,?)", (identifiers[0], today))
        connection.execute("INSERT INTO tactic_pack_activation(pack_id,active) VALUES(?,1)", (pack_id,))
        connection.execute("UPDATE settings SET tactics_new_per_day=1 WHERE id=1")
        tactical_catalog.seed_tactical_introductions(connection, today)
        assert connection.execute("SELECT 1 FROM cards WHERE id=?", (identifiers[0],)).fetchone() is None
        assert connection.execute("SELECT card_id FROM tactic_progress").fetchone()[0] == identifiers[1]
        assert connection.execute("SELECT COUNT(*) FROM tactic_introductions").fetchone()[0] == 1


def test_import_does_not_report_deleted_content_as_created_or_shared(workspace, monkeypatch):
    import asyncio
    from io import BytesIO
    from starlette.datastructures import UploadFile
    from app.services.opening_graph import decision_segments
    moves = ["a2a4", "h7h6", "a4a5", "h6h5", "b2b4"]
    segments = decision_segments(chess.STARTING_FEN, moves, "white", 2)
    with database.connection() as connection:
        connection.executemany("INSERT INTO deleted_cards(card_id,deleted_at) VALUES(?,?)", [(segment.card_id, date.today().isoformat()) for segment in segments])
    monkeypatch.setattr(main, "enqueue_opening_graph_rebuild", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "enqueue_integrity_scans", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "enqueue_coverage_refresh", lambda *args, **kwargs: None)
    result = asyncio.run(main.import_pgn(UploadFile(BytesIO(b'[Event "Removed source"]\n\n1. a4 h6 2. a5 h5 3. b4 *'), filename="removed.pgn"), "white", 2, None))
    assert result.cards_created == result.prefix_cards_created == result.decision_cards_created == 0
    assert result.shared_prefixes_reused == result.shared_decisions_reused == 0
