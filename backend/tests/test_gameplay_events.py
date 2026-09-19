import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.gameplay_events import refresh_gameplay_events


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_gameplay_events_reuse_both_sides_analysis_and_recompute_idempotently(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:events"
    moves = ["e2e4", "d7d5", "e4d5"]
    with TestClient(app):
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(
                       id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,analysis_version
                   ) VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,?,1)""",
                (game_id, datetime.now(timezone.utc).isoformat(), START, json.dumps(moves)),
            )
            db.execute(
                """INSERT INTO game_move_analysis(
                       game_id,ply,eval_before_cp,eval_after_cp,loss_cp,depth,best_move_uci,
                       principal_variation_json,mover_color,is_player_move,actual_move_uci
                   ) VALUES(?,1,0,160,160,14,'d7d5','[]','black',0,'d7d5')""",
                (game_id,),
            )
            db.execute(
                """INSERT INTO game_move_analysis(
                       game_id,ply,eval_before_cp,eval_after_cp,loss_cp,depth,best_move_uci,
                       principal_variation_json,mover_color,is_player_move,actual_move_uci
                   ) VALUES(?,2,160,150,10,14,'e4d5','[\"e4d5\"]','white',1,'e4d5')""",
                (game_id,),
            )
        refresh_gameplay_events(game_id)
        refresh_gameplay_events(game_id)
        with database.connection() as db:
            rows = db.execute(
                "SELECT * FROM gameplay_events WHERE game_id=? AND kind='tactical opportunity'",
                (game_id,),
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["beneficiary_color"] == "white"
            assert rows[0]["created_by_color"] == "black"
            assert rows[0]["outcome"] == "found"
