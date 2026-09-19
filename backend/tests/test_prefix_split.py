from datetime import date, datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.cards import card_id


STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def seed_prefix_card(database_connection, trained_color: str, moves: list[str]) -> str:
    repertoire_id = f"{trained_color}-repertoire"
    identifier = card_id(STARTING_FEN, moves)
    now = datetime.now(timezone.utc).isoformat()
    database_connection.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
        (repertoire_id, trained_color.title(), f"{trained_color}.pgn", now),
    )
    database_connection.execute(
        "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (
            f"{trained_color}-line",
            repertoire_id,
            "Main line",
            trained_color,
            STARTING_FEN,
            json.dumps(moves),
            now,
        ),
    )
    database_connection.execute(
        """INSERT INTO cards(
               id,repertoire_id,kind,start_fen,moves_json,state,due_date,
               content_type,trained_color,revision,recent_attempts_json
           ) VALUES(?,?,'prefix',?,?,'learning',?,'opening',?,3,'[\"again\",\"again\",\"again\"]')""",
        (
            identifier,
            repertoire_id,
            STARTING_FEN,
            json.dumps(moves),
            date.today().isoformat(),
            trained_color,
        ),
    )
    database_connection.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
        (repertoire_id, identifier),
    )
    database_connection.execute(
        """INSERT INTO reviews(
               card_id,rating,reviewed_at,previous_interval,next_interval
           ) VALUES(?,'again',?,0,0)""",
        (identifier, now),
    )
    database_connection.execute(
        "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,0)",
        (date.today().isoformat(), identifier),
    )
    return identifier


@pytest.mark.parametrize(
    ("trained_color", "moves", "expected_parent", "expected_child"),
    [
        (
            "white",
            ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
            ["e2e4", "e7e5", "g1f3"],
            ["b8c6", "f1b5"],
        ),
        (
            "black",
            ["e2e4", "e7e5", "g1f3", "b8c6"],
            ["e2e4", "e7e5"],
            ["g1f3", "b8c6"],
        ),
    ],
)
def test_black_and_white_prefix_splits_preserve_the_opponent_setup_move(
    tmp_path,
    monkeypatch,
    trained_color,
    moves,
    expected_parent,
    expected_child,
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection, trained_color, moves
            )
        preview = client.get(f"/api/cards/{source_card_id}/prefix-split")
        assert preview.status_code == 200
        assert preview.json()["parent"]["moves"] == expected_parent
        assert preview.json()["continuation"]["moves"] == expected_child
        accepted = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 3},
        )
        assert accepted.status_code == 200
        assert accepted.json()["continuation"]["tested_player_moves"] == 1


def test_accepted_shorter_opening_prefix_creates_a_one_player_decision_continuation_card(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    complete_line = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection, "white", complete_line
            )
        result = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 3},
        ).json()
        with database.connection() as database_connection:
            continuation = database_connection.execute(
                "SELECT * FROM cards WHERE id=?", (result["continuation"]["card_id"],)
            ).fetchone()
            assert continuation["kind"] == "response"
            assert continuation["state"] == "learning"
            assert json.loads(continuation["moves_json"]) == ["b8c6", "f1b5"]
            assert database_connection.execute(
                "SELECT COUNT(*) FROM repertoire_cards WHERE card_id=?",
                (continuation["id"],),
            ).fetchone()[0] == 1


def test_prefix_split_retry_creates_exactly_one_parent_child_and_queue_entry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection,
                "white",
                ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
            )
        first = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 3},
        ).json()
        repeated = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 3},
        ).json()
        assert first["idempotent"] is False
        assert repeated["idempotent"] is True
        assert repeated["parent"] == first["parent"]
        assert repeated["continuation"] == first["continuation"]
        with database.connection() as database_connection:
            assert database_connection.execute(
                "SELECT COUNT(*) FROM prefix_splits WHERE source_card_id=?",
                (source_card_id,),
            ).fetchone()[0] == 1
            assert database_connection.execute(
                "SELECT COUNT(*) FROM daily_queue WHERE card_id=?",
                (first["continuation"]["card_id"],),
            ).fetchone()[0] == 1


def test_prefix_split_preserves_the_complete_repertoire_line_and_original_reviews(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    complete_line = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection, "white", complete_line
            )
        result = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 3},
        ).json()
        with database.connection() as database_connection:
            assert json.loads(
                database_connection.execute(
                    "SELECT moves_json FROM repertoire_lines"
                ).fetchone()[0]
            ) == complete_line
            assert database_connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id=?",
                (result["parent"]["card_id"],),
            ).fetchone()[0] == 1
            source = database_connection.execute(
                "SELECT archived,superseded_by FROM cards WHERE id=?",
                (source_card_id,),
            ).fetchone()
            assert source["archived"] == 1
            assert source["superseded_by"] == result["parent"]["card_id"]
