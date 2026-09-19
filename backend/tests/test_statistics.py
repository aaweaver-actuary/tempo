import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.statistics import refresh_game_features


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _game(db, game_id: str, result: str, color: str = "white", played_at: str | None = None):
    db.execute(
        """INSERT INTO imported_games(
               id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json
           ) VALUES(?,'lichess','TempoPlayer',?,'rapid',1,?,?,?,'[\"e2e4\"]')""",
        (game_id, played_at or datetime.now(timezone.utc).isoformat(), color, result, START),
    )


def test_statistics_score_rate_and_engine_metrics_use_exact_denominators(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _game(db, "win", "1-0")
            _game(db, "draw", "1/2-1/2")
            _game(db, "loss-unanalyzed", "0-1")
            for game_id, loss_cp in (("win", 20), ("draw", 80)):
                db.execute(
                    """INSERT INTO game_move_analysis(
                           game_id,ply,eval_before_cp,eval_after_cp,loss_cp,depth,
                           principal_variation_json,mover_color,is_player_move,actual_move_uci
                       ) VALUES(?,0,0,?, ?,14,'[]','white',1,'e2e4')""",
                    (game_id, -loss_cp, loss_cp),
                )
        refresh_game_features("win")
        refresh_game_features("draw")
        response = client.get("/api/statistics/overview?window_days=30")
        assert response.status_code == 200
        result = response.json()
        assert result["score"] == {
            "value": 0.5, "wins": 1, "draws": 1, "losses": 1,
            "numerator": 1.5, "denominator": 3,
            "confidence_interval": result["score"]["confidence_interval"],
        }
        assert result["decision_quality"]["denominator"] == 2
        assert result["decision_quality"]["mean_loss_cp"] == 50
        assert result["games"] == 3
        assert result["analyzed_games"] == 2


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
