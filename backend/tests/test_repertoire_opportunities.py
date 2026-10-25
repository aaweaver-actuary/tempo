"""Retained discovery admission/cache behavior; opportunity production is retired."""

from datetime import date, datetime, timezone
import json

import threading

import time

from concurrent.futures import ThreadPoolExecutor

import chess

from tempo_legacy_database import connections as database

from app.services.durable_tasks import claim_task, enqueue_task_in_transaction
from tempo_legacy_database.adapters.database_executor import database_writer

from app.services import discovery_admission
from app.services.discovery_admission import create_admission_intent, execute_admission_intent_slice
from app.services.discovery_admission import _cached_repertoire_positions



def test_discovery_recommendation_cache_rebuilds_after_line_change():
    source = ("one", "Line", chess.STARTING_FEN, '["e2e4"]', "white")
    changed = ("one", "Line", chess.STARTING_FEN, '["d2d4"]', "white")
    first_positions, _ = _cached_repertoire_positions((source,), "white")
    changed_positions, _ = _cached_repertoire_positions((changed,), "white")
    assert first_positions[0]["next_move"] == "e2e4"
    assert changed_positions[0]["next_move"] == "d2d4"



def _seed_decision_route(db):
    now = datetime.now(timezone.utc).isoformat()
    start = chess.STARTING_FEN
    board = chess.Board()
    root_key = " ".join(board.fen().split()[:4])
    board.push_uci("e2e4")
    board.push_uci("e7e5")
    target_key = " ".join(board.fen().split()[:4])
    db.execute("INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES('rep','Main','main.pgn',?,1)", (now,))
    db.execute("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                  VALUES('line','rep','Main','white',?,?,?)""", (start, json.dumps(["e2e4", "e7e5", "g1f3"]), now))
    for card_id, card_start, moves, state in (
        ("root", start, ["e2e4"], "new"),
        ("target", board.fen(), ["g1f3"], "locked"),
    ):
        db.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color)
                      VALUES(?,?,?,?,?,?,?,'opening','white')""",
                   (card_id, "rep", "response", card_start, json.dumps(moves), state, date.today().isoformat()))
        db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',?)", (card_id,))
    db.execute("""INSERT INTO opening_graph_publications(repertoire_id,generation,published_at)
                  VALUES('rep',1,?)""", (now,))
    for index, card_id, parent, key, moves in (
        (0, "root", None, root_key, ["e2e4"]),
        (1, "target", "root", target_key, ["g1f3"]),
    ):
        db.execute("""INSERT INTO opening_graph_steps(
            repertoire_id,generation,line_id,decision_index,segment_kind,
            first_decision_index,last_decision_index,decision_fen_keys_json,
            card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color)
            VALUES('rep',1,'line',?,'decision',?,?,?, ?,?,?, ?,?,'white')""",
            (index, index, index, json.dumps([key]), card_id, parent, key,
             start if index == 0 else board.fen(), json.dumps(moves)))
    return root_key, target_key



def test_discovery_admission_replay_promotes_stalled_save_ahead_of_recurring_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    created_at = datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        _seed_decision_route(db)
        db.execute(
            """INSERT INTO repertoire_opportunities(
                 id,repertoire_id,kind,fen_key,score,evidence_json,evidence_fingerprint,
                 created_at,updated_at)
               VALUES('opportunity','rep','missing_response','position',1,'{}','fingerprint',?,?)""",
            (created_at, created_at),
        )
        db.execute(
            """INSERT INTO discovery_admission_intents(
                 id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,
                 selected_move_uci,preview_moves_json,recommendation_json,line_id,created_at,updated_at)
               VALUES('intent','opportunity','rep','fingerprint',?,'e2e4','["e2e4"]',
                      '{}','new-line',?,?)""",
            (chess.STARTING_FEN, created_at, created_at),
        )
        enqueue_task_in_transaction(db, "repertoire_game_refresh", "all", {}, priority=90)
        enqueue_task_in_transaction(
            db, "discovery_admission", "intent", {"intent_id": "intent"}, priority=125,
        )
    replayed = create_admission_intent("opportunity", "e2e4", "fingerprint")
    assert replayed["id"] == "intent"
    with database.read_connection() as db:
        task = db.execute(
            "SELECT priority FROM background_tasks WHERE kind='discovery_admission' AND deduplication_key='intent'"
        ).fetchone()
        assert task["priority"] == 80
    database_writer.start()
    try:
        claimed = claim_task()
    finally:
        database_writer.stop()
    assert claimed["kind"] == "discovery_admission"
    assert claimed["deduplication_key"] == "intent"



def test_discovery_familiar_default_prefers_quality_within_one_hundred_cp():
    rank = discovery_admission._rank_candidates
    candidates = [
        {"move_uci": "a2a3", "loss_cp": 0, "familiar": False},
        {"move_uci": "f3e5", "loss_cp": 100, "familiar": True},
        {"move_uci": "d1e2", "loss_cp": 35, "familiar": True},
        {"move_uci": "f2f4", "loss_cp": 101, "familiar": True},
        {"move_uci": "b1c3", "loss_cp": 31, "familiar": False},
        {"move_uci": "h2h3", "loss_cp": 30, "familiar": False},
    ]
    assert [item["move_uci"] for item in rank(candidates)] == ["d1e2", "f3e5", "a2a3", "h2h3"]
    assert rank([{"move_uci": "a2a3", "loss_cp": 31, "familiar": False}]) == []



def test_discovery_branch_materialization_keeps_foreground_free_and_replays_once(tmp_path, monkeypatch, request):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    database_writer.start()
    request.addfinalizer(database_writer.stop)
    with database.connection() as db:
        _, target_key = _seed_decision_route(db)
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            """INSERT INTO repertoire_opportunities(
                 id,repertoire_id,kind,fen_key,status,score,evidence_json,evidence_fingerprint,
                 created_at,updated_at) VALUES('gap','rep','missing_response',?,'active',1,'{}','revision',?,?)""",
            (target_key, now, now),
        )
        db.execute(
            """INSERT INTO discovery_admission_intents(
                 id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,
                 selected_move_uci,preview_moves_json,recommendation_json,line_id,created_at,updated_at)
               VALUES('intent','gap','rep','revision',?,'b1c3','["b1c3"]','{}','new-line',?,?)""",
            (chess.STARTING_FEN, now, now),
        )
    discovery_admission.enqueue_admission_intent("intent")
    task = claim_task("discovery_admission")
    assert task is not None
    entered = threading.Event()
    release = threading.Event()
    original_push = chess.Board.push_uci

    def paused_push(board, move_uci):
        if move_uci == "b1c3" and threading.current_thread().name != "MainThread":
            entered.set()
            assert release.wait(timeout=5)
        return original_push(board, move_uci)

    monkeypatch.setattr(chess.Board, "push_uci", paused_push)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(execute_admission_intent_slice, task)
        try:
            assert entered.wait(timeout=5)
            started = time.monotonic()
            with database.connection() as db:
                db.execute("UPDATE settings SET initial_depth=initial_depth WHERE id=1")
            with database.read_connection() as db:
                assert db.execute("SELECT state FROM discovery_admission_intents WHERE id='intent'").fetchone()[0] == "preparing"
            assert time.monotonic() - started < 1
        finally:
            release.set()
        assert result.result(timeout=5) is True
    with database.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM repertoire_lines WHERE id='new-line'").fetchone()[0] == 1
        graph_task = db.execute(
            "SELECT generation FROM background_tasks WHERE kind='opening_graph_rebuild' AND deduplication_key='rep'",
        ).fetchone()
        assert graph_task is not None
    assert execute_admission_intent_slice(task) is True
    with database.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM repertoire_lines WHERE id='new-line'").fetchone()[0] == 1
        assert db.execute(
            "SELECT generation FROM background_tasks WHERE kind='opening_graph_rebuild' AND deduplication_key='rep'",
        ).fetchone()[0] == graph_task["generation"]



def test_discovery_same_move_recognizes_comparable_repertoire_position():
    examples, _ = discovery_admission._repertoire_positions([{
        "id": "line", "name": "Opening", "start_fen": chess.STARTING_FEN,
        "moves_json": json.dumps(["e2e4", "e7e5", "g1f3"]),
    }], "white")
    board = chess.Board()
    board.push_uci("e2e4")
    board.push_uci("h7h6")
    matched = discovery_admission._comparable_move_example(examples, board, "white", "g1f3")
    assert matched and matched["line_id"] == "line"
    assert discovery_admission._comparable_move_example(examples, board, "white", "d2d4") is None
    board.push_uci("d2d4")
    board.push_uci("h6h5")
    assert discovery_admission._comparable_move_example(examples, board, "white", "g1f3") is None
