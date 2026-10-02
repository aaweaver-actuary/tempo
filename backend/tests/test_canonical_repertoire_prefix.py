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


def test_canonical_prefix_refresh_preserves_previously_handled_discoveries(prefix_database):
    from app.services.repertoire_opportunities import _publish, list_opportunities
    add_line([*ITALIAN, "f8c5", "c2c3"])
    evidence = {"supporting_games": 1}
    publication = dict(repertoire_id="italian", kind="missing_response", fen_key="position", target="g8f6",
                       card_id=None, opponent_move_uci="g8f6", score=1, evidence=evidence)
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('accepted-card','italian','prefix',?,?,'2026-10-02')",
                           (chess.STARTING_FEN, json.dumps(ITALIAN)))
        _publish(connection, **publication)
        connection.execute("UPDATE repertoire_opportunities SET handled_evidence_json=evidence_json,admission_state='queued',admitted_card_id='accepted-card'")
    apply_preview(prepare_prefix())
    with database.connection() as connection:
        _publish(connection, **publication)
        opportunity = connection.execute("SELECT * FROM repertoire_opportunities").fetchone()
        assert opportunity["canonical_prefix_revision"] == 1
        assert json.loads(opportunity["handled_evidence_json"]) == evidence
        assert opportunity["admission_state"] == "queued" and opportunity["admitted_card_id"] == "accepted-card"
        assert list_opportunities(connection, "italian") == []


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


@pytest.fixture
def quiet_prefix_writes(monkeypatch):
    from app import main
    for name in ('enqueue_opening_graph_rebuild', 'enqueue_integrity_scans', 'enqueue_coverage_refresh'):
        monkeypatch.setattr(main, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(main.coordinator, 'wake', lambda: None)


@pytest.mark.parametrize('mutation', ['delete', 'change'])
def test_canonical_prefix_current_route_required_after_source_disappears_at_branch_boundary(prefix_database, quiet_prefix_writes, mutation):
    from app.main import branch
    from app.models import BranchRequest
    from fastapi import HTTPException
    from app.services.canonical_prefix import line_origin, read_prefix
    route = [*ITALIAN, 'f8c5', 'c2c3']
    add_line(route, 'route-source')
    apply_preview(prepare_prefix())
    starting_fen = prefix_projection(route)['ending_fen']
    request = BranchRequest(repertoire_id='italian', name='Anchored', trained_color='white', starting_fen=starting_fen, moves=['g8f6'])
    assert branch(request)['id']
    # Recertify the now-current source state before removing its only rooted route.
    prepare_prefix()
    with database.connection() as connection:
        if mutation == 'delete':
            connection.execute("DELETE FROM repertoire_lines WHERE id='route-source'")
        else:
            connection.execute("UPDATE repertoire_lines SET moves_json=? WHERE id='route-source'", (json.dumps([*ITALIAN, 'g8f6']),))
    with database.read_connection() as connection:
        assert line_origin(connection, read_prefix(connection, 'italian')['preview_id'], starting_fen) is None
    with pytest.raises(HTTPException, match='outside the repertoire'):
        branch(BranchRequest(**{**request.model_dump(), 'moves': ['d7d6']}))
    # The old continuation must not self-certify through its historical origin.
    assert prepare_prefix()['state'] == 'conflicts'
    branch(BranchRequest(repertoire_id='italian', name='Restored route', trained_color='white', starting_fen=chess.STARTING_FEN, moves=route))
    assert prepare_prefix()['state'] == 'ready'
    assert branch(BranchRequest(**{**request.model_dump(), 'moves': ['d7d6']}))['id']


def test_sqlite_unrestricted_zero_node_coverage_preserves_complete_empty_run(prefix_database):
    from app.services.repertoire_coverage import enqueue_coverage_refresh
    run_id = enqueue_coverage_refresh('italian')
    with database.read_connection() as connection:
        run = connection.execute('SELECT * FROM repertoire_coverage_runs WHERE id=?', (run_id,)).fetchone()
    assert run['status'] == 'complete'
    assert run['last_error'] is None and run['total_nodes'] == 0


def study_snapshot():
    with database.read_connection() as connection:
        return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in ('repertoires', 'repertoire_lines', 'cards', 'repertoire_cards', 'card_revisions', 'reviews', 'daily_queue', 'position_annotations', 'background_tasks')}


@pytest.mark.parametrize('linked_prefix', [ITALIAN, ['e2e4', 'e7e5', 'g1f3', 'd7d6']])
def test_sqlite_shared_card_edit_validates_all_memberships_before_study_mutation(prefix_database, quiet_prefix_writes, linked_prefix):
    from app.main import revise_card
    from app.models import CardRevisionRequest
    from fastapi import HTTPException
    add_line(ITALIAN)
    apply_preview(prepare_prefix())
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at,canonical_prefix_moves_json) VALUES('other','Other','other.pgn','2026-10-02',?)", (json.dumps(linked_prefix),))
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('shared','other','prefix',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(['e2e4', 'e7e5', 'g1f3'])))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('italian','shared')")
    before = study_snapshot()
    invalid_moves = ['e2e4', 'e7e5', 'g1f3', 'd7d6'] if linked_prefix == ITALIAN else ITALIAN
    with pytest.raises(HTTPException, match='outside the repertoire'):
        revise_card('shared', CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=invalid_moves, history_mode='preserve', expected_revision=1))
    assert study_snapshot() == before
    valid_moves = ITALIAN if linked_prefix == ITALIAN else ['e2e4', 'e7e5', 'g1f3']
    assert revise_card('shared', CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=valid_moves, history_mode='preserve', expected_revision=1))['card_id']


def test_sqlite_prefixed_pgn_reimport_validates_all_candidates_atomically(prefix_database, quiet_prefix_writes):
    import asyncio
    from io import BytesIO
    from fastapi import HTTPException, UploadFile
    from app.main import import_pgn
    add_line(ITALIAN)
    apply_preview(prepare_prefix())
    def run_import(text):
        return asyncio.run(import_pgn(UploadFile(filename='italian.pgn', file=BytesIO(text.encode())), 'white', 3, None))
    before = study_snapshot()
    with pytest.raises(HTTPException, match='outside the repertoire'):
        run_import('[Event "Matching"]\n\n1. e4 {new annotation} e5 2. Nf3 Nc6 3. Bc4 Bc5 *\n\n[Event "Off scope"]\n\n1. e4 e5 2. Nf3 d6 *')
    assert study_snapshot() == before
    result = run_import('[Event "Matching"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 *')
    assert result.repertoire_id == 'italian'
    assert len(study_snapshot()['repertoire_lines']) == 2


def test_canonical_prefix_identical_previews_reuse_work_and_version_changes_create_new_scan(prefix_database):
    with database.connection() as connection:
        first = request_preview(connection, 'italian', ITALIAN)
        second = request_preview(connection, 'italian', ITALIAN)
        assert first['preview_id'] == second['preview_id']
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='canonical_prefix_preview'").fetchone()[0] == 1
    ready = prepare_prefix()
    assert ready['preview_id'] == first['preview_id']
    add_line(ITALIAN)
    changed_source = prepare_prefix()
    assert changed_source['preview_id'] != first['preview_id']
    apply_preview(changed_source)
    changed_prefix_revision = prepare_prefix()
    assert changed_prefix_revision['preview_id'] != changed_source['preview_id']


def test_canonical_prefix_preview_retention_is_bounded_restartable_and_preserves_active_certificate(prefix_database):
    from app.services.durable_tasks import requeue_interrupted_tasks
    from app.services.canonical_prefix import line_origin, read_prefix
    add_line([*ITALIAN, 'f8c5'])
    apply_preview(prepare_prefix())
    with database.read_connection() as connection:
        active_id = read_prefix(connection, 'italian')['preview_id']
    for length in range(1, 6):
        with database.connection() as connection:
            request_preview(connection, 'italian', ITALIAN[:min(length, 4)])
            connection.execute('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=?', ('italian',))
            request_preview(connection, 'italian', ITALIAN[:min(length, 4)])
    # A lost lease resumes through normal restart recovery; no scan is reset by reuse.
    claimed = claim_task('canonical_prefix_preview')
    with database.connection() as connection:
        connection.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=?", (claimed['id'],))
    requeue_interrupted_tasks()
    for _ in range(600):
        task = claim_task('canonical_prefix_preview')
        if not task:
            break
        assert execute_prefix_preview_slice(task)
        # Replaying the same delivery cannot delete the next row or republish.
        assert not execute_prefix_preview_slice(task)
    else:
        pytest.fail('Preview retention did not finish within its bounded fixture')
    with database.read_connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM canonical_prefix_previews').fetchone()[0] <= 9
        assert connection.execute('SELECT 1 FROM canonical_prefix_previews WHERE id=?', (active_id,)).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='canonical_prefix_preview'").fetchone()[0] <= 9
        assert not connection.execute('SELECT 1 FROM canonical_prefix_positions position LEFT JOIN canonical_prefix_previews preview ON preview.id=position.preview_id WHERE preview.id IS NULL').fetchone()
        # Historical certificates remain retained for history, never authoritative.
        assert line_origin(connection, active_id, prefix_projection([*ITALIAN, 'f8c5'])['ending_fen']) is None


def test_canonical_prefix_card_root_recertifies_connected_lines_but_cannot_resurrect_deleted_anchor(prefix_database):
    from app.services.canonical_prefix import line_origin, read_prefix
    route = [*ITALIAN, 'f8c5', 'c2c3']
    starting_fen = prefix_projection(route)['ending_fen']
    add_line(['g8f6'], 'anchored', starting_fen)
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('root-card','italian','prefix',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(route)))
    preview = prepare_prefix()
    assert preview['state'] == 'ready'
    apply_preview(preview)
    with database.connection() as connection:
        prefix = read_prefix(connection, 'italian')
        assert line_origin(connection, prefix['preview_id'], starting_fen) == route
        connection.execute("DELETE FROM cards WHERE id='root-card'")
        assert line_origin(connection, prefix['preview_id'], starting_fen) is None
    assert prepare_prefix()['state'] == 'conflicts'


def test_sqlite_integrity_line_rewrite_cannot_escape_canonical_scope(prefix_database):
    from app.services.repertoire_integrity import _rewrite_line
    from fastapi import HTTPException
    add_line(ITALIAN)
    apply_preview(prepare_prefix())
    before = study_snapshot()
    with pytest.raises(HTTPException, match='outside the repertoire'):
        with database.connection() as connection:
            line = connection.execute("SELECT * FROM repertoire_lines WHERE id='main'").fetchone()
            _rewrite_line(connection, line, ['e2e4', 'e7e5', 'g1f3', 'd7d6'])
    assert study_snapshot() == before


def test_canonical_prefix_unverified_continuation_never_reports_partial_routes_as_complete(prefix_database):
    from app.services.repertoire_coverage import enqueue_coverage_refresh, coverage_summary
    route = [*ITALIAN, 'f8c5', 'c2c3']
    add_line(route, 'root')
    add_line(['g8f6'], 'anchored', prefix_projection(route)['ending_fen'])
    apply_preview(prepare_prefix())
    # An unrelated additive mutation also invalidates the old source certificate.
    add_line([*ITALIAN, 'g8f6'], 'other')
    enqueue_coverage_refresh('italian')
    summary = coverage_summary('italian')
    assert summary['status'] == 'failed' and not summary['is_complete']
    assert 'verification' in summary['last_error']
    assert prepare_prefix()['state'] == 'ready'


def test_canonical_prefix_admission_rechecks_source_version_before_inserting(prefix_database, monkeypatch):
    from app.services import discovery_admission
    from app.services.durable_tasks import enqueue_task_in_transaction
    from app.services.repertoire_opportunities import _publish
    add_line(ITALIAN)
    apply_preview(prepare_prefix())
    with database.connection() as connection:
        _publish(connection, repertoire_id='italian', kind='missing_response', fen_key=position_key_for_test(prefix_projection(ITALIAN)['ending_fen']), target='f8c5', card_id=None, opponent_move_uci='f8c5', score=1, evidence={})
        opportunity_id = connection.execute('SELECT id FROM repertoire_opportunities').fetchone()[0]
        connection.execute("INSERT INTO discovery_admission_intents(id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,selected_move_uci,preview_moves_json,recommendation_json,line_id,created_at,updated_at) VALUES('race',?,'italian','evidence',?,'f8c5','[\"f8c5\"]','{}','race-line','2026-10-02','2026-10-02')", (opportunity_id, prefix_projection(ITALIAN)['ending_fen']))
        enqueue_task_in_transaction(connection, 'discovery_admission', 'race', {'intent_id': 'race'})
    original_validate = discovery_admission.validate_scoped_line
    def mutate_after_validation(*args):
        result = original_validate(*args)
        add_line([*ITALIAN, 'g8f6'], 'concurrent-edit')
        return result
    monkeypatch.setattr(discovery_admission, 'validate_scoped_line', mutate_after_validation)
    with pytest.raises(ValueError, match='routes changed'):
        discovery_admission._materialize_admission_branch(claim_task('discovery_admission'))
    with database.read_connection() as connection:
        assert not connection.execute("SELECT 1 FROM repertoire_lines WHERE id='race-line'").fetchone()


def test_integrity_shared_card_replacement_belongs_only_to_validated_repertoire(prefix_database):
    from app.services.repertoire_integrity import _rewrite_card
    add_line(ITALIAN)
    apply_preview(prepare_prefix())
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at,canonical_prefix_moves_json) VALUES('other','Philidor','other.pgn','2026-10-02',?)", (json.dumps(['e2e4', 'e7e5', 'g1f3', 'd7d6']),))
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('shared-stub','other','prefix',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(ITALIAN[:3])))
        for repertoire_id in ('italian', 'other'):
            connection.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)', (repertoire_id, 'shared-stub'))
        source_card = connection.execute("SELECT * FROM cards WHERE id='shared-stub'").fetchone()
        assert _rewrite_card(connection, 'italian', source_card, ITALIAN)
        replacement = connection.execute("SELECT * FROM cards WHERE id<>'shared-stub'").fetchone()
        assert replacement['repertoire_id'] == 'italian'
        assert connection.execute("SELECT archived FROM cards WHERE id='shared-stub'").fetchone()[0] == 0
        assert connection.execute("SELECT repertoire_id FROM repertoire_cards WHERE card_id=?", (replacement['id'],)).fetchone()[0] == 'italian'
