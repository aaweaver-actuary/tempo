"""Synthetic authoring, provenance, quota, and compatibility regressions."""

from contextlib import nullcontext
from datetime import date
import uuid

import chess
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app import database
from app.models import TacticCaptureRequest
from app.services.tactic_capture import capture_tactic, validate_tactic_line
from app.services.tactic_admission import count_daily_tactic_introductions
from app.services.tactic_capture_migration import normalize_game_tactic_cards
from app.services.tactical_catalog import seed_tactical_introductions


@pytest.fixture
def capture_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "captures.db")
    from app.services import tactical_catalog
    synthetic_records = [{"PuzzleId": f"synthetic-{index}", "FEN": chess.STARTING_FEN,
                          "Moves": f"{move.uci()} a7a6"}
                         for index, move in enumerate(chess.Board().legal_moves)]
    monkeypatch.setattr(tactical_catalog, "pack_records", lambda _pack: synthetic_records)
    database.initialize()
    with database.connection() as connection:
        yield connection


def authored_capture(move="e2e4", **overrides):
    return TacticCaptureRequest(capture_id=uuid.uuid4(), starting_fen=chess.STARTING_FEN,
                               moves=[move], **overrides)


def test_captured_tactic_create_queues_valid_tactic_today(capture_database):
    result = capture_tactic(capture_database, authored_capture(source_kind="puzzle_rush"))
    card = capture_database.execute("SELECT * FROM cards WHERE id=?", (result["card_id"],)).fetchone()
    assert result["queued"] and result["introduced"] and not result["reused"]
    assert (card["content_type"], card["state"], card["scheduling_mode"], card["trained_color"]) == (
        "tactic", "learning", "normal", "white")
    assert card["introduced_at"] == card["due_date"] == date.today().isoformat()
    assert card["repertoire_id"] == "__captured_tactics__"
    queue_entry = capture_database.execute("SELECT * FROM daily_queue").fetchone()
    assert queue_entry["attempt_state"] == "guided" and queue_entry["card_bucket"] == "tactic"
    assert queue_entry["gameplay_priority_reason"] == "Captured tactic · Puzzle Rush"
    assert capture_database.execute("SELECT card_id FROM tactic_captures").fetchone()[0] == card["id"]


def test_captured_tactic_create_is_idempotent(capture_database):
    request = authored_capture()
    first = capture_tactic(capture_database, request)
    queue_before = [tuple(row) for row in capture_database.execute("SELECT * FROM daily_queue")]
    assert capture_tactic(capture_database, request) == first
    assert [tuple(row) for row in capture_database.execute("SELECT * FROM daily_queue")] == queue_before
    for table in ("cards", "tactic_captures", "daily_queue"):
        assert capture_database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    with pytest.raises(HTTPException) as failure:
        capture_tactic(capture_database, request.model_copy(update={"note": "changed"}))
    assert failure.value.status_code == 409


def test_captured_tactic_reuses_existing_tactic_without_erasing_history(capture_database):
    first = capture_tactic(capture_database, authored_capture())
    capture_database.execute(
        "UPDATE cards SET introduced_at='2026-01-01',state='mature',scheduling_mode='light',"
        "interval_days=30,repetitions=7,lapses=2,stability=12,due_date='2099-01-01' WHERE id=?", (first["card_id"],))
    capture_database.execute(
        "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct','2026-01-02',10,30)",
        (first["card_id"],))
    prior_reviews = [tuple(row) for row in capture_database.execute("SELECT * FROM reviews")]
    recaptured = capture_tactic(capture_database, authored_capture(source_kind="other"))
    assert recaptured["card_id"] == first["card_id"] and recaptured["reused"] and not recaptured["introduced"]
    card = capture_database.execute("SELECT * FROM cards").fetchone()
    assert card["introduced_at"] == "2026-01-01"
    assert (card["interval_days"], card["repetitions"], card["lapses"], card["stability"]) == (30, 7, 2, 12)
    assert card["state"] == "learning" and card["scheduling_mode"] == "normal"
    assert card["due_date"] == date.today().isoformat()
    assert [tuple(row) for row in capture_database.execute("SELECT * FROM reviews")] == prior_reviews
    assert capture_database.execute("SELECT COUNT(*) FROM tactic_captures").fetchone()[0] == 2
    assert capture_database.execute("SELECT COUNT(*) FROM daily_queue WHERE status='queued'").fetchone()[0] == 1


@pytest.mark.parametrize("fen,moves", [
    ("invalid", ["e2e4"]), ("8/8/8/8/8/8/8/8 w - - 0 1", ["e2e4"]),
    (chess.STARTING_FEN, ["e2e5"]), (chess.STARTING_FEN, ["e2e4", "e7e4"]),
    (chess.STARTING_FEN, []), (chess.STARTING_FEN, ["0000"]),
])
def test_captured_tactic_rejects_invalid_fen_and_illegal_solution(capture_database, fen, moves):
    with pytest.raises(HTTPException) as failure:
        validate_tactic_line(fen, moves)
    assert failure.value.status_code == 422
    assert capture_database.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


@pytest.mark.parametrize("automatic_before,captured_count,automatic_after", [(0, 3, 2), (2, 3, 0)])
def test_captured_tactics_consume_remaining_daily_tactic_introductions(
        capture_database, automatic_before, captured_count, automatic_after):
    day = date.today().isoformat()
    capture_database.execute("INSERT INTO tactic_pack_activation VALUES('fork-easy-01',1)")
    capture_database.execute("UPDATE settings SET tactics_new_per_day=?", (automatic_before,))
    seed_tactical_introductions(capture_database, day)
    capture_database.execute("UPDATE settings SET tactics_new_per_day=5")
    for move in ["e2e4", "d2d4", "g1f3"][:captured_count]:
        capture_tactic(capture_database, authored_capture(move))
    seed_tactical_introductions(capture_database, day)
    assert count_daily_tactic_introductions(capture_database, day) == 5
    assert capture_database.execute("SELECT COUNT(*) FROM tactic_introductions").fetchone()[0] == automatic_before + automatic_after


def test_capture_after_daily_tactic_allowance_still_queues_without_evicting_existing_tactics(capture_database):
    day = date.today().isoformat()
    capture_database.execute("INSERT INTO tactic_pack_activation VALUES('fork-easy-01',1)")
    seed_tactical_introductions(capture_database, day)
    initial_entries = {row[0] for row in capture_database.execute("SELECT id FROM daily_queue")}
    for move in ["e2e4", "d2d4", "g1f3"]:
        capture_tactic(capture_database, authored_capture(move))
    seed_tactical_introductions(capture_database, day)
    assert count_daily_tactic_introductions(capture_database, day) == 8
    assert capture_database.execute("SELECT COUNT(*) FROM tactic_introductions").fetchone()[0] == 5
    assert initial_entries <= {row[0] for row in capture_database.execute("SELECT id FROM daily_queue WHERE status='queued'")}


def test_recapturing_existing_tactic_does_not_consume_new_tactic_allowance(capture_database):
    captured = capture_tactic(capture_database, authored_capture())
    capture_database.execute("UPDATE cards SET introduced_at='2026-01-01' WHERE id=?", (captured["card_id"],))
    capture_tactic(capture_database, authored_capture())
    capture_database.execute("INSERT INTO tactic_pack_activation VALUES('fork-easy-01',1)")
    seed_tactical_introductions(capture_database, date.today().isoformat())
    assert capture_database.execute("SELECT COUNT(*) FROM tactic_introductions").fetchone()[0] == 5


def test_capture_reopens_finished_queue_even_with_zero_automatic_allowance(capture_database):
    capture_database.execute("UPDATE settings SET tactics_new_per_day=0")
    first = capture_tactic(capture_database, authored_capture())
    capture_database.execute("UPDATE daily_queue SET status='complete'")
    second = capture_tactic(capture_database, authored_capture())
    assert first["card_id"] == second["card_id"]
    assert [tuple(row) for row in capture_database.execute("SELECT status,cycle FROM daily_queue ORDER BY id")] == [
        ("complete", 0), ("queued", 1)]


@pytest.mark.parametrize("column,value", [("content_type", "opening"), ("archived", 1),
                                         ("superseded_by", "replacement"), ("pending_validation", 1)])
def test_capture_conflict_does_not_relabel_or_resurrect_card(capture_database, column, value):
    first = capture_tactic(capture_database, authored_capture())
    capture_database.execute(f"UPDATE cards SET {column}=? WHERE id=?", (value, first["card_id"]))
    before = dict(capture_database.execute("SELECT * FROM cards").fetchone())
    with pytest.raises(HTTPException) as failure:
        capture_tactic(capture_database, authored_capture())
    assert failure.value.status_code == 409
    assert dict(capture_database.execute("SELECT * FROM cards").fetchone()) == before
    assert capture_database.execute("SELECT COUNT(*) FROM tactic_captures").fetchone()[0] == 1


def test_capture_places_after_four_cards_and_replay_does_not_move_it(capture_database):
    for move in ["a2a3", "b2b3", "c2c3", "d2d3", "f2f3"]:
        capture_tactic(capture_database, authored_capture(move))
    request = authored_capture()
    result = capture_tactic(capture_database, request)
    order = [row[0] for row in capture_database.execute("SELECT card_id FROM daily_queue ORDER BY position,id")]
    assert order.index(result["card_id"]) == 4
    capture_tactic(capture_database, request)
    assert order == [row[0] for row in capture_database.execute("SELECT card_id FROM daily_queue ORDER BY position,id")]


def test_game_tactic_migration_normalizes_only_known_plural_rows(capture_database):
    first = capture_tactic(capture_database, authored_capture())
    second = capture_tactic(capture_database, authored_capture("d2d4"))
    capture_database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__game_tactics__','Game tactics','Game','2026-01-01')")
    capture_database.execute("UPDATE cards SET content_type='tactics',repertoire_id='__game_tactics__',archived=1 WHERE id=?", (first["card_id"],))
    capture_database.execute("UPDATE cards SET content_type='tactics' WHERE id=?", (second["card_id"],))
    unrelated_singular = capture_tactic(capture_database, authored_capture("c2c4"))
    capture_database.execute("UPDATE daily_queue SET card_bucket='tactics'")
    assert normalize_game_tactic_cards(capture_database) == 1
    assert normalize_game_tactic_cards(capture_database) == 0
    assert capture_database.execute("SELECT card_bucket FROM daily_queue WHERE card_id=?", (unrelated_singular["card_id"],)).fetchone()[0] == "tactics"
    assert tuple(capture_database.execute("SELECT content_type,archived FROM cards WHERE id=?", (first["card_id"],)).fetchone()) == ("tactic", 1)
    assert capture_database.execute("SELECT content_type FROM cards WHERE id=?", (second["card_id"],)).fetchone()[0] == "tactics"
    assert capture_database.execute("SELECT card_bucket FROM daily_queue WHERE card_id=?", (second["card_id"],)).fetchone()[0] == "tactics"


def test_capture_accepts_black_castling_en_passant_and_underpromotion():
    cases = [
        ("4k3/8/8/8/8/8/8/4K3 b - - 0 1", ["e8d7"], "black"),
        ("4k3/8/8/8/8/8/8/4K2R w K - 0 1", ["e1g1"], "white"),
        ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2", ["e5d6"], "white"),
        ("7k/P7/8/8/8/8/8/4K3 w - - 0 1", ["a7a8n"], "white"),
    ]
    for fen, moves, color in cases:
        normalized_fen, normalized_moves, trained_color = validate_tactic_line(fen, moves)
        assert normalized_fen == fen and normalized_moves == moves and trained_color == color


def test_capture_api_uses_foreground_receipt_and_rejects_mismatched_key(monkeypatch):
    from app import main, command_dispatch
    from fastapi.responses import JSONResponse
    observed = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command", lambda name, payload, *, idempotency_key:
                        observed.append((name, payload, idempotency_key)) or JSONResponse({"operation_id": idempotency_key}, status_code=202))
    body = authored_capture().model_dump(mode="json")
    client = TestClient(main.app)
    assert client.post("/api/tactics/captures", json=body).status_code == 202
    assert observed == [("tactics.capture.create", {"request": body}, body["capture_id"])]
    assert client.post("/api/tactics/captures", json=body, headers={"Idempotency-Key": "different"}).status_code == 422
    assert len(observed) == 1


def test_game_tactic_import_repair_verifies_original_before_recorded_destination_changes(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import repair_verified_game_tactics as repair
    source = tmp_path / "original.db"
    original_bytes = b"unchanged historical source snapshot"
    source.write_bytes(original_bytes)
    calls = []
    recorded = False

    class Result:
        rowcount = 1
        def fetchone(self):
            return (1,) if recorded else None

    class Destination:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, statement, parameters=()):
            nonlocal recorded
            if statement.startswith("UPDATE"):
                calls.append("repair")
            if statement.startswith("INSERT INTO internal_migrations"):
                recorded = True
                calls.append("record")
                assert parameters[0].startswith("tactic-capture-post-import-v1:")
            return Result()

    monkeypatch.setattr(repair.psycopg, "connect", lambda _dsn: Destination())
    def verify(path, dsn, verify_only):
        assert path == source and dsn == "destination" and verify_only
        assert source.read_bytes() == original_bytes
        calls.append("verify")
    monkeypatch.setattr(repair, "migrate", verify)
    assert repair.repair_verified_import(source, "destination") == 1
    assert calls == ["verify", "repair", "repair", "record"]
    assert repair.repair_verified_import(source, "destination") == 0
    assert source.read_bytes() == original_bytes


def test_game_tactic_import_repair_never_mutates_after_failed_exact_verification(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import repair_verified_game_tactics as repair
    source = tmp_path / "original.db"
    source.write_bytes(b"original")
    class Destination:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, statement, _parameters):
            assert statement.startswith("SELECT")
            class Result:
                def fetchone(self): return None
            return Result()
    monkeypatch.setattr(repair.psycopg, "connect", lambda _dsn: Destination())
    def fail_verify(*_, **__): raise RuntimeError("exact comparison failed")
    monkeypatch.setattr(repair, "migrate", fail_verify)
    with pytest.raises(RuntimeError, match="exact comparison failed"):
        repair.repair_verified_import(source, "destination")
    assert source.read_bytes() == b"original"
