from datetime import date, datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient

from app import database
from app.main import app


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def seed_lapse(database_connection, finding_id: str = "lapse"):
    now = datetime.now(timezone.utc).isoformat()
    database_connection.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES('rep','Main','main.pgn',?,1)",
        (now,),
    )
    database_connection.execute(
        """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,interval_days)
           VALUES('card','rep','prefix',?,?,'mature',?,7)""",
        (START, json.dumps(["e2e4"]), (date.today() + timedelta(days=7)).isoformat()),
    )
    database_connection.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep','card')"
    )
    database_connection.execute(
        """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
           VALUES('lichess:game','lichess','TempoPlayer',?,'rapid',1,'white','0-1',?,?)""",
        (now, START, json.dumps(["d2d4"])),
    )
    database_connection.execute(
        """INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,repertoire_id,card_id,created_at,updated_at)
           VALUES(?,'lichess:game',1,0,'repertoire lapse',1,?,'rep','card',?,?)""",
        (finding_id, json.dumps({"fen": START, "expected": ["e2e4"], "actual": "d2d4"}), now, now),
    )


def test_ignored_gameplay_finding_never_changes_card_scheduling(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_lapse(db)
            before = dict(db.execute("SELECT * FROM cards WHERE id='card'").fetchone())
        response = client.post("/api/game-findings/lapse/decision", json={"decision": "ignored"})
        assert response.status_code == 200
        with database.connection() as db:
            after = dict(db.execute("SELECT * FROM cards WHERE id='card'").fetchone())
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='card'").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card'").fetchone()[0] == 0
        assert after == before


def test_confirmed_gameplay_lapse_records_exactly_one_again_and_queues_the_card(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_lapse(db)
            for index in range(5):
                card_id = f"other-{index}"
                db.execute(
                    "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,'rep','prefix',?,'[]',?)",
                    (card_id, START, date.today().isoformat()),
                )
                db.execute(
                    "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",
                    (date.today().isoformat(), card_id, index),
                )
        first = client.post("/api/game-findings/lapse/decision", json={"decision": "accepted"})
        second = client.post("/api/game-findings/lapse/decision", json={"decision": "accepted"})
        assert first.status_code == second.status_code == 200
        assert second.json()["scheduling"]["idempotent"] is True
        with database.connection() as db:
            review = db.execute("SELECT * FROM reviews WHERE source_kind='game' AND source_ref='lapse'").fetchall()
            assert len(review) == 1
            assert review[0]["rating"] == "again"
            card = db.execute("SELECT due_date FROM cards WHERE id='card'").fetchone()
            assert card["due_date"] == date.today().isoformat()
            queued = db.execute(
                "SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id",
                (date.today().isoformat(),),
            ).fetchall()
        assert [row["card_id"] for row in queued].index("card") == 4


def test_first_big_mistake_creates_a_previewed_deduplicated_middlegame_card(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    now = datetime.now(timezone.utc).isoformat()
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                   VALUES('lichess:mistake','lichess','TempoPlayer',?,'rapid',1,'white','0-1',?,'[]')""",
                (now, START),
            )
            db.execute(
                """INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at)
                   VALUES('mistake','lichess:mistake',1,0,'first big mistake',1,?,?,?)""",
                (json.dumps({"fen": START, "best_move_uci": "e2e4", "principal_variation": ["e2e4", "e7e5", "g1f3"]}), now, now),
            )
        preview = client.post("/api/game-findings/mistake/card", json={"save": False}).json()
        assert preview["saved"] is False
        assert preview["preview"]["best_move"] == "e2e4"
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM cards WHERE content_type='middlegame'").fetchone()[0] == 0
        saved = client.post("/api/game-findings/mistake/card", json={"save": True}).json()
        repeated = client.post("/api/game-findings/mistake/card", json={"save": True}).json()
        assert saved["saved"] is True
        assert repeated["reused"] is True
        assert repeated["card_id"] == saved["card_id"]
        with database.connection() as db:
            card = db.execute("SELECT * FROM cards WHERE id=?", (saved["card_id"],)).fetchone()
            assert card["content_type"] == "middlegame"
            assert card["due_date"] == date.today().isoformat()
            assert card["trained_color"] == "white"
            assert db.execute("SELECT COUNT(*) FROM cards WHERE content_type='middlegame'").fetchone()[0] == 1
