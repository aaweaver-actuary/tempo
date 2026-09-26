"""Regressions for per-repertoire counts and game-position review."""

import json
import threading
import time
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.activity_gate import activity_gate
from app.services.database_executor import database_writer
from app.services.durable_tasks import claim_task, enqueue_task, requeue_interrupted_tasks
from app.services.repertoire_game_refresh import execute_repertoire_game_refresh_slice


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
FEN_KEY = " ".join(START.split()[:4])


def _seed_repertoire(db, repertoire_id: str, card_id: str):
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)", (repertoire_id, repertoire_id, "test.pgn", now))
    db.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)",
               (f"line-{repertoire_id}", repertoire_id, repertoire_id, "white", START, json.dumps(["e2e4"]), now))
    db.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(?,1,?)", (repertoire_id, now))
    db.execute("INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,'opening','white')",
               (card_id, repertoire_id, START, date.today().isoformat()))
    db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (repertoire_id, card_id))
    db.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,segment_kind,
                  decision_fen_keys_json,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
                  VALUES(?,1,?,0,'prefix',?,?,?,?,?,'white')""",
               (repertoire_id, f"line-{repertoire_id}", json.dumps([FEN_KEY]), card_id, FEN_KEY, START, json.dumps(["e2e4"])))


def test_repertoire_statistics_shared_prefixes_and_first_valid_daily_study_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_repertoire(db, "first", "shared")
            _seed_repertoire(db, "second", "shared")
            reviews = [("again", "2026-09-24T12:00:00+00:00", None),
                       ("correct", "2026-09-24T13:00:00+00:00", None),
                       ("correct", "2026-09-25T12:00:00+00:00", None),
                       ("again", "2026-09-25T13:00:00+00:00", "2026-09-25T14:00:00+00:00")]
            for rating, timestamp, invalidated_at in reviews:
                db.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,invalidated_at) VALUES('shared',?,?,0,1,?)",
                           (rating, timestamp, invalidated_at))
        for repertoire_id in ("first", "second"):
            response = client.get(f"/api/repertoires/{repertoire_id}/statistics?window=all")
            assert response.status_code == 200, response.text
            statistics = response.json()
            assert statistics["prefix"] == {"total": 1, "active": 1, "studied": 1, "unseen": 0, "locked": 0, "paused": 0}
            assert statistics["study"] == {"correct": 1, "attempts": 2, "accuracy": 0.5}
            assert statistics["games"]["adherence"] is None


def test_repertoire_statistics_primary_game_decisions_and_position_navigation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_repertoire(db, "first", "first-card")
            _seed_repertoire(db, "second", "second-card")
            now = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('game','lichess','test',?,'rapid',1,'white','1-0',?,'[\"e2e4\"]')", (now, START))
            for repertoire_id, primary in (("first", 1), ("second", 0)):
                db.execute("INSERT INTO game_repertoire_matches(game_id,repertoire_id,is_primary,classification,updated_at) VALUES('game',?,?,'covered',?)", (repertoire_id, primary, now))
            db.execute("INSERT INTO repertoire_decision_events(id,game_id,repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at) VALUES('event','game','first','first-card',4,?,'e2e4','e2e4','success',?,?)", (FEN_KEY, now, now))
            db.execute("INSERT INTO repertoire_decision_events(id,game_id,repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at) VALUES('untrained','game','first',NULL,6,?,'e2e4','d2d4','miss',?,?)", (FEN_KEY, now, now))
            db.execute("INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci) VALUES('game',0,?,'e2e4')", (FEN_KEY,))
            db.execute("INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci) VALUES('game',4,?,'e2e4')", (FEN_KEY,))
        first = client.get("/api/repertoires/first/statistics").json()
        second = client.get("/api/repertoires/second/statistics").json()
        assert first["games"]["adherence"] == 1
        assert first["games"]["decisions"] == 1
        assert first["games"]["positions_seen"] == 1
        assert second["games"]["adherence"] is None
        positions = client.get("/api/repertoires/first/statistics/positions").json()
        assert positions["positions"][0]["sample_ply"] == 4
        assert positions["positions"][0]["games"] == 1
        supporting = client.get("/api/games/summary", params={"fen": START, "repertoire_id": "first"}).json()
        assert supporting["total"] == 1
        assert supporting["games"][0]["matched_position_ply"] == 4
        assert client.get("/api/games/summary", params={"fen": START, "repertoire_id": "second"}).json()["total"] == 0


def test_repertoire_statistics_unlock_forecast_and_paused_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_repertoire(db, "first", "parent")
            db.execute("UPDATE cards SET state='mature' WHERE id='parent'")
            db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES('child','first','response',?,'[\"e2e4\"]','locked',?)", (START, date.today().isoformat()))
            db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('first','child')")
            db.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,segment_kind,
                         decision_fen_keys_json,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color)
                         VALUES('first',1,'line-first',1,'decision',?,'child','parent',?,?,'[\"e2e4\"]','white')""",
                       (json.dumps([FEN_KEY]), FEN_KEY, START))
        summary = client.get("/api/repertoires/first/statistics").json()
        assert summary["unlocks"][0]["earliest_unlock_date"] == date.today().isoformat()
        with database.connection() as db:
            db.execute("UPDATE cards SET pending_validation=1 WHERE id='parent'")
        paused = client.get("/api/repertoires/first/statistics").json()
        assert paused["unlocks"][0]["status"] == "paused"
        assert paused["unlocks"][0]["earliest_unlock_date"] is None
        with database.connection() as db:
            db.execute("UPDATE cards SET pending_validation=0,state='learning',introduced_at=? WHERE id='parent'", (date.today().isoformat(),))
        forecast = client.get("/api/repertoires/first/statistics").json()["unlocks"][0]
        assert forecast["status"] == "forecast"
        assert forecast["earliest_unlock_date"] >= date.today().isoformat()


def test_repertoire_game_refresh_yields_to_foreground_and_replays_once_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    database_writer.start()
    try:
        with database.connection() as db:
            for game_id in ("one", "two"):
                db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES(?,'lichess','test',?,'rapid',1,'white','1-0',?,'[\"e2e4\"]')",
                           (game_id, datetime.now(timezone.utc).isoformat(), START))
        enqueue_task("repertoire_game_refresh", "all", {"after_game_id": ""})
        first = claim_task("repertoire_game_refresh")
        assert first is not None
        completed = threading.Event()

        def run_slice():
            execute_repertoire_game_refresh_slice(first)
            completed.set()

        with activity_gate.foreground():
            worker = threading.Thread(target=run_slice)
            worker.start()
            time.sleep(0.05)
            assert not completed.is_set()
            with database.read_connection() as db:
                assert db.execute("SELECT COUNT(*) FROM imported_games").fetchone()[0] == 2
        worker.join(timeout=2)
        assert completed.is_set()
        assert execute_repertoire_game_refresh_slice(first) is False
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM game_derivation_jobs").fetchone()[0] == 1
        # Reclaiming after a restart keeps the queued cursor and adds the second game once.
        requeue_interrupted_tasks()
        second = claim_task("repertoire_game_refresh")
        assert second is not None
        assert execute_repertoire_game_refresh_slice(second) is True
        with database.connection() as db:
            assert [row[0] for row in db.execute("SELECT game_id FROM game_derivation_jobs ORDER BY game_id")] == ["one", "two"]
    finally:
        database_writer.stop()


def test_repertoire_positions_paginate_without_repeating_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_repertoire(db, "first", "card")
            now = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('game','lichess','test',?,'rapid',1,'white','1-0',?,'[\"e2e4\"]')", (now, START))
            db.execute("INSERT INTO game_repertoire_matches(game_id,repertoire_id,is_primary,classification,updated_at) VALUES('game','first',1,'covered',?)", (now,))
            positions = [f"position-{index} w - -" for index in range(21)]
            db.execute("UPDATE opening_graph_steps SET decision_fen_keys_json=? WHERE repertoire_id='first'", (json.dumps(positions),))
            for ply, position in enumerate(positions):
                db.execute("""INSERT INTO repertoire_decision_events(id,game_id,repertoire_id,card_id,ply,fen_key,
                              expected_uci,actual_uci,outcome,played_at,updated_at)
                              VALUES(?,'game','first','card',? ,?,'e2e4','d2d4','miss',?,?)""",
                           (f"event-{ply}", ply, position, now, now))
        first_page = client.get("/api/repertoires/first/statistics/positions?window=all&limit=20").json()
        second_page = client.get("/api/repertoires/first/statistics/positions", params={"window": "all", "limit": 20, "cursor": first_page["next_cursor"]}).json()
        assert first_page["total"] == 21
        assert len(first_page["positions"]) == 20
        assert len(second_page["positions"]) == 1
        assert {row["fen_key"] for row in first_page["positions"]}.isdisjoint({row["fen_key"] for row in second_page["positions"]})
        assert client.get("/api/repertoires/first/statistics/positions?cursor=bad").status_code == 422


def test_repertoire_statistics_gets_are_query_only_and_within_foreground_read_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_repertoire(db, "first", "card")
        before = database.DB_PATH.read_bytes()
        started = time.perf_counter()
        for path in ("/api/repertoires", "/api/repertoires/first/statistics",
                     "/api/repertoires/first/statistics/positions"):
            response = client.get(path)
            assert response.status_code == 200, response.text
        assert time.perf_counter() - started < 1.0
        assert database.DB_PATH.read_bytes() == before
