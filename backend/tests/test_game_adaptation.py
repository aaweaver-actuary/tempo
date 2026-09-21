from datetime import date, datetime, timedelta, timezone
import json

import chess
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.game_findings import refresh_game_findings
from app.services import introduction_priorities


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_stockfish_timeout_preserves_personal_priority_evidence_and_remains_retryable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    now = datetime.now(timezone.utc).isoformat()
    position_after_e4 = chess.Board(START)
    position_after_e4.push_uci("e2e4")
    opponent_position_key = " ".join(position_after_e4.fen().split()[:4])
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(
                       id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,analysis_state
                   ) VALUES('lichess:timeout','lichess','player',?,'rapid',1,'white','1-0',?,?,'analyzing')""",
                (now, START, json.dumps(["e2e4", "e7e5"])),
            )
            db.execute(
                """INSERT INTO game_analysis_jobs(
                       game_id,status,lease_id,updated_at
                   ) VALUES('lichess:timeout','leased','lease-timeout',?)""",
                (now,),
            )
            db.execute(
                """INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci)
                   VALUES('lichess:timeout',1,?,'e7e5')""",
                (opponent_position_key,),
            )
        failed = client.post(
            "/api/games/analysis/lichess:timeout/failure",
            json={"lease_id": "lease-timeout", "error": "Stockfish took too long"},
        )
        assert failed.status_code == 200
        assert failed.json() == {"status": "failed", "retryable": True}
        refresh_game_findings("lichess:timeout")
        with database.connection() as db:
            job = db.execute(
                "SELECT status,last_error FROM game_analysis_jobs WHERE game_id='lichess:timeout'"
            ).fetchone()
            game = db.execute(
                "SELECT analysis_state FROM imported_games WHERE id='lichess:timeout'"
            ).fetchone()
            personal = introduction_priorities._personal_evidence(
                db,
                {opponent_position_key},
                "white",
            )
            assert db.execute(
                "SELECT COUNT(*) FROM game_findings WHERE game_id='lichess:timeout'"
            ).fetchone()[0] == 0
        assert job["status"] == "failed"
        assert job["last_error"] == "Stockfish took too long"
        assert game["analysis_state"] == "failed"
        assert personal[opponent_position_key]["e7e5"] > 0
        retried = client.post("/api/games/analysis/lichess:timeout/retry")
        assert retried.status_code == 200
        assert retried.json()["status"] == "queued"


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


def test_game_gap_with_nearby_mistake_prioritizes_existing_unseen_card_for_tomorrow(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    now = datetime.now(timezone.utc).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with TestClient(app):
        with database.connection() as db:
            db.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Rep','rep.pgn',?)",
                (now,),
            )
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color)
                   VALUES('future-card','rep','prefix',?,'[\"e2e4\"]','new',?,'opening','white')""",
                (START, (date.today() + timedelta(days=21)).isoformat()),
            )
            db.execute(
                """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,analysis_version)
                   VALUES('gap-game','lichess','TempoPlayer',?,'rapid',1,'white','lost',?,'[\"d2d4\"]',1)""",
                (now, START),
            )
            db.execute(
                """INSERT INTO game_move_analysis(game_id,ply,eval_before_cp,eval_after_cp,loss_cp,label,depth,best_move_uci,principal_variation_json)
                   VALUES('gap-game',0,20,-150,170,'major mistake',14,'e2e4','[\"e2e4\",\"e7e5\"]')"""
            )
            db.execute(
                "INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at) VALUES('rep','clean',?)",
                (now,),
            )
        refresh_game_findings("gap-game")
        with database.connection() as db:
            finding = db.execute(
                "SELECT * FROM game_findings WHERE game_id='gap-game' AND kind='repertoire gap'"
            ).fetchone()
            assert finding["card_id"] == "future-card"
            priority = db.execute(
                "SELECT * FROM gameplay_card_priorities WHERE card_id='future-card'"
            ).fetchone()
            assert priority["priority_date"] == tomorrow
            from app.main import seed_queue

            seed_queue(db, tomorrow)
            queued = db.execute(
                "SELECT card_id FROM daily_queue WHERE queue_date=? ORDER BY position", (tomorrow,)
            ).fetchall()
            assert [row["card_id"] for row in queued][0] == "future-card"


def test_missing_game_gap_line_requires_preview_before_card_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    now = datetime.now(timezone.utc).isoformat()
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,analysis_version)
                   VALUES('gap-game','lichess','TempoPlayer',?,'rapid',1,'white','lost',?,'[\"d2d4\"]',1)""",
                (now, START),
            )
            db.execute(
                """INSERT INTO game_move_analysis(game_id,ply,eval_before_cp,eval_after_cp,loss_cp,label,depth,best_move_uci,principal_variation_json)
                   VALUES('gap-game',0,20,-150,170,'major mistake',14,'e2e4','[\"e2e4\",\"e7e5\"]')"""
            )
        refresh_game_findings("gap-game")
        finding = client.get("/api/game-findings?game_id=gap-game").json()["findings"]
        gap = next(item for item in finding if item["kind"] == "repertoire gap")
        preview = client.post(
            f"/api/game-findings/{gap['id']}/card", json={"save": False}
        ).json()
        assert preview["saved"] is False
        with database.connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM cards WHERE source_ref=?", (gap["id"],)
            ).fetchone()[0] == 0
