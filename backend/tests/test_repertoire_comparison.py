from datetime import datetime, timezone
import json
import threading

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.repertoire_comparison import compare_all_games
import app.services.repertoire_comparison as repertoire_comparison


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def seed_repertoire(database_connection, moves: list[str]):
    now = datetime.now(timezone.utc).isoformat()
    database_connection.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES('rep','Réti','reti.pgn',?,1)",
        (now,),
    )
    database_connection.execute(
        "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('line','rep','Réti','white',?,?,?)",
        (START, json.dumps(moves), now),
    )
    database_connection.execute(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('card','rep','prefix',?,?,?)",
        (START, json.dumps(moves), now[:10]),
    )
    database_connection.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep','card')"
    )


def seed_game(database_connection, identifier: str, moves: list[str]):
    database_connection.execute(
        """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
           VALUES(?, 'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,?)""",
        (identifier, datetime.now(timezone.utc).isoformat(), START, json.dumps(moves)),
    )


def test_repertoire_comparison_retains_identity_across_deviation_and_transposition(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as db:
            seed_repertoire(db, ["g1f3", "d7d5", "g2g3", "g8f6", "f1g2"])
            seed_game(db, "transposition", ["g2g3", "d7d5", "g1f3", "g8f6", "f1g2"])
        compare_all_games()
        with database.connection() as db:
            match = db.execute(
                "SELECT * FROM game_repertoire_matches WHERE game_id='transposition' AND is_primary=1"
            ).fetchone()
        assert match["repertoire_id"] == "rep"
        assert match["classification"] == "player deviation"
        assert match["first_player_deviation_ply"] == 0
        assert match["deviation_card_id"] == "card"
        assert match["matched_player_decisions"] == 1
        assert match["deepest_covered_ply"] == 5


def test_line_ending_is_out_of_book_rather_than_a_player_deviation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as db:
            seed_repertoire(db, ["e2e4", "e7e5"])
            seed_game(db, "line-ending", ["e2e4", "e7e5", "g1f3"])
        compare_all_games()
        with database.connection() as db:
            match = db.execute(
                "SELECT * FROM game_repertoire_matches WHERE game_id='line-ending'"
            ).fetchone()
        assert match["classification"] == "out of book"
        assert match["out_of_book_ply"] == 2
        assert match["first_player_deviation_ply"] is None


def test_background_repertoire_computation_holds_no_sqlite_write_transaction(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    entered_computation = threading.Event()
    release_computation = threading.Event()
    original_compare = repertoire_comparison._compare_game_to_repertoire

    def deliberately_slow_compare(*args, **kwargs):
        entered_computation.set()
        assert release_computation.wait(timeout=2)
        return original_compare(*args, **kwargs)

    monkeypatch.setattr(
        repertoire_comparison,
        "_compare_game_to_repertoire",
        deliberately_slow_compare,
    )
    with TestClient(app):
        with database.connection() as db:
            seed_repertoire(db, ["e2e4", "e7e5"])
            seed_game(db, "slow-compute", ["e2e4", "e7e5"])
        background_thread = threading.Thread(
            target=lambda: repertoire_comparison.compare_games(
                ["slow-compute"], background=True
            )
        )
        background_thread.start()
        assert entered_computation.wait(timeout=1)
        with database.connection() as db:
            db.execute("UPDATE settings SET new_cards_per_day=11 WHERE id=1")
        release_computation.set()
        background_thread.join(timeout=2)
        assert not background_thread.is_alive()
