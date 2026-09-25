import json

import chess
from fastapi.testclient import TestClient

from app import database
from app.main import app


START = chess.STARTING_FEN


def seed_line(db, identifier, repertoire_id, color, moves, starting_fen=START):
    db.execute(
        "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
        (repertoire_id, repertoire_id, "paste-test.pgn", "2026-09-25T00:00:00+00:00"),
    )
    db.execute(
        "INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",
        (identifier, repertoire_id, identifier, color, starting_fen, json.dumps(moves), "2026-09-25T00:00:00+00:00"),
    )


def preview(client, text, **extra):
    response = client.post("/api/repertoire/paste/preview", json={"text": text, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def commit(client, view, text, selections, **extra):
    return client.post("/api/repertoire/paste/commit", json={
        "text": text, "preview_token": view["preview_token"],
        "selections": selections, **extra,
    })


def test_paste_san_variations_matches_repertoire_and_saves_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_line(db, "white-line", "white-rep", "white", ["e2e4", "e7e5", "g1f3"])
            seed_line(db, "black-line", "black-rep", "black", ["d2d4", "d7d5", "c2c4", "e7e6"])
        text = "1. e4 e5 2. Nf3 Nc6 3. Bc4 *\n\n1. d4 d5 2. c4 e6 3. Nc3 Nf6 *"
        view = preview(client, text)
        assert len(view["lines"]) == 2
        assert [line["suggested_repertoire_id"] for line in view["lines"]] == ["white-rep", "black-rep"]
        result = commit(client, view, text, [
            {"index": 0, "repertoire_id": "white-rep"},
            {"index": 1, "repertoire_id": "black-rep"},
        ])
        assert result.status_code == 200, result.text
        assert len(result.json()["saved"]) == 2
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM repertoire_lines").fetchone()[0] == 4


def test_paste_pgn_fen_variations_and_partial_gap_context(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        board = chess.Board()
        board.push_san("e4")
        context_fen = board.fen()
        with database.connection() as db:
            seed_line(db, "white-line", "white-rep", "white", ["e2e4", "e7e5", "g1f3"])
        text = f'[SetUp "1"]\n[FEN "{context_fen}"]\n\n1... c5 2. Nf3 (2. Nc3) *'
        view = preview(client, text)
        assert len(view["lines"]) == 2
        assert all(line["starting_fen"] == context_fen for line in view["lines"])
        partial = preview(client, "1... c5 2. Nf3", starting_fen=context_fen)
        assert partial["lines"][0]["moves"] == ["c7c5", "g1f3"]
        assert client.post("/api/repertoire/paste/preview", json={"text": "1... c5 2. Nf3"}).status_code == 422


def test_paste_preview_requires_choice_when_multiple_match_or_none_match(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_line(db, "one", "one-rep", "white", ["e2e4", "e7e5", "g1f3"])
            seed_line(db, "two", "two-rep", "white", ["e2e4", "e7e5", "f1c4"])
        ambiguous = preview(client, "1. e4 e5 2. Nc3")
        assert ambiguous["lines"][0]["suggested_repertoire_id"] is None
        assert len([option for option in ambiguous["lines"][0]["options"] if option["matched"]]) == 2
        unmatched = preview(client, "1. d4 d5 2. c4")
        assert unmatched["lines"][0]["suggested_repertoire_id"] is None
        assert not any(option["matched"] for option in unmatched["lines"][0]["options"])


def test_paste_matches_a_line_starting_at_existing_terminal_position(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_line(db, "short", "white-rep", "white", ["e2e4"])
            seed_line(db, "other", "black-rep", "black", ["d2d4", "d7d5"])
        board = chess.Board()
        board.push_san("e4")
        result = preview(client, "1... c5 2. Nf3", starting_fen=board.fen())
        assert result["lines"][0]["suggested_repertoire_id"] == "white-rep"


def test_paste_duplicate_conflict_and_stale_preview_are_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_line(db, "existing", "white-rep", "white", ["e2e4", "e7e5", "g1f3"])
        duplicate_text = "1. e4 e5 2. Nf3"
        duplicate = preview(client, duplicate_text)
        option = next(option for option in duplicate["lines"][0]["options"] if option["repertoire_id"] == "white-rep")
        assert option["duplicate"] is True
        saved_duplicate = commit(client, duplicate, duplicate_text, [{"index": 0, "repertoire_id": "white-rep"}])
        assert saved_duplicate.status_code == 200
        assert saved_duplicate.json()["saved"][0]["duplicate"] is True
        conflict_text = "1. d4 d5 2. c4"
        conflicting = preview(client, conflict_text)
        option = next(option for option in conflicting["lines"][0]["options"] if option["repertoire_id"] == "white-rep")
        assert option["conflicts"]
        assert commit(client, conflicting, conflict_text, [{"index": 0, "repertoire_id": "white-rep"}]).status_code == 422
        with database.connection() as db:
            seed_line(db, "changed", "white-rep", "white", ["c2c4", "e7e5"])
        stale = commit(client, conflicting, conflict_text, [{"index": 0, "repertoire_id": "white-rep", "acknowledge_conflict": True}])
        assert stale.status_code == 409
        assert client.post("/api/repertoire/paste/preview", json={"text": "1. e4 Banana"}).status_code == 422


def test_paste_marks_gap_covered_only_with_opponent_move_and_response(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        board = chess.Board()
        board.push_san("e4")
        gap_fen = board.fen()
        with database.connection() as db:
            seed_line(db, "existing", "white-rep", "white", ["e2e4", "e7e5", "g1f3"])
            db.execute("INSERT INTO repertoire_coverage_runs(id,repertoire_id,status,settings_json,created_at,updated_at) VALUES('run','white-rep','complete','{}','now','now')")
            db.execute("""INSERT INTO repertoire_coverage_nodes(
                id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,covered_replies_json,updated_at
            ) VALUES('node','run','white-rep',?, ?,1,'white','[]','[]','now')""", (gap_fen, " ".join(gap_fen.split()[:4])))
            db.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,source_state) VALUES('node','c7c5','ready')")
        text = "1. e4 c5 2. Nf3"
        view = preview(client, text, source_gap_id="node:c7c5")
        saved = commit(client, view, text, [{"index": 0, "repertoire_id": "white-rep", "acknowledge_conflict": True}], source_gap_id="node:c7c5")
        assert saved.status_code == 200, saved.text
        assert saved.json()["gap_resolved"] is True
        with database.connection() as db:
            assert db.execute("SELECT covered FROM repertoire_coverage_candidates WHERE node_id='node'").fetchone()[0] == 1


def test_paste_batch_conflict_requires_confirmation_and_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_line(db, "existing", "white-rep", "white", ["e2e4", "e7e5", "g1f3"])
        text = "1. e4 e5 2. Nf3 Nc6 3. Bc4\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5"
        view = preview(client, text)
        selections = [
            {"index": 0, "repertoire_id": "white-rep"},
            {"index": 1, "repertoire_id": "white-rep"},
        ]
        rejected = commit(client, view, text, selections)
        assert rejected.status_code == 422
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM repertoire_lines").fetchone()[0] == 1
        acknowledged = commit(client, view, text, [
            {**selection, "acknowledge_conflict": True} for selection in selections
        ])
        assert acknowledged.status_code == 200, acknowledged.text
        assert all(line["conflict"] for line in acknowledged.json()["saved"])


def test_paste_rejects_incomplete_trained_side_line_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            seed_line(db, "existing", "white-rep", "white", ["e2e4", "e7e5", "g1f3"])
        text = "1. e4 e5"
        view = preview(client, text)
        option = next(option for option in view["lines"][0]["options"] if option["repertoire_id"] == "white-rep")
        assert option["trainable"] is False
        saved = commit(client, view, text, [{"index": 0, "repertoire_id": "white-rep"}])
        assert saved.status_code == 422
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM repertoire_lines").fetchone()[0] == 1
