from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient

from app import database
from app.main import app


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_recurring_motif_recommends_but_does_not_activate_a_tactics_pack(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        with database.connection() as db:
            for index in range(3):
                game_id = f"lichess:fork-{index}"
                played_at = (now - timedelta(days=index)).isoformat()
                db.execute(
                    """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                       VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,'[]')""",
                    (game_id, played_at, START),
                )
                db.execute(
                    """INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,motif,created_at,updated_at)
                       VALUES(?,?,1,8,'tactical miss',0.95,?,'fork',?,?)""",
                    (f"finding-{index}", game_id, json.dumps({"loss_cp": 180 + index}), played_at, played_at),
                )
        recommendation = client.get("/api/game-insights/motifs").json()["recommendations"][0]
        assert recommendation["motif"] == "fork"
        assert recommendation["miss_count"] == 3
        assert recommendation["recommended_pack_id"].startswith("fork-")
        with database.connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM tactic_pack_activation WHERE active=1"
            ).fetchone()[0] == 0
