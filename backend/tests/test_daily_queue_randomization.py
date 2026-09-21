from datetime import date

from fastapi.testclient import TestClient

from app import database
from app.main import app, materialize_daily_queue
from app.services.database_executor import submit_foreground_write


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _seed_cards() -> None:
    today = date.today().isoformat()
    with database.connection() as db:
        db.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('queue-test','Queue','test',?)",
            (today,),
        )
        for index, content_type in enumerate(
            ["opening", "puzzle", "endgame", "middlegame"] * 2
        ):
            card_id = f"review-{index}"
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,introduced_at)
                   VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,?,?)""",
                (card_id, "queue-test", START_FEN, today, content_type, today),
            )
            db.execute(
                """INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval)
                   VALUES(?,'correct',?,0,1)""",
                (card_id, f"{today}T00:00:00+00:00"),
            )
        for index, content_type in enumerate(
            ["opening", "puzzle", "endgame", "middlegame"] * 2
        ):
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,introduced_at)
                   VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,?,?)""",
                (f"new-{index}", "queue-test", START_FEN, today, content_type, today),
            )
    submit_foreground_write(
        lambda connection: materialize_daily_queue(connection, today),
        label="test-daily-queue",
    )


def test_daily_queue_is_stable_within_a_day_and_mixed(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_cards()
        first = client.get("/api/queue/today").json()["cards"]
        second = client.get("/api/queue/today").json()["cards"]
        assert [card["id"] for card in first] == [card["id"] for card in second]
        cohorts = ["review" if card["id"].startswith("review-") else "new" for card in first]
        assert all(left != right for left, right in zip(cohorts, cohorts[1:]))
        assert len({card["content_type"] for card in first[:6]}) >= 3


def test_daily_queue_uses_a_different_seed_for_the_next_day(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_cards()
        client.get("/api/queue/today")
        with database.connection() as db:
            from app.main import randomize_daily_queue

            today = date.today().isoformat()
            randomize_daily_queue(db, today)
            today_order = [
                row[0]
                for row in db.execute(
                    "SELECT card_id FROM daily_queue WHERE queue_date=? ORDER BY position",
                    (today,),
                )
            ]
            tomorrow = "2099-01-02"
            db.execute(
                """INSERT INTO daily_queue(queue_date,card_id,position)
                   SELECT ?,card_id,position FROM daily_queue WHERE queue_date=?""",
                (tomorrow, today),
            )
            randomize_daily_queue(db, tomorrow)
            tomorrow_order = [
                row[0]
                for row in db.execute(
                    "SELECT card_id FROM daily_queue WHERE queue_date=? ORDER BY position",
                    (tomorrow,),
                )
            ]
        assert today_order != tomorrow_order
