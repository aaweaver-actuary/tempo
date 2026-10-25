import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tempo_legacy_database import connections as database
from app.main import app
from app.services.statistics import refresh_game_features
from app.games.records import GameRecord
from app.services.game_sync import _persist_game_once


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _game(db, game_id: str, result: str, color: str = "white", played_at: str | None = None):
    db.execute(
        """INSERT INTO imported_games(
               id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json
           ) VALUES(?,'lichess','TempoPlayer',?,'rapid',1,?,?,?,'[\"e2e4\"]')""",
        (game_id, played_at or datetime.now(timezone.utc).isoformat(), color, result, START),
    )


def test_statistics_local_day_and_hour_respect_timezone_and_dst(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as db:
            db.execute("UPDATE settings SET timezone='America/New_York' WHERE id=1")
            _game(db, "dst", "1-0", played_at="2026-07-06T03:30:00+00:00")
            db.execute(
                """INSERT INTO game_move_analysis(
                       game_id,ply,eval_before_cp,eval_after_cp,loss_cp,depth,
                       principal_variation_json,mover_color,is_player_move,actual_move_uci
                   ) VALUES('dst',0,0,-10,10,8,'[]','white',1,'e2e4')"""
            )
        refresh_game_features("dst")
        with database.connection() as db:
            feature = db.execute("SELECT * FROM game_feature_rows WHERE game_id='dst'").fetchone()
            assert feature["local_day"] == "2026-07-05"
            assert feature["local_hour"] == 23
            assert feature["local_weekday"] == 6


def test_opening_and_endgame_evaluations_use_player_perspective(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    endgame_fen = "8/8/8/8/8/4k3/7p/6K1 b - - 0 1"
    with TestClient(app):
        with database.connection() as db:
            db.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('black-rep','Black','test',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            db.execute(
                """INSERT INTO imported_games(
                       id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json
                   ) VALUES('perspective','lichess','TempoPlayer',?,'rapid',1,'black','0-1',?,'[\"h2h1q\"]')""",
                (datetime.now(timezone.utc).isoformat(), endgame_fen),
            )
            db.execute(
                """INSERT INTO game_repertoire_matches(
                       game_id,repertoire_id,is_primary,classification,out_of_book_ply,updated_at
                   ) VALUES('perspective','black-rep',1,'out of book',0,?)""",
                (datetime.now(timezone.utc).isoformat(),),
            )
            db.execute(
                """INSERT INTO game_move_analysis(
                       game_id,ply,eval_before_cp,eval_after_cp,loss_cp,depth,
                       principal_variation_json,mover_color,is_player_move,actual_move_uci
                   ) VALUES('perspective',0,200,190,10,8,'[]','black',1,'h2h1q')"""
            )
        refresh_game_features("perspective")
        with database.connection() as db:
            feature = db.execute("SELECT * FROM game_feature_rows WHERE game_id='perspective'").fetchone()
            assert feature["opening_exit_eval_cp"] == -200
            assert feature["endgame_entry_eval_cp"] == -200






@pytest.mark.parametrize("postgres", [False, True])
def test_daily_statistics_refresh_is_retired_without_admission(monkeypatch, postgres):
    from app import command_dispatch, command_gateway, main, tasks
    from tempo_legacy_database.adapters import postgres_store

    def forbidden_admission(*_args, **_kwargs):
        raise AssertionError("Retired daily statistics admitted work")

    monkeypatch.setattr(postgres_store, "configured", lambda: postgres)
    monkeypatch.setattr(command_dispatch, "dispatch_command", forbidden_admission)
    monkeypatch.setattr(main, "enqueue_daily_snapshot", forbidden_admission, raising=False)
    response = TestClient(app).post("/api/statistics/daily/2026-09-18/refresh")
    # The existing PostgreSQL write guard rejects unknown mutations before routing.
    assert response.status_code == (503 if postgres else 404)
    assert "statistics.daily.refresh" not in command_gateway._handlers
    assert "daily_statistics" not in tasks._SUPPORTED_BACKGROUND_KINDS


def test_game_arrival_and_features_do_not_access_retired_daily_materialization(tmp_path, monkeypatch):
    from tempo_legacy_database.adapters.games.sync import records as sync_storage

    def forbidden_daily_write(*_args, **_kwargs):
        raise AssertionError("Game import accessed retired daily materialization")

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    monkeypatch.setattr(sync_storage, "invalidate_day_snapshot", forbidden_daily_write, raising=False)
    monkeypatch.setattr(sync_storage, "invalidate_day_insights", forbidden_daily_write, raising=False)
    record = GameRecord(
        provider="lichess", username="TempoPlayer", provider_game_id="late",
        played_at="2026-09-18T23:00:00+00:00", speed="rapid", rated=True,
        color="white", result="1-0", start_fen=START, uci_moves=["e2e4"],
        content_hash="late-hash",
    )
    assert _persist_game_once(record) == ("inserted", "lichess:late")
    assert _persist_game_once(record) == ("duplicate", "lichess:late")
    changed = replace(record, content_hash="late-updated", result="0-1")
    assert _persist_game_once(changed) == ("updated", "lichess:late")
    refresh_game_features("lichess:late")
    with database.read_connection() as db:
        game = db.execute("SELECT content_hash,analysis_state FROM imported_games WHERE id='lichess:late'").fetchone()
        assert tuple(game) == ("late-updated", "pending")
        feature = db.execute("SELECT game_id,feature_version,outcome_score FROM game_feature_rows WHERE game_id='lichess:late'").fetchone()
        assert tuple(feature) == ("lichess:late", 1, 0.0)
