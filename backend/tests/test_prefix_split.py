from datetime import date, datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app import main as main_module
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
            assert continuation["introduced_at"] == date.today().isoformat()
            assert json.loads(continuation["moves_json"]) == ["b8c6", "f1b5"]
            assert database_connection.execute(
                "SELECT COUNT(*) FROM repertoire_cards WHERE card_id=?",
                (continuation["id"],),
            ).fetchone()[0] == 1


def test_prefix_split_resets_remediation_state_on_shortened_parent(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    complete_line = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection, "white", complete_line
            )
            database_connection.execute(
                "UPDATE cards SET scheduling_mode='hard', hard_correct_streak=2 WHERE id=?",
                (source_card_id,),
            )

        result = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 3},
        ).json()

        with database.connection() as database_connection:
            parent = database_connection.execute(
                """SELECT recent_attempts_json,scheduling_mode,hard_correct_streak
                   FROM cards WHERE id=?""",
                (result["parent"]["card_id"],),
            ).fetchone()
            assert json.loads(parent["recent_attempts_json"]) == []
            assert parent["scheduling_mode"] == "normal"
            assert parent["hard_correct_streak"] == 0

            source = database_connection.execute(
                "SELECT archived,superseded_by FROM cards WHERE id=?",
                (source_card_id,),
            ).fetchone()
            assert source["archived"] == 1
            assert source["superseded_by"] == result["parent"]["card_id"]
            assert database_connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id=?",
                (result["parent"]["card_id"],),
            ).fetchone()[0] == 1
            continuation = database_connection.execute(
                "SELECT kind,moves_json FROM cards WHERE id=?",
                (result["continuation"]["card_id"],),
            ).fetchone()
            assert continuation["kind"] == "response"
            assert json.loads(continuation["moves_json"]) == ["b8c6", "f1b5"]


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
        with database.connection() as database_connection:
            database_connection.execute(
                "UPDATE cards SET pending_validation=0 WHERE id IN (?,?)",
                (first["parent"]["card_id"], first["continuation"]["card_id"]),
            )
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
            assert database_connection.execute(
                "SELECT COUNT(*) FROM cards WHERE id IN (?,?) AND pending_validation=1",
                (first["parent"]["card_id"], first["continuation"]["card_id"]),
            ).fetchone()[0] == 0


def test_rejected_prefix_split_stays_hidden_until_another_failed_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection, "white", ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
            )
        response = client.post(
            f"/api/cards/{source_card_id}/prefix-split/reject",
            json={"expected_revision": 3},
        )
        assert response.status_code == 200
        queue_card = next(card for card in client.get("/api/queue/window?limit=20").json()["cards"] if card["id"] == source_card_id)
        assert queue_card["prefix_split_offer_available"] is False
        with database.connection() as database_connection:
            database_connection.execute(
                "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct',?,0,1)",
                (source_card_id, datetime.now(timezone.utc).isoformat()),
            )
        queue_card = next(card for card in client.get("/api/queue/window?limit=20").json()["cards"] if card["id"] == source_card_id)
        assert queue_card["prefix_split_offer_available"] is False
        with database.connection() as database_connection:
            database_connection.execute(
                "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'again',?,0,0)",
                (source_card_id, datetime.now(timezone.utc).isoformat()),
            )
        queue_card = next(card for card in client.get("/api/queue/window?limit=20").json()["cards"] if card["id"] == source_card_id)
        assert queue_card["prefix_split_offer_available"] is True


def test_prefix_split_accept_enqueues_rebuild_atomically_without_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection, "white", ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
            )
        response = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 3},
        )
        assert response.status_code == 200
        with database.connection() as database_connection:
            assert database_connection.execute(
                "SELECT COUNT(*) FROM background_tasks WHERE kind='opening_graph_rebuild' AND deduplication_key='white-repertoire'"
            ).fetchone()[0] == 1
            queued_cards = [
                row[0] for row in database_connection.execute(
                    "SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id",
                    (date.today().isoformat(),),
                )
            ]
            assert queued_cards == [response.json()["parent"]["card_id"], response.json()["continuation"]["card_id"]]


def test_prefix_split_rolls_back_cards_when_rebuild_cannot_be_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection, "white", ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
            )
        monkeypatch.setattr(main_module, "enqueue_task_in_transaction", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("enqueue failed")))
        response = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 3},
        )
        assert response.status_code == 503
        with database.connection() as database_connection:
            assert database_connection.execute(
                "SELECT archived FROM cards WHERE id=?", (source_card_id,)
            ).fetchone()[0] == 0
            assert database_connection.execute(
                "SELECT COUNT(*) FROM prefix_splits WHERE source_card_id=?", (source_card_id,)
            ).fetchone()[0] == 0


def test_prefix_split_stale_revision_keeps_original_card_and_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as database_connection:
            source_card_id = seed_prefix_card(
                database_connection, "white", ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
            )
        response = client.post(
            f"/api/cards/{source_card_id}/prefix-split",
            json={"expected_revision": 2},
        )
        assert response.status_code == 409
        with database.connection() as database_connection:
            assert database_connection.execute(
                "SELECT archived FROM cards WHERE id=?", (source_card_id,)
            ).fetchone()[0] == 0
            assert database_connection.execute(
                "SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND status='queued'",
                (source_card_id,),
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


@pytest.mark.parametrize('card_source, owner_link_source, other_link_source', [(0, 0, 0), (1, 1, 1), (1, 1, 0), (0, 1, 0)])
def test_prefix_split_preserves_each_shared_membership_provenance(tmp_path, monkeypatch, card_source, owner_link_source, other_link_source):
    from app.services.prefix_split import apply_prefix_split
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    with database.connection() as db:
        source_id = seed_prefix_card(db, 'white', ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'])
        db.execute('UPDATE cards SET canonical_route_source=? WHERE id=?', (card_source, source_id))
        db.execute('UPDATE repertoire_cards SET canonical_route_source=? WHERE card_id=?', (owner_link_source, source_id))
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('shared','Shared','shared.pgn','2026-10-03')")
        db.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,?)', ('shared', source_id, other_link_source))
        result = apply_prefix_split(db, source_id, 3)
        for child in [result['parent']['card_id'], result['continuation']['card_id']]:
            assert db.execute('SELECT canonical_route_source FROM cards WHERE id=?', (child,)).fetchone()[0] == card_source
            for repertoire_id, expected in [('white-repertoire', owner_link_source), ('shared', other_link_source)]:
                assert db.execute('SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (repertoire_id, child)).fetchone()[0] == expected
        assert not db.execute('SELECT 1 FROM repertoire_cards WHERE card_id=?', (source_id,)).fetchone()
        assert apply_prefix_split(db, source_id, 3)['idempotent']


@pytest.mark.parametrize('card_source', [0, 1])
def test_prefix_split_owner_without_link_inherits_card_provenance(tmp_path, monkeypatch, card_source):
    from app.services.prefix_split import apply_prefix_split
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    with database.connection() as db:
        source_id = seed_prefix_card(db, 'white', ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'])
        db.execute('UPDATE cards SET canonical_route_source=? WHERE id=?', (card_source, source_id))
        db.execute('DELETE FROM repertoire_cards WHERE card_id=?', (source_id,))
        result = apply_prefix_split(db, source_id, 3)
        for child in [result['parent']['card_id'], result['continuation']['card_id']]:
            assert db.execute('SELECT canonical_route_source FROM repertoire_cards WHERE card_id=?', (child,)).fetchone()[0] == card_source


def test_prefix_split_generated_input_preserves_existing_authored_children_without_source_bump(tmp_path, monkeypatch):
    from app.services.prefix_split import apply_prefix_split, preview_prefix_split, _copy_card
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    with database.connection() as db:
        source_id = seed_prefix_card(db, 'white', ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'])
        db.execute('UPDATE cards SET canonical_route_source=0 WHERE id=?', (source_id,))
        db.execute('UPDATE repertoire_cards SET canonical_route_source=0 WHERE card_id=?', (source_id,))
        preview = preview_prefix_split(db, source_id)
        source = db.execute('SELECT * FROM cards WHERE id=?', (source_id,)).fetchone()
        for child in [preview['parent'], preview['continuation']]:
            _copy_card(db, source, {'id': child['card_id'], 'start_fen': child['starting_fen'], 'moves_json': json.dumps(child['moves']), 'canonical_route_source': 1})
            db.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1)', ('white-repertoire', child['card_id']))
        before = db.execute("SELECT scope_source_revision FROM repertoires WHERE id='white-repertoire'").fetchone()[0]
        result = apply_prefix_split(db, source_id, 3)
        assert db.execute("SELECT scope_source_revision FROM repertoires WHERE id='white-repertoire'").fetchone()[0] == before
        for child in [result['parent']['card_id'], result['continuation']['card_id']]:
            assert db.execute('SELECT canonical_route_source FROM repertoire_cards WHERE card_id=?', (child,)).fetchone()[0] == 1
