"""Assumed openings bound repertoire analysis without changing study cards."""

import json

import chess
import pytest

from app import database
from app.canonical_prefix_api import save_prefix
from app.services.canonical_prefix import (
    ensure_line_in_scope, game_in_scope, parse_prefix, prefix_projection,
    validate_scoped_line,
)
from app.services.canonical_prefix_preview import (
    execute_prefix_preview_slice, preview_projection, request_preview,
)
from app.services.durable_tasks import claim_task

from app.services.repertoire_coverage import discover_opponent_positions


ITALIAN = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]


def test_italian_prefix_suppresses_sicilian_and_philidor_coverage_nodes():
    positions = discover_opponent_positions([{
        "start_fen": chess.STARTING_FEN, "trained_color": "white",
        "moves_json": json.dumps([*ITALIAN, "f8c5", "c2c3"]),
        "scope_start_ply": len(ITALIAN),
    }], 15)
    assert [position["ply"] for position in positions] == [5, 7]
    assert positions[0]["routes"] == [ITALIAN]
    assert positions[0]["covered_replies"] == ["f8c5"]


@pytest.fixture
def prefix_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "prefix.db")
    database.initialize()
    from app.services.database_executor import database_writer
    database_writer.start()
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('italian','Italian','italian.pgn','2026-10-02')")
    yield "italian"
    database_writer.stop()


def add_line(moves, identifier="main", starting_fen=chess.STARTING_FEN):
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,'italian',?,'white',?,?,'2026-10-02')",
            (identifier, identifier, starting_fen, json.dumps(moves)),
        )


def prepare_prefix(moves=ITALIAN):
    with database.connection() as connection:
        result = request_preview(connection, "italian", moves)
    for _ in range(100):
        task = claim_task("canonical_prefix_preview")
        if task is None:
            break
        execute_prefix_preview_slice(task)
    else:
        pytest.fail("Prefix preview did not finish within its bounded fixture")
    with database.read_connection() as connection:
        return preview_projection(connection, "italian", result["preview_id"])


def apply_preview(preview):
    with database.connection() as connection:
        return save_prefix(connection, {"repertoire_id": "italian", "request": {
            "preview_id": preview["preview_id"], "expected_revision": preview["revision"],
        }})


def test_canonical_prefix_parses_one_legal_san_sequence_and_rejects_nulls():
    assert parse_prefix("1. e4 e5 2.Nf3 Nc6 3. Bc4") == ITALIAN
    assert prefix_projection(ITALIAN)["san"] == "1. e4 e5 2. Nf3 Nc6 3. Bc4"
    assert parse_prefix("") == []
    for invalid in ("e4 e4", "e4 --", "e4 (d4)", '[Event "Italian"] e4', "e4 1-0"):
        with pytest.raises(ValueError):
            parse_prefix(invalid)


def test_canonical_prefix_requires_exact_complete_game_history():
    assert game_in_scope(chess.STARTING_FEN, [*ITALIAN, "g8f6"], ITALIAN)
    assert not game_in_scope(chess.STARTING_FEN, ITALIAN[:-1], ITALIAN)
    assert not game_in_scope(chess.STARTING_FEN, ["e2e4", "e7e5", "g1f3", "d7d6"], ITALIAN)
    assert not game_in_scope(chess.STARTING_FEN, ["g1f3", "b8c6", "e2e4", "e7e5", "f1c4"], ITALIAN)
    assert not game_in_scope(prefix_projection(ITALIAN)["ending_fen"], ["g8f6"], ITALIAN)
    assert game_in_scope(chess.STARTING_FEN, ["d2d4"], [])


def test_canonical_prefix_accepts_matching_stubs_and_verified_continuations():
    assert validate_scoped_line(chess.STARTING_FEN, ITALIAN[:3], ITALIAN, [])["status"] == "valid"
    ending_fen = prefix_projection(ITALIAN)["ending_fen"]
    continuation = validate_scoped_line(ending_fen, ["f8c5", "c2c3"], ITALIAN, ITALIAN)
    assert continuation["status"] == "valid"
    assert continuation["scope_start_ply"] == 0
    assert validate_scoped_line(ending_fen, ["f8c5"], ITALIAN, None)["status"] == "pending"


def test_canonical_prefix_durable_preview_resolves_continuations_in_a_later_pass(prefix_database):
    downstream = prefix_projection([*ITALIAN, "f8c5", "c2c3"])["ending_fen"]
    add_line(["g8f6", "d2d4"], "a-continuation", downstream)
    add_line([*ITALIAN, "f8c5", "c2c3"], "z-opening")
    preview = prepare_prefix()
    assert preview["state"] == "ready"
    assert preview["conflict_count"] == 0
    assert preview["suggestion"]["moves_uci"] == [*ITALIAN, "f8c5", "c2c3"]
    applied = apply_preview(preview)
    assert applied["revision"] == 1
    with database.connection() as connection:
        result = ensure_line_in_scope(connection, "italian", downstream, ["g8f6", "d2d4"])
        assert result["scope_start_ply"] == 0


def test_canonical_prefix_reports_first_conflict_and_disconnected_lines_without_deleting(prefix_database):
    add_line(["e2e4", "e7e5", "g1f3", "d7d6", "f1c4"], "philidor")
    add_line(["d7d5"], "disconnected", prefix_projection(["d2d4"])["ending_fen"])
    preview = prepare_prefix()
    assert preview["state"] == "conflicts"
    assert preview["conflict_count"] == 2
    philidor = next(conflict for conflict in preview["conflicts"] if conflict["name"] == "philidor")
    assert philidor["disagreement_ply"] == 3
    assert "expected Nc6, found d6" in philidor["reason"]
    from fastapi import HTTPException
    with pytest.raises(HTTPException, match="Resolve conflicting"):
        apply_preview(preview)
    with database.read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM repertoire_lines").fetchone()[0] == 2


@pytest.mark.parametrize("mutation", ["insert", "move"])
def test_canonical_prefix_stale_preview_cannot_save_after_a_source_edit(prefix_database, mutation):
    add_line([*ITALIAN, "f8c5", "c2c3"])
    preview = prepare_prefix()
    if mutation == "insert":
        add_line([*ITALIAN, "g8f6", "d2d3"], "new")
    else:
        with database.connection() as connection:
            connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
            connection.execute("UPDATE repertoire_lines SET repertoire_id='other' WHERE id='main'")
    from fastapi import HTTPException
    with pytest.raises(HTTPException, match="check the current prefix"):
        apply_preview(preview)
    with database.read_connection() as connection:
        assert preview_projection(connection, "italian", preview["preview_id"])["state"] == "stale"


@pytest.mark.parametrize("mutation", ["update", "insert", "delete"])
def test_canonical_prefix_preview_rejects_owned_card_changes_without_a_link(prefix_database, mutation):
    add_line([*ITALIAN, "f8c5", "c2c3"])
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('owned','italian','prefix',?,?,'2026-10-02')",
                           (chess.STARTING_FEN, json.dumps(ITALIAN)))
    preview = prepare_prefix()
    with database.connection() as connection:
        if mutation == "update":
            connection.execute("UPDATE cards SET moves_json=? WHERE id='owned'", (json.dumps(["e2e4", "e7e5", "g1f3", "d7d6"]),))
        elif mutation == "insert":
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('another-owned','italian','prefix',?,?,'2026-10-02')",
                               (chess.STARTING_FEN, json.dumps(ITALIAN)))
        else:
            connection.execute("DELETE FROM cards WHERE id='owned'")
    from fastapi import HTTPException
    with pytest.raises(HTTPException, match="check the current prefix"):
        apply_preview(preview)


def test_canonical_prefix_rejects_off_scope_additions_and_clearing_restores_analysis(prefix_database):
    add_line([*ITALIAN, "f8c5", "c2c3"])
    apply_preview(prepare_prefix())
    from fastapi import HTTPException
    with database.connection() as connection:
        with pytest.raises(HTTPException, match="outside the repertoire"):
            ensure_line_in_scope(connection, "italian", chess.STARTING_FEN, ["e2e4", "c7c5", "g1f3"])
    cleared = apply_preview(prepare_prefix([]))
    assert cleared["moves_uci"] == [] and cleared["revision"] == 2
    with database.connection() as connection:
        ensure_line_in_scope(connection, "italian", chess.STARTING_FEN, ["e2e4", "c7c5", "g1f3"])


def test_canonical_prefix_preview_yields_to_foreground_restarts_and_replays_idempotently(prefix_database, monkeypatch):
    import threading
    from app.services import canonical_prefix_preview as preview_service
    from app.services.activity_gate import activity_gate
    add_line([*ITALIAN, "f8c5", "c2c3"])
    with database.connection() as connection:
        preview = request_preview(connection, "italian", ITALIAN)
    task = claim_task("canonical_prefix_preview")
    computing, release_computation = threading.Event(), threading.Event()
    original_validation = preview_service.validate_scoped_line
    def held_validation(*args):
        computing.set()
        assert release_computation.wait(3), "Foreground test did not release computation"
        return original_validation(*args)
    monkeypatch.setattr(preview_service, "validate_scoped_line", held_validation)
    worker_errors = []
    def work():
        try:
            preview_service.execute_prefix_preview_slice(task)
        except Exception as error:
            worker_errors.append(error)
    worker = threading.Thread(target=work)
    worker.start()
    assert computing.wait(3)
    with activity_gate.foreground():
        with database.connection() as connection:
            assert connection.execute("SELECT name FROM repertoires WHERE id='italian'").fetchone()[0] == "Italian"
        release_computation.set()
    worker.join(3)
    assert not worker.is_alive() and not worker_errors
    # A lost acknowledgement must not insert the slice twice.
    preview_service.execute_prefix_preview_slice(task)
    monkeypatch.setattr(preview_service, "validate_scoped_line", original_validation)
    for _ in range(20):
        restarted = claim_task("canonical_prefix_preview")
        if not restarted:
            break
        preview_service.execute_prefix_preview_slice(restarted)
    with database.read_connection() as connection:
        assert preview_projection(connection, "italian", preview["preview_id"])["state"] == "ready"
        assert connection.execute("SELECT COUNT(*) FROM canonical_prefix_results WHERE preview_id=?", (preview["preview_id"],)).fetchone()[0] == 1


def test_matching_game_feedback_starts_after_prefix_while_training_routes_remain_intact():
    from app.services.repertoire_comparison import _compare_game_to_repertoire, _position_graph
    moves = [*ITALIAN, "f8c5", "c2c3"]
    graph = _position_graph([{"start_fen": chess.STARTING_FEN, "moves": moves}])
    comparison = _compare_game_to_repertoire({"id": "game", "color": "white", "start_fen": chess.STARTING_FEN, "moves": moves},
        {"id": "italian", "is_main": 1, "canonical_prefix_moves_json": json.dumps(ITALIAN)}, graph, {})
    assert [event["ply"] for event in comparison["decision_events"]] == [6]
    assert comparison["matched"] == 1
    assert graph[0][position_key_for_test(chess.STARTING_FEN)] == {"e2e4"}


def position_key_for_test(fen):
    return " ".join(chess.Board(fen).fen().split()[:4])


def test_canonical_prefix_probabilities_condition_on_assumed_opponent_moves():
    from app.services.introduction_priorities import IntendedLine, _line_probabilities, _route_signature
    moves = tuple([*ITALIAN, "f8c5", "c2c3"])
    line = IntendedLine("italian", chess.STARTING_FEN, "white", moves, _route_signature(chess.STARTING_FEN, moves), 5)
    probabilities, edges, _ = _line_probabilities([line], {}, {}, 15, 0.0005)
    assert probabilities["italian"] == 1
    assert [value[1]["explorer"] for value in edges.values()][:2] == ["assumed", "assumed"]


def test_canonical_prefix_anchored_continuation_retains_downstream_probabilities_and_absolute_horizon(prefix_database):
    from app.services.canonical_prefix import scope_lines
    from app.services.introduction_priorities import _maximal_intended_lines, _line_probabilities
    add_line(ITALIAN[:3], "opening-stub")
    add_line(["f8c5", "c2c3"], "continuation", prefix_projection(ITALIAN)["ending_fen"])
    apply_preview(prepare_prefix())
    # Shortening the assumption must restore the probability of reaching Bc4.
    apply_preview(prepare_prefix(ITALIAN[:3]))
    with database.read_connection() as connection:
        original_lines = [dict(row) for row in connection.execute("SELECT * FROM repertoire_lines")]
        analysis_lines = scope_lines(connection, "italian", original_lines)
    nodes = discover_opponent_positions(analysis_lines, 3)
    assert [node["ply"] for node in nodes] == [3, 5]
    assert nodes[0]["routes"] == [ITALIAN[:3]]
    assert nodes[0]["covered_replies"] == ["b8c6"]
    intended_lines = _maximal_intended_lines(analysis_lines)
    public = {
        position_key_for_test(prefix_projection(route)["ending_fen"]): {
            "moves": {move: {"explorer_probability": probability}},
            "explorer_status": "complete", "explorer_games": 100,
            "maia_status": "failed",
        }
        for route, move, probability in ((ITALIAN[:3], "b8c6", 0.25), (ITALIAN, "f8c5", 0.5))
    }
    probabilities, _, _ = _line_probabilities(intended_lines, public, {}, 15, 0.0005)
    assert probabilities == {"continuation": 0.125}
    with database.read_connection() as connection:
        assert [dict(row) for row in connection.execute("SELECT * FROM repertoire_lines")] == original_lines


def test_canonical_prefix_black_boundary_preserves_later_position_transpositions():
    from app.services.repertoire_comparison import _compare_game_to_repertoire, _position_graph
    opening = [*ITALIAN, "f8c5", "c2c3", "g8f6", "d2d4"]
    transposed = [*ITALIAN, "g8f6", "c2c3", "f8c5", "d2d4"]
    graph = _position_graph([{"start_fen": chess.STARTING_FEN, "moves": opening}])
    match = _compare_game_to_repertoire({"id": "later", "color": "white", "start_fen": chess.STARTING_FEN, "moves": transposed},
        {"id": "italian", "is_main": 1, "canonical_prefix_moves_json": json.dumps(ITALIAN)}, graph, {})
    assert next(event for event in match["decision_events"] if event["ply"] == 8)["outcome"] == "success"
    black_nodes = discover_opponent_positions([{"start_fen": chess.STARTING_FEN,
        "trained_color": "black", "moves_json": json.dumps(opening), "scope_start_ply": 6}], 15)
    assert [node["ply"] for node in black_nodes] == [6, 8]


def test_canonical_prefix_revisions_are_independent_and_stale_coverage_is_unknown(prefix_database):
    from app.services.repertoire_coverage import coverage_summary, enqueue_coverage_refresh, coverage_gaps
    add_line([*ITALIAN, "f8c5", "c2c3"])
    old_run = enqueue_coverage_refresh("italian")
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        other_before = dict(connection.execute("SELECT * FROM repertoires WHERE id='other'").fetchone())
    first = apply_preview(prepare_prefix())
    assert first["revision"] == 1
    assert coverage_summary("italian")["probability_coverage"] is None
    assert not coverage_summary("italian")["is_complete"]
    assert coverage_gaps("italian") == []
    second = apply_preview(prepare_prefix(ITALIAN[:4]))
    assert second["revision"] == 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        apply_preview(prepare_old_preview_for_test(first))
    with database.read_connection() as connection:
        assert dict(connection.execute("SELECT * FROM repertoires WHERE id='other'").fetchone()) == other_before
        assert connection.execute("SELECT status FROM repertoire_coverage_runs WHERE id=?", (old_run,)).fetchone()[0] == "failed"


def prepare_old_preview_for_test(saved):
    # A previously issued token cannot be borrowed by a newer revision.
    with database.read_connection() as connection:
        row = connection.execute("SELECT id FROM canonical_prefix_previews WHERE expected_revision=? ORDER BY created_at LIMIT 1", (saved["revision"] - 1,)).fetchone()
    return {"preview_id": row["id"], "revision": saved["revision"] - 1}


def test_canonical_prefix_matching_stub_without_continuation_never_reports_complete(prefix_database):
    from app.services.repertoire_coverage import enqueue_coverage_refresh, coverage_summary
    add_line(ITALIAN[:3])
    apply_preview(prepare_prefix())
    enqueue_coverage_refresh("italian")
    summary = coverage_summary("italian")
    assert summary["status"] == "failed"
    assert not summary["is_complete"] and summary["probability_coverage"] is None
    assert "Add a continuation" in summary["last_error"]


def test_canonical_prefix_shared_card_edit_cannot_escape_any_linked_repertoire(prefix_database):
    from app.card_commands import revise_card
    from fastapi import HTTPException
    add_line([*ITALIAN, "f8c5", "c2c3"])
    apply_preview(prepare_prefix())
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('shared','other','prefix',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(ITALIAN)))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('italian','shared')")
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('other','shared')")
        with pytest.raises(HTTPException, match="outside the repertoire"):
            revise_card(connection, {"card_id": "shared", "request": {"expected_revision": 1,
                "starting_fen": chess.STARTING_FEN, "moves": ["e2e4", "e7e5", "g1f3", "d7d6"], "history_mode": "preserve"}})
        assert json.loads(connection.execute("SELECT moves_json FROM cards WHERE id='shared'").fetchone()[0]) == ITALIAN


def test_canonical_prefix_stale_recommendation_worker_cannot_publish(prefix_database):
    from app.services.discovery_admission import execute_recommendation_request_slice
    from app.services.repertoire_opportunities import _publish, list_opportunities
    add_line([*ITALIAN, "f8c5", "c2c3"])
    with database.connection() as connection:
        _publish(connection, repertoire_id="italian", kind="missing_response", fen_key="old-position", target="g8f6", card_id=None, opponent_move_uci="g8f6", score=1, evidence={})
        opportunity = dict(connection.execute("SELECT * FROM repertoire_opportunities").fetchone())
    apply_preview(prepare_prefix())
    with database.read_connection() as connection:
        assert list_opportunities(connection, "italian") == []
    execute_recommendation_request_slice({"payload": {"opportunity_id": opportunity["id"]}})
    with database.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM threat_analysis_requests").fetchone()[0] == 0


def test_canonical_prefix_game_matches_and_statistics_exclude_sicilian_philidor_and_incomplete_games(prefix_database):
    from app.services.repertoire_comparison import compare_all_games
    from app.services.repertoire_statistics import repertoire_statistics
    add_line([*ITALIAN, "f8c5", "c2c3"])
    games = {"italian-game": [*ITALIAN, "f8c5", "c2c3"],
        "sicilian-game": ["e2e4", "c7c5", "g1f3"],
        "philidor-game": ["e2e4", "e7e5", "g1f3", "d7d6", "f1c4"],
        "incomplete-game": ITALIAN[:3]}
    with database.connection() as connection:
        for identifier, moves in games.items():
            connection.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES(?,'lichess','TempoPlayer','2026-10-02T12:00:00+00:00','rapid',1,'white','1-0',?,?)", (identifier, chess.STARTING_FEN, json.dumps(moves)))
    apply_preview(prepare_prefix())
    compare_all_games()
    with database.read_connection() as connection:
        matches = list(connection.execute("SELECT game_id FROM game_repertoire_matches WHERE repertoire_id='italian'"))
        assert [row["game_id"] for row in matches] == ["italian-game"]
        assert connection.execute("SELECT COUNT(*) FROM imported_games").fetchone()[0] == 4
        assert all(row["ply"] >= 5 for row in connection.execute("SELECT ply FROM repertoire_decision_events WHERE repertoire_id='italian'"))
    statistics = repertoire_statistics("italian", "all")
    assert statistics["games"]["matched"] == 1



def test_canonical_prefix_repertoire_api_exposes_only_normalized_typed_metadata(prefix_database):
    from app.main import list_repertoires
    item = list_repertoires()["repertoires"][0]
    assert item["canonical_prefix"] == prefix_projection([], 0)
    assert "canonical_prefix_moves_json" not in item
    assert "canonical_prefix_revision" not in item


@pytest.mark.parametrize("change_prefix_before_admission", [False, True])
def test_canonical_prefix_discovery_admits_verified_new_gap_routes_and_rejects_stale_queued_work(prefix_database, change_prefix_before_admission):
    from app.services.discovery_admission import _materialize_admission_branch
    from app.services.repertoire_coverage import enqueue_coverage_refresh
    from app.services.repertoire_opportunities import _publish
    from app.services.durable_tasks import enqueue_task_in_transaction
    add_line([*ITALIAN, "f8c5", "c2c3"])
    apply_preview(prepare_prefix())
    run_id = enqueue_coverage_refresh("italian")
    starting_fen = prefix_projection([*ITALIAN, "g8f6"])["ending_fen"]
    with database.connection() as connection:
        node = connection.execute("SELECT id,fen_key FROM repertoire_coverage_nodes WHERE run_id=? AND ply=5", (run_id,)).fetchone()
        _publish(connection, repertoire_id="italian", kind="missing_response", fen_key=node["fen_key"], target="g8f6", card_id=None,
            opponent_move_uci="g8f6", score=1, evidence={"coverage_node_id": node["id"]})
        opportunity = connection.execute("SELECT id FROM repertoire_opportunities").fetchone()[0]
        connection.execute("INSERT INTO discovery_admission_intents(id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,selected_move_uci,preview_moves_json,recommendation_json,line_id,created_at,updated_at) VALUES('intent',?,'italian','evidence',?,'d2d3',?,'{}','discovered-line','2026-10-02','2026-10-02')", (opportunity, starting_fen, json.dumps(["d2d3"])))
        enqueue_task_in_transaction(connection, "discovery_admission", "intent", {"intent_id": "intent"})
    task = claim_task("discovery_admission")
    if change_prefix_before_admission:
        apply_preview(prepare_prefix(ITALIAN[:4]))
        with pytest.raises(ValueError, match="canonical prefix changed"):
            _materialize_admission_branch(task)
    else:
        _materialize_admission_branch(task)
        _materialize_admission_branch(task)
    with database.read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM repertoire_lines WHERE id='discovered-line'").fetchone()[0] == int(not change_prefix_before_admission)
        if not change_prefix_before_admission:
            from app.services.canonical_prefix import read_prefix, line_origin
            assert line_origin(connection, read_prefix(connection, "italian")["preview_id"], starting_fen) == [*ITALIAN, "g8f6"]


def test_canonical_prefix_opportunity_publication_locks_repertoire_before_task_to_avoid_foreground_deadlock(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from app.services import repertoire_opportunities
    locks = []
    class Database:
        def execute(self, *_args):
            return SimpleNamespace(fetchone=lambda: {"id": "card"})
    def scoped_metadata(_database, _repertoire_id, *, lock=False):
        if lock:
            locks.append("repertoire")
        return {"revision": 1}
    monkeypatch.setattr(repertoire_opportunities.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(repertoire_opportunities, "read_prefix", scoped_metadata)
    monkeypatch.setattr(repertoire_opportunities, "background_read_connection", lambda: nullcontext(Database()))
    monkeypatch.setattr(repertoire_opportunities, "connection", lambda **_kwargs: nullcontext(Database()))
    monkeypatch.setattr(repertoire_opportunities, "lock_current_slice", lambda *_args: locks.append("task") or True)
    monkeypatch.setattr(repertoire_opportunities, "_load_card_inputs", lambda *_args: None)
    monkeypatch.setattr(repertoire_opportunities, "_load_recurring_decisions", lambda *_args: [])
    monkeypatch.setattr(repertoire_opportunities, "_window_days", lambda *_args: 90)
    monkeypatch.setattr(repertoire_opportunities, "_apply_card_opportunity", lambda *_args: False)
    monkeypatch.setattr(repertoire_opportunities, "_apply_recurring_decisions", lambda *_args: None)
    monkeypatch.setattr(repertoire_opportunities, "_advance_slice", lambda *_args: None)
    assert repertoire_opportunities.execute_opportunity_slice({"payload": {"repertoire_id": "italian", "phase": "cards", "cursor": ""}})
    assert locks == ["repertoire", "task"]
