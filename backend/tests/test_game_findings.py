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
                    """INSERT INTO gameplay_events(
                           id,game_id,analysis_version,classifier_version,ply,kind,motif,
                           beneficiary_color,created_by_color,outcome,confidence,loss_cp,
                           principal_variation_json,evidence_json,created_at,updated_at
                       ) VALUES(?,?,1,1,8,'tactical opportunity','fork','white','black',
                                'missed',0.95,?,'[]','{}',?,?)""",
                    (f"event-{index}", game_id, 180 + index, played_at, played_at),
                )
        recommendation = client.get("/api/game-insights/motifs").json()["recommendations"][0]
        assert recommendation["motif"] == "fork"
        assert recommendation["miss_count"] == 3
        assert recommendation["opportunity_count"] == 3
        assert recommendation["miss_rate"] == 1
        assert recommendation["window_days"] == 30
        assert recommendation["recommended_pack_id"].startswith("fork-")
        with database.connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM tactic_pack_activation WHERE active=1"
            ).fetchone()[0] == 0


def test_tactics_suggestions_use_thirty_days_and_explicit_opportunity_denominator(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        with database.connection() as db:
            for index, (age_days, outcome) in enumerate(
                [(1, "missed"), (2, "missed"), (3, "missed"), (4, "found"), (31, "missed")]
            ):
                game_id = f"lichess:window-{index}"
                played_at = (now - timedelta(days=age_days)).isoformat()
                db.execute(
                    """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                       VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,'[]')""",
                    (game_id, played_at, START),
                )
                db.execute(
                    """INSERT INTO gameplay_events(
                           id,game_id,analysis_version,classifier_version,ply,kind,motif,
                           beneficiary_color,created_by_color,outcome,confidence,loss_cp,
                           principal_variation_json,evidence_json,created_at,updated_at
                       ) VALUES(?,?,1,1,8,'tactical opportunity','fork','white','black',
                                ?,0.95,180,'[]','{}',?,?)""",
                    (f"window-event-{index}", game_id, outcome, played_at, played_at),
                )
        recommendation = client.get("/api/game-insights/motifs").json()["recommendations"][0]
        assert recommendation["miss_count"] == 3
        assert recommendation["opportunity_count"] == 4
        assert recommendation["miss_rate"] == 0.75
        assert len(recommendation["supporting_games"]) == 3
