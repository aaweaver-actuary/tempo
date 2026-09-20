import json

import chess
from fastapi.testclient import TestClient
from app.main import seed_queue
from app.main import app

from app import database
from app.services import introduction_priorities as priorities


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _seed_repertoire(db, lines: list[list[str]], cards: list[list[str]]) -> None:
    db.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Rep','rep.pgn','2026-09-20T00:00:00+00:00')"
    )
    for index, moves in enumerate(lines):
        db.execute(
            """INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
               VALUES(?,?,?,'white',?,?,?)""",
            (f"line-{index}", "rep", f"line-{index}", START, json.dumps(moves), "2026-09-20T00:00:00+00:00"),
        )
    for index, moves in enumerate(cards):
        db.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date)
               VALUES(?,?,'prefix',?,?,?)""",
            (f"card-{index}", "rep", START, json.dumps(moves), "2026-09-20"),
        )
        db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',?)", (f"card-{index}",))


def _fen_after(moves: list[str]) -> str:
    board = chess.Board()
    for move in moves:
        board.push_uci(move)
    return " ".join(board.fen().split()[:4])


def _seed_public_probability(db, node_id: str, moves_before: list[str], move_uci: str, probability: float) -> None:
    db.execute(
        """INSERT INTO repertoire_coverage_nodes(
               id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,
               covered_replies_json,explorer_status,maia_status,explorer_games,updated_at
           ) VALUES(?,?,?, ?,?,?,?,'[]','[]','complete','complete',100,?)""",
        (node_id, "run", "rep", _fen_after(moves_before), _fen_after(moves_before), len(moves_before), "white", "2026-09-20T00:00:00+00:00"),
    )
    db.execute(
        """INSERT INTO repertoire_coverage_candidates(
               node_id,move_uci,explorer_probability,maia_probability,source_state
           ) VALUES(?,?,?,?,?)""",
        (node_id, move_uci, probability, probability, "blended"),
    )


def test_completed_zero_personal_and_explorer_support_with_low_maia_deprioritizes_the_line():
    probability, evidence = priorities._move_probability(
        "c7c5",
        "position",
        public={
            "position": {
                "explorer_status": "complete",
                "explorer_games": 1_000,
                "maia_status": "complete",
                "moves": {"c7c5": {"maia_probability": 0.001}},
            }
        },
        personal={"position": {"total": 10.0, "e7e5": 10.0}},
        reply_moves={"position": {"c7c5", "e7e5"}},
        path_floor=0.0005,
    )
    assert probability < 0.001
    assert evidence["personal"] == "zero"
    assert evidence["explorer"] == "zero"
    assert evidence["maia"] == "positive"


def test_unavailable_sources_are_unknown_instead_of_zero():
    probability, evidence = priorities._move_probability(
        "c7c5",
        "position",
        public={
            "position": {
                "explorer_status": "queued",
                "explorer_games": 0,
                "maia_status": "queued",
                "moves": {},
            }
        },
        personal={},
        reply_moves={"position": {"c7c5", "e7e5"}},
        path_floor=0.0005,
    )
    assert probability == 0.5
    assert evidence["personal"] == "unknown"
    assert evidence["explorer"] == "pending"
    assert evidence["maia"] == "pending"


def test_strict_prefix_repertoire_records_do_not_become_duplicate_completed_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(
            db,
            [["d2d4", "d7d5", "c1f4"], ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3"]],
            [["d2d4", "d7d5", "c1f4"]],
        )
        rows = db.execute("SELECT * FROM repertoire_lines WHERE repertoire_id='rep'").fetchall()
    assert len(priorities._maximal_intended_lines(rows)) == 1


def test_london_trunk_moves_do_not_add_repeated_near_full_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    london_a = ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3", "e7e6", "b1c3"]
    london_b = ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3", "c7c5", "g1f3"]
    with database.connection() as db:
        _seed_repertoire(db, [london_a, london_b], [london_a[:5], london_b[:5]])
        priorities.rebuild_introduction_priorities(db, "rep")
        rows = db.execute(
            "SELECT frontier_decisions_json,frontier_reach FROM repertoire_card_introduction_priorities ORDER BY card_id"
        ).fetchall()
    for row in rows:
        decisions = json.loads(row["frontier_decisions_json"])
        assert len(decisions) <= 3
        assert 0 <= row["frontier_reach"] <= 1


def test_common_multi_move_line_can_outrank_a_rare_single_move_line(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    common = ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3", "e7e6", "b1c3"]
    rare = ["c2c4", "e7e5", "b1c3"]
    with database.connection() as db:
        _seed_repertoire(db, [common, rare], [common[:5], rare])
        db.execute(
            """INSERT INTO repertoire_coverage_runs(
                   id,repertoire_id,status,settings_json,total_nodes,completed_nodes,created_at,updated_at
               ) VALUES('run','rep','complete','{}',4,4,?,?)""",
            ("2026-09-20T00:00:00+00:00", "2026-09-20T00:00:00+00:00"),
        )
        _seed_public_probability(db, "common-1", ["d2d4"], "d7d5", 0.9)
        _seed_public_probability(db, "common-2", ["d2d4", "d7d5", "c1f4"], "g8f6", 0.9)
        _seed_public_probability(db, "common-3", ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3"], "e7e6", 0.9)
        _seed_public_probability(db, "rare-1", ["c2c4"], "e7e5", 0.01)
        priorities.rebuild_introduction_priorities(db, "rep")
        scores = {
            row["card_id"]: row["priority_score"]
            for row in db.execute(
                "SELECT card_id,priority_score FROM repertoire_card_introduction_priorities"
            )
        }
    assert scores["card-0"] > scores["card-1"]


def test_single_and_multi_move_cards_use_normalized_frontier_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    line = ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3", "e7e6", "b1c3"]
    with database.connection() as db:
        _seed_repertoire(db, [line], [["d2d4"], line[:5]])
        priorities.rebuild_introduction_priorities(db, "rep")
        values = [
            row["frontier_reach"]
            for row in db.execute(
                "SELECT frontier_reach FROM repertoire_card_introduction_priorities"
            )
        ]
    assert all(0 <= value <= 1 for value in values)


def test_seed_queue_introduces_the_highest_impact_line_first(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    common = ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3", "e7e6", "b1c3"]
    rare = ["c2c4", "e7e5", "b1c3"]
    with database.connection() as db:
        _seed_repertoire(db, [common, rare], [common[:5], rare])
        db.execute("UPDATE settings SET new_cards_per_day=1 WHERE id=1")
        db.execute(
            """INSERT INTO repertoire_coverage_runs(
                   id,repertoire_id,status,settings_json,total_nodes,completed_nodes,created_at,updated_at
               ) VALUES('run','rep','complete','{}',4,4,?,?)""",
            ("2026-09-20T00:00:00+00:00", "2026-09-20T00:00:00+00:00"),
        )
        _seed_public_probability(db, "common-1", ["d2d4"], "d7d5", 0.9)
        _seed_public_probability(db, "common-2", ["d2d4", "d7d5", "c1f4"], "g8f6", 0.9)
        _seed_public_probability(db, "common-3", ["d2d4", "d7d5", "c1f4", "g8f6", "e2e3"], "e7e6", 0.9)
        _seed_public_probability(db, "rare-1", ["c2c4"], "e7e5", 0.01)
        seed_queue(db, "2026-09-20")
        introduced = db.execute(
            "SELECT card_id FROM daily_queue WHERE queue_date='2026-09-20'"
        ).fetchall()
    assert [row["card_id"] for row in introduced] == ["card-0"]


def test_repertoire_listing_exposes_priority_evidence_state(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_repertoire(db, [["d2d4", "d7d5", "c1f4"]], [["d2d4", "d7d5", "c1f4"]])
            priorities.rebuild_introduction_priorities(db, "rep")
        response = client.get("/api/repertoires")
    assert response.status_code == 200
    status = response.json()["repertoires"][0]["introduction_priority"]
    assert status["state"] == "fallback"
    assert status["explorer"] == "unknown"
    assert status["maia"] == "unknown"
