from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from app import database
from app.main import app


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def seed_opportunity(db, *, game_id="tactical-game", finding_id="tactical-finding", outcome="missed"):
    now = datetime.now(timezone.utc).isoformat()
    evidence = {
        "fen": START,
        "actual_move_uci": "a2a3",
        "candidate_lines": [{"uci": "e2e4", "pv": ["e2e4", "e7e5"]}],
        "motif_evidence": [{"motif": "pin", "existed_before": False, "created_by_candidate_move": True,
                            "concrete_outcome": {"type": "evaluation_swing", "evaluation_swing_cp": 300}}],
    }
    db.execute(
        """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,analysis_state,analysis_version)
           VALUES(?,?,?,?,?,1,'white','lost',?,?,'ready',1)""",
        (game_id, "lichess", "Tempo", now, "rapid", START, json.dumps(["a2a3"])),
    )
    db.execute(
        """INSERT INTO tactical_opportunities(id,game_id,analysis_version,ply,motif,outcome,confidence,opportunity_value_cp,
           evaluation_loss_cp,played_move_uci,accepted_moves_json,evidence_json,active,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
        ("opportunity-1", game_id, 1, 0, "pin", outcome, .95, 300, 180, "a2a3", '["e2e4"]', json.dumps(evidence), now, now),
    )
    db.execute(
        """INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,motif,source_opportunity_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (finding_id, game_id, 1, 0, "tactical miss", .95, json.dumps(evidence), "pin", "opportunity-1", now, now),
    )


def test_tactical_statistics_has_explicit_zero_safe_conversion_and_pin_breakdown(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_opportunity(db)
        response = client.get("/api/game-insights/tactical")
    assert response.status_code == 200
    body = response.json()
    assert body["overall"]["opportunities"] == 1
    assert body["overall"]["conversion_rate"] == 0.0
    assert body["motifs"][0]["pin_breakdown"]["created"]["opportunities"] == 1


def test_tactical_queue_skip_keeps_finding_pending_and_ignore_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_opportunity(db)
        first = client.get("/api/game-findings/tactical-queue")
        assert first.status_code == 200 and first.json()["remaining"] == 1
        skipped = client.post("/api/game-findings/tactical-finding/curation", json={"action": "skip"})
        assert skipped.status_code == 200
        with database.connection() as db:
            assert db.execute("SELECT status FROM game_findings WHERE id='tactical-finding'").fetchone()[0] == "pending"
        ignored = client.post("/api/game-findings/tactical-finding/curation", json={"action": "ignore"})
        assert ignored.status_code == 200
        assert client.get("/api/game-findings/tactical-queue").json()["remaining"] == 0


def test_tactical_card_preview_is_side_effect_free_and_save_admits_one_personal_tactics_card(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_opportunity(db)
        preview = client.post("/api/game-findings/tactical-finding/card", json={"save": False})
        assert preview.status_code == 200
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
        saved = client.post("/api/game-findings/tactical-finding/card", json={"save": True})
        assert saved.status_code == 200
        with database.connection() as db:
            card = db.execute("SELECT content_type,repertoire_id FROM cards").fetchone()
            assert tuple(card) == ("tactics", "__game_tactics__")
            assert db.execute("SELECT status FROM game_findings WHERE id='tactical-finding'").fetchone()[0] == "accepted"
            assert db.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 1
