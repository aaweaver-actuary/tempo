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
        # Canonical prefix decisions remain evidence even when the exact route
        # is excluded from opening classification and coverage statistics.
        assert connection.execute("SELECT COUNT(*) FROM repertoire_decision_events WHERE repertoire_id='italian' AND ply<5").fetchone()[0] > 0
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
        return {"canonical_prefix_revision": 1, "canonical_scope_source_revision": 2, "canonical_scope_preview_id": "checked"}
    monkeypatch.setattr(repertoire_opportunities, "game_scope_generation", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(repertoire_opportunities.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(repertoire_opportunities, "scope_identity", scoped_metadata)
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


def test_canonical_prefix_retired_preview_cannot_save_during_bounded_cleanup(prefix_database):
    from fastapi import HTTPException
    route = [*ITALIAN, 'f8c5', 'c2c3', 'g8f6', 'd2d3']
    add_line(route)
    old = prepare_prefix()
    with database.connection() as connection:
        # Keep a partially retired certificate observable after a 64-row batch.
        for ordinal in range(70):
            connection.execute("INSERT INTO canonical_prefix_results(preview_id,item_id,name,status) VALUES(?,?,?,'valid')",
                               (old['preview_id'], f'line:retention-{ordinal}', f'Retention line {ordinal}'))
        for length in range(len(route) + 1):
            if length != len(ITALIAN):
                request_preview(connection, 'italian', route[:length])
    for _ in range(150):
        task = claim_task('canonical_prefix_preview')
        assert task is not None
        execute_prefix_preview_slice(task)
        with database.read_connection() as connection:
            state = connection.execute('SELECT state FROM canonical_prefix_previews WHERE id=?', (old['preview_id'],)).fetchone()
        if state and state['state'] == 'stale':
            break
    else:
        pytest.fail('Old preview was not retired by bounded retention')
    # Its source tuple is still current, but its certificate is being dismantled.
    with pytest.raises(HTTPException, match='check the current prefix again'):
        apply_preview(old)


def test_canonical_coverage_source_change_hides_complete_run_and_stale_maia(prefix_database):
    from app.services import repertoire_coverage as coverage
    add_line([*ITALIAN, "f8c5", "c2c3", "g8f6"])
    apply_preview(prepare_prefix())
    run_id = coverage.enqueue_coverage_refresh("italian")
    with database.connection() as connection:
        connection.execute("UPDATE repertoire_coverage_runs SET status='complete' WHERE id=?", (run_id,))
        connection.execute("UPDATE repertoire_coverage_nodes SET explorer_status='complete',explorer_games=1000 WHERE run_id=?", (run_id,))
        connection.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,required,covered,blended_probability,source_state) SELECT id,'f8c5',1,1,1,'explorer-only' FROM repertoire_coverage_nodes WHERE run_id=?", (run_id,))
    assert coverage.coverage_summary("italian")["is_complete"]
    lease = coverage.claim_maia_coverage_node()
    assert lease is not None
    with database.connection() as connection:
        connection.execute("UPDATE repertoire_lines SET moves_json=? WHERE id='main'", (json.dumps([*ITALIAN, "g8f6", "d2d3"]),))
    assert coverage.coverage_summary("italian")["run_id"] is None
    assert coverage.coverage_gaps("italian") == []
    assert coverage.claim_maia_coverage_node() is None
    with pytest.raises(RuntimeError, match="no longer active"):
        coverage.submit_maia_coverage(lease["node_id"], lease["lease_id"], [{"move_uci": "f8c5", "probability": 1}])
    apply_preview(prepare_prefix())
    fresh_run = coverage.enqueue_coverage_refresh("italian")
    assert fresh_run != run_id
    assert coverage.coverage_summary("italian")["run_id"] == fresh_run


def test_canonical_graph_materialization_preserves_authoritative_source_revision(prefix_database):
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    from app.services.canonical_prefix import read_prefix
    add_line([*ITALIAN, "f8c5", "c2c3"])
    apply_preview(prepare_prefix())
    with database.read_connection() as connection:
        source_revision = read_prefix(connection, "italian")["source_revision"]
    enqueue_opening_graph_rebuild("italian")
    task = claim_task("opening_graph_rebuild")
    assert task is not None
    execute_opening_graph_rebuild(task)
    with database.read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM cards WHERE content_type='opening'").fetchone()[0] > 0
        assert read_prefix(connection, "italian")["source_revision"] == source_revision


def _set_other_prefix(identifier, moves):
    with database.connection() as connection:
        preview = request_preview(connection, identifier, moves)
    for _ in range(100):
        task = claim_task("canonical_prefix_preview")
        if task is None:
            break
        execute_prefix_preview_slice(task)
    with database.connection() as connection:
        save_prefix(connection, {"repertoire_id": identifier, "request": {
            "preview_id": preview["preview_id"], "expected_revision": preview["revision"]}})


@pytest.mark.parametrize("change", ["newly-eligible", "newly-ineligible", "null-primary", "same-prefix"])
def test_canonical_global_game_scope_hides_other_primary_and_null_comparisons(prefix_database, change):
    from app.services.repertoire_comparison import compare_all_games
    from app.services.repertoire_statistics import repertoire_statistics
    from app.services.canonical_scope_freshness import game_scope_generation
    add_line(ITALIAN[:1])
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        connection.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('other-line','other','Other','white',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(ITALIAN[:3])))
    if change != "newly-ineligible":
        _set_other_prefix("other", ITALIAN)
    if change in {"null-primary", "same-prefix"}:
        apply_preview(prepare_prefix())
    moves = ITALIAN if change == "same-prefix" else ["e2e4", "e7e5", "g1f3", "d7d6", "f1c4"]
    with database.connection() as connection:
        connection.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('scope-game','lichess','TempoPlayer','2026-10-02T12:00:00+00:00','rapid',1,'white','1-0',?,?)", (chess.STARTING_FEN, json.dumps(moves)))
    compare_all_games()
    from app.services.statistics import refresh_game_features, statistics_breakdown
    refresh_game_features("scope-game")
    assert statistics_breakdown("repertoire", 3650)["segments"]
    from app.services.game_findings import _upsert_finding
    with database.connection() as connection:
        _upsert_finding(connection, game_id="scope-game", analysis_version=1, ply=0, kind="first big mistake", confidence=1, evidence={})
        if change != "null-primary":
            _upsert_finding(connection, game_id="scope-game", analysis_version=1, ply=1, kind="repertoire gap", confidence=1, evidence={}, repertoire_id="italian")
    with database.read_connection() as connection:
        generation = game_scope_generation(connection)
        before = connection.execute("SELECT * FROM current_repertoire_comparisons WHERE game_id='scope-game'").fetchone()
        assert before is not None
    _set_other_prefix("other", ITALIAN if change in {"newly-ineligible", "same-prefix"} else [])
    if change == "same-prefix":
        with database.read_connection() as connection:
            assert game_scope_generation(connection) == generation
            assert connection.execute("SELECT 1 FROM current_repertoire_comparisons WHERE game_id='scope-game'").fetchone()
        return
    assert repertoire_statistics("italian", "all")["games"]["matched"] == 0
    assert statistics_breakdown("repertoire", 3650)["segments"] == []
    assert statistics_breakdown("color", 3650)["segments"][0]["games"] == 1
    with database.read_connection() as connection:
        assert game_scope_generation(connection) > generation
        for table in ("game_repertoire_matches", "repertoire_comparisons", "repertoire_decision_events"):
            assert not connection.execute(f"SELECT 1 FROM current_{table} WHERE game_id='scope-game'").fetchone()
        assert connection.execute("SELECT 1 FROM imported_games WHERE id='scope-game'").fetchone()
        assert [row["kind"] for row in connection.execute("SELECT kind FROM current_game_findings WHERE game_id='scope-game'")] == ["first big mistake"]
    compare_all_games()
    with database.read_connection() as connection:
        primary = connection.execute("SELECT repertoire_id FROM current_repertoire_comparisons WHERE game_id='scope-game'").fetchone()[0]
        assert primary == ("italian" if change == "newly-ineligible" else "other")


def _complete_gap_run():
    from app.services.repertoire_coverage import enqueue_coverage_refresh
    run_id = enqueue_coverage_refresh("italian")
    with database.connection() as connection:
        connection.execute("UPDATE repertoire_coverage_runs SET status='complete' WHERE id=?", (run_id,))
        connection.execute("UPDATE repertoire_coverage_nodes SET explorer_status='complete',explorer_games=1000 WHERE run_id=?", (run_id,))
        connection.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,explorer_probability,required,covered,blended_probability,source_state) SELECT id,'a7a6',0.5,1,0,0.5,'explorer-only' FROM repertoire_coverage_nodes WHERE run_id=? AND ply=5", (run_id,))
    return run_id


def test_canonical_opportunity_compute_source_race_discards_then_rebuilds(prefix_database, monkeypatch):
    from app.services import repertoire_opportunities as opportunities
    from app.services.durable_tasks import enqueue_task_in_transaction
    add_line([*ITALIAN, "f8c5"])
    apply_preview(prepare_prefix())
    _complete_gap_run()
    with database.connection() as connection:
        enqueue_task_in_transaction(connection, "repertoire_opportunity", "italian", {"repertoire_id": "italian", "phase": "nodes", "cursor": ""})
    task = claim_task("repertoire_opportunity")
    original_calculate = opportunities._calculate_node_opportunities
    def mutate_after_compute(inputs):
        decisions = original_calculate(inputs)
        assert any(decision["active"] for decision in decisions)
        add_line([*ITALIAN, "g8f6"], "concurrent-route")
        return decisions
    monkeypatch.setattr(opportunities, "_calculate_node_opportunities", mutate_after_compute)
    assert opportunities.execute_opportunity_slice(task)
    with database.read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM repertoire_opportunities").fetchone()[0] == 0
    monkeypatch.setattr(opportunities, "_calculate_node_opportunities", original_calculate)
    apply_preview(prepare_prefix())
    _complete_gap_run()
    next_task = claim_task("repertoire_opportunity")
    assert next_task is not None
    for _ in range(20):
        if not opportunities.execute_opportunity_slice(next_task):
            break
        with database.read_connection() as connection:
            if opportunities.list_opportunities(connection, "italian"):
                break
        next_task = claim_task("repertoire_opportunity")
        assert next_task is not None
    with database.read_connection() as connection:
        assert opportunities.list_opportunities(connection, "italian")


def test_canonical_explorer_source_race_cannot_publish_and_priority_ignores_old_run(prefix_database, monkeypatch):
    from app.services import repertoire_coverage as coverage
    from app.services.introduction_priorities import _coverage_evidence
    add_line([*ITALIAN, "f8c5", "c2c3"])
    apply_preview(prepare_prefix())
    run_id = coverage.enqueue_coverage_refresh("italian")
    monkeypatch.setattr(coverage, "get_explorer_session_token", lambda: "fixture-token")
    node = coverage.claim_coverage_node()
    assert node is not None
    monkeypatch.setattr(coverage, "_cached_explorer_payload", lambda *_args: (None, "fixture-cache"))
    def fetch_then_change_source(*_args):
        add_line([*ITALIAN, "g8f6"], "new-route")
        return {"moves": [{"uci": "a7a6", "white": 1000}]}
    monkeypatch.setattr(coverage, "_fetch_explorer", fetch_then_change_source)
    coverage.execute_coverage_node(node)
    with database.read_connection() as connection:
        assert not connection.execute("SELECT 1 FROM repertoire_coverage_candidates WHERE node_id=?", (node["id"],)).fetchone()
        assert _coverage_evidence(connection, "italian") == {}
    assert coverage.coverage_summary("italian")["run_id"] is None
    assert coverage.enqueue_coverage_refresh("italian") != run_id


def test_canonical_coverage_scope_predicate_survives_postgres_compatibility_translation():
    from types import SimpleNamespace
    from app.postgres_store import postgres_sql
    from app.services.canonical_scope_freshness import coverage_scope_predicate
    postgres_database = SimpleNamespace(execute_native=lambda: None)
    compatibility_sql = postgres_sql("SELECT n.id FROM repertoire_coverage_nodes n JOIN repertoire_coverage_runs r ON r.id=n.run_id WHERE " + coverage_scope_predicate(postgres_database))
    assert "JSON_EXTRACT_PATH_TEXT(r.settings_json," in compatibility_sql
    assert "JSONB" not in compatibility_sql
    assert "canonical_scope_source_revision" in compatibility_sql
    native_sql = coverage_scope_predicate(postgres_database, native=True)
    assert "settings_json::jsonb->>'canonical_scope_source_revision'" in native_sql


def test_canonical_explicit_generated_card_edit_promotes_source_and_clearing_revokes_route(prefix_database):
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    from app.services.canonical_prefix import read_prefix
    add_line([*ITALIAN, "f8c5", "c2c3"])
    apply_preview(prepare_prefix())
    enqueue_opening_graph_rebuild("italian")
    execute_opening_graph_rebuild(claim_task("opening_graph_rebuild"))
    with database.connection() as connection:
        generated_card = connection.execute("SELECT * FROM cards WHERE canonical_route_source=0 LIMIT 1").fetchone()
        before_edit = read_prefix(connection, "italian")["source_revision"]
        connection.execute("UPDATE cards SET moves_json='[]' WHERE id=?", (generated_card["id"],))
        assert connection.execute("SELECT canonical_route_source FROM cards WHERE id=?", (generated_card["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT canonical_route_source FROM repertoire_cards WHERE card_id=?", (generated_card["id"],)).fetchone()[0] == 1
        assert read_prefix(connection, "italian")["source_revision"] > before_edit
        before_unlink = read_prefix(connection, "italian")["source_revision"]
        connection.execute("DELETE FROM repertoire_cards WHERE card_id=?", (generated_card["id"],))
        assert read_prefix(connection, "italian")["source_revision"] > before_unlink


def test_canonical_graph_cleanup_preserves_independently_authored_cards(prefix_database):
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    from app.services.canonical_prefix import read_prefix
    add_line([*ITALIAN, "f8c5"])
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('authored','italian','response',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(ITALIAN[:3])))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('italian','authored')")
    apply_preview(prepare_prefix())
    enqueue_opening_graph_rebuild("italian")
    with database.read_connection() as connection:
        before_graph = read_prefix(connection, "italian")["source_revision"]
    execute_opening_graph_rebuild(claim_task("opening_graph_rebuild"))
    with database.read_connection() as connection:
        assert read_prefix(connection, "italian")["source_revision"] == before_graph
        assert connection.execute("SELECT archived FROM cards WHERE id='authored'").fetchone()[0] == 0
        assert connection.execute("SELECT 1 FROM repertoire_cards WHERE card_id='authored'").fetchone()


def test_canonical_published_introduction_priorities_hide_after_source_edit(prefix_database):
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    from app.services.introduction_priorities import rebuild_introduction_priorities, priority_status
    add_line([*ITALIAN, "f8c5", "c2c3"])
    apply_preview(prepare_prefix())
    enqueue_opening_graph_rebuild("italian")
    execute_opening_graph_rebuild(claim_task("opening_graph_rebuild"))
    with database.connection() as connection:
        rebuild_introduction_priorities(connection, "italian")
        assert connection.execute("SELECT 1 FROM current_repertoire_card_introduction_priorities").fetchone()
        assert priority_status(connection, "italian")["updated_at"] is not None
    add_line([*ITALIAN, "g8f6"], "new-route")
    with database.read_connection() as connection:
        assert connection.execute("SELECT 1 FROM repertoire_card_introduction_priorities").fetchone()
        assert not connection.execute("SELECT 1 FROM current_repertoire_card_introduction_priorities").fetchone()
        assert priority_status(connection, "italian")["updated_at"] is None


def test_canonical_discovery_feed_counts_and_foreground_admission_hide_stale_source(prefix_database):
    from fastapi import HTTPException
    from app import discovery_commands, main
    from app.services.repertoire_opportunities import _publish
    add_line([*ITALIAN, "f8c5"])
    apply_preview(prepare_prefix())
    with database.connection() as connection:
        _publish(connection, repertoire_id="italian", kind="missing_response",
                 fen_key=position_key_for_test(prefix_projection(ITALIAN)["ending_fen"]),
                 target="a7a6", card_id=None, opponent_move_uci="a7a6", score=1, evidence={})
        saved_opportunity = dict(connection.execute("SELECT * FROM repertoire_opportunities").fetchone())
    assert main.discoveries_feed()["total"] == 1
    add_line([*ITALIAN, "g8f6"], "new-route")
    assert main.discoveries_feed() == {"discoveries": [], "total": 0, "next_offset": None, "unread_count": 0}
    class PostgreSQLCommandDatabase:
        def __init__(self, connection):
            self.connection = connection
        def execute_native(self, statement, parameters=()):
            return self.connection.execute(statement.replace("%s", "?").replace(" FOR UPDATE", ""), parameters)
        def execute(self, statement, parameters=()):
            return self.execute_native(statement, parameters)
    with database.connection() as connection:
        with pytest.raises(HTTPException) as rejection:
            discovery_commands.accept_discovery(PostgreSQLCommandDatabase(connection), {
                "opportunity_id": saved_opportunity["id"], "selected_move_uci": "a7a6",
                "evidence_fingerprint": saved_opportunity["evidence_fingerprint"], "repertoire_id": "italian"})
        assert rejection.value.status_code == 404
        assert not connection.execute("SELECT 1 FROM discovery_admission_intents").fetchone()


def test_canonical_preview_checks_authored_membership_of_a_generated_shared_card(prefix_database):
    add_line(ITALIAN)
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES('generated-shared','other','response',?,?,'2026-10-02',0)",
                           (chess.STARTING_FEN, json.dumps(["e2e4", "e7e5", "g1f3", "d7d6"])))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('italian','generated-shared',1)")
    preview = prepare_prefix()
    assert preview["state"] == "conflicts"
    assert any(conflict["item_id"] == "card:generated-shared" for conflict in preview["conflicts"])


def test_canonical_introduction_scores_hide_after_another_repertoire_scope_changes(prefix_database):
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    from app.services.introduction_priorities import rebuild_introduction_priorities
    from app.services.canonical_prefix import read_prefix
    add_line([*ITALIAN, "f8c5", "c2c3"])
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
    apply_preview(prepare_prefix())
    enqueue_opening_graph_rebuild("italian")
    execute_opening_graph_rebuild(claim_task("opening_graph_rebuild"))
    with database.connection() as connection:
        rebuild_introduction_priorities(connection, "italian")
        source_revision = read_prefix(connection, "italian")["source_revision"]
        assert connection.execute("SELECT 1 FROM current_repertoire_card_introduction_priorities").fetchone()
    _set_other_prefix("other", ITALIAN)
    with database.read_connection() as connection:
        assert read_prefix(connection, "italian")["source_revision"] == source_revision
        assert connection.execute("SELECT 1 FROM repertoire_card_introduction_priorities").fetchone()
        assert not connection.execute("SELECT 1 FROM current_repertoire_card_introduction_priorities").fetchone()


def test_canonical_global_scope_ignores_internal_tactics_and_study_schedule_changes(prefix_database):
    from app.services.canonical_scope_freshness import game_scope_generation
    add_line(ITALIAN)
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('scheduled','italian','prefix',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(ITALIAN)))
        before_study = game_scope_generation(connection)
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__captured_tactics__','Captured tactics','internal','2026-10-02')")
        connection.execute("UPDATE cards SET state='learning',due_date='2026-10-03',stability=5 WHERE id='scheduled'")
        connection.execute("DELETE FROM repertoires WHERE id='__captured_tactics__'")
        assert game_scope_generation(connection) == before_study


@pytest.mark.parametrize('admission', ['branch', 'pgn', 'paste', 'repair'])
def test_canonical_downstream_admission_certifies_final_source_without_renewing_unrelated_routes(prefix_database, quiet_prefix_writes, admission):
    from app.services.canonical_prefix import line_origin, read_prefix, scope_line
    route = [*ITALIAN, 'f8c5', 'c2c3']
    unrelated = [*ITALIAN, 'g8f6', 'd2d3']
    add_line(route)
    add_line(unrelated, 'unrelated')
    downstream = prefix_projection(route)['ending_fen']
    if admission == 'repair':
        add_line(['d7d6', 'd2d4'], 'repair-source', downstream)
    apply_preview(prepare_prefix())
    with database.read_connection() as connection:
        before = read_prefix(connection, 'italian')['source_revision']
    if admission == 'branch':
        from app.main import branch
        from app.models import BranchRequest
        branch(BranchRequest(repertoire_id='italian', name='New downstream', trained_color='white', starting_fen=downstream, moves=['g8f6', 'd2d4']))
    elif admission == 'pgn':
        import asyncio
        from io import BytesIO
        from fastapi import UploadFile
        from app.main import import_pgn
        text = f'[Event "Downstream"]\n[SetUp "1"]\n[FEN "{downstream}"]\n\n4... Nf6 5. d4 *'
        asyncio.run(import_pgn(UploadFile(filename='italian.pgn', file=BytesIO(text.encode())), 'white', 3, None))
    elif admission == 'paste':
        from app.services.analysis_paste import build_paste_preview, parse_pasted_lines, commit_pasted_lines
        with database.connection() as connection:
            text = 'Nf6 d4'
            preview = build_paste_preview(connection, text, downstream, None)
            commit_pasted_lines(connection, text, downstream, None, preview['preview_token'], [{'index': 0, 'repertoire_id': 'italian', 'acknowledge_conflict': True}], preview, parse_pasted_lines(text, downstream))
    else:
        from app.services.repertoire_integrity import _rewrite_line
        with database.connection() as connection:
            source = connection.execute("SELECT * FROM repertoire_lines WHERE id='repair-source'").fetchone()
            assert _rewrite_line(connection, source, ['g8f6', 'd2d4'])
    with database.read_connection() as connection:
        current = read_prefix(connection, 'italian')
        assert current['source_revision'] > before
        assert line_origin(connection, current['preview_id'], downstream) == route
        line = dict(connection.execute('SELECT * FROM repertoire_lines WHERE start_fen=? AND moves_json=?', (downstream, json.dumps(['g8f6', 'd2d4']))).fetchone())
        assert not scope_line(connection, 'italian', line).get('scope_pending')
        assert line_origin(connection, current['preview_id'], prefix_projection(unrelated)['ending_fen']) is None


@pytest.mark.parametrize('mutation', ['revise', 'archive'])
def test_canonical_sqlite_card_scope_mutation_admits_durable_game_refresh(prefix_database, quiet_prefix_writes, mutation):
    from app import main
    from app.models import CardRevisionRequest
    from app.services.canonical_scope_freshness import game_scope_generation
    from app.services.cards import card_id
    identifier = card_id(chess.STARTING_FEN, ITALIAN)
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,'italian','response',?,?,'2026-10-02')", (identifier, chess.STARTING_FEN, json.dumps(ITALIAN)))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('italian',?)", (identifier,))
        before = game_scope_generation(connection)
    if mutation == 'revise':
        main.revise_card(identifier, CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=[*ITALIAN, 'f8c5'], history_mode='preserve', expected_revision=1))
    else:
        main.archive_card(identifier)
    with database.read_connection() as connection:
        assert game_scope_generation(connection) > before
        refresh = connection.execute("SELECT * FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'").fetchone()
        assert refresh is not None and refresh['state'] == 'queued'
        assert json.loads(refresh['payload_json']) == {'after_game_id': ''}


def test_canonical_sqlite_existing_generated_replacement_promotes_only_edited_membership(prefix_database, quiet_prefix_writes):
    from app.main import revise_card
    from app.models import CardRevisionRequest
    from app.services.cards import card_id
    from app.services.canonical_prefix import read_prefix
    from app.services.repertoire_integrity import _reconcile_derived_cards
    first = card_id(chess.STARTING_FEN, ITALIAN)
    replacement = card_id(chess.STARTING_FEN, [*ITALIAN, 'f8c5'])
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        for identifier, moves in [(first, ITALIAN), (replacement, [*ITALIAN, 'f8c5'])]:
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES(?,'italian','response',?,?,'2026-10-02',0)", (identifier, chess.STARTING_FEN, json.dumps(moves)))
            connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('italian',?,0)", (identifier,))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('other',?,0)", (replacement,))
        before = read_prefix(connection, 'italian')['source_revision']
    assert revise_card(first, CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=[*ITALIAN, 'f8c5'], history_mode='preserve', expected_revision=1))['card_id'] == replacement
    with database.connection() as connection:
        assert connection.execute('SELECT canonical_route_source FROM cards WHERE id=?', (replacement,)).fetchone()[0] == 1
        assert connection.execute("SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id='italian' AND card_id=?", (replacement,)).fetchone()[0] == 1
        assert connection.execute("SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id='other' AND card_id=?", (replacement,)).fetchone()[0] == 0
        assert read_prefix(connection, 'italian')['source_revision'] > before
        _reconcile_derived_cards(connection, 'other')
        _reconcile_derived_cards(connection, 'italian')
        assert connection.execute('SELECT archived FROM cards WHERE id=?', (replacement,)).fetchone()[0] == 0
        assert connection.execute("SELECT 1 FROM repertoire_cards WHERE repertoire_id='italian' AND card_id=?", (replacement,)).fetchone()


@pytest.mark.parametrize('card_source,link_source', [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_canonical_integrity_reconciliation_respects_specific_membership_provenance(prefix_database, card_source, link_source):
    from app.services.repertoire_integrity import _reconcile_derived_cards
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES('standalone','italian','response',?,?,'2026-10-02',?)", (chess.STARTING_FEN, json.dumps(ITALIAN), card_source))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('italian','standalone',?)", (link_source,))
        _reconcile_derived_cards(connection, 'italian')
        link = connection.execute("SELECT 1 FROM repertoire_cards WHERE repertoire_id='italian' AND card_id='standalone'").fetchone()
        assert bool(link) == bool(link_source)
        # An explicit generated owner membership must not become an authored fallback.
        assert connection.execute("SELECT archived FROM cards WHERE id='standalone'").fetchone()[0] == int(not link_source)


def test_canonical_generated_graph_materialization_does_not_admit_global_game_refresh(prefix_database):
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    from app.services.canonical_scope_freshness import game_scope_generation
    from app.services.canonical_prefix import read_prefix
    add_line([*ITALIAN, 'f8c5', 'c2c3'])
    apply_preview(prepare_prefix())
    with database.connection() as connection:
        connection.execute("DELETE FROM background_tasks WHERE kind='repertoire_game_refresh'")
        before = (read_prefix(connection, 'italian')['source_revision'], game_scope_generation(connection))
    enqueue_opening_graph_rebuild('italian')
    execute_opening_graph_rebuild(claim_task('opening_graph_rebuild'))
    with database.read_connection() as connection:
        assert connection.execute('SELECT 1 FROM cards WHERE canonical_route_source=0').fetchone()
        assert (read_prefix(connection, 'italian')['source_revision'], game_scope_generation(connection)) == before
        assert not connection.execute("SELECT 1 FROM background_tasks WHERE kind='repertoire_game_refresh'").fetchone()


def test_canonical_game_refresh_admission_rolls_back_with_mutation_and_fences_prior_sweep(prefix_database):
    from app.services.repertoire_game_refresh import refreshing_game_scope, execute_repertoire_game_refresh_slice
    from app.services.canonical_scope_freshness import game_scope_generation
    with database.read_connection() as connection:
        initial = game_scope_generation(connection)
    with pytest.raises(RuntimeError, match='Interrupted'):
        with database.connection() as connection, refreshing_game_scope(connection):
            connection.execute("UPDATE repertoires SET is_main=1 WHERE id='italian'")
            raise RuntimeError('Interrupted foreground write')
    with database.read_connection() as connection:
        assert game_scope_generation(connection) == initial
        assert not connection.execute("SELECT 1 FROM background_tasks WHERE kind='repertoire_game_refresh'").fetchone()
    with database.connection() as connection, refreshing_game_scope(connection):
        connection.execute("UPDATE repertoires SET is_main=1 WHERE id='italian'")
    old_sweep = claim_task('repertoire_game_refresh')
    with database.connection() as connection, refreshing_game_scope(connection):
        connection.execute("UPDATE repertoires SET is_main=0 WHERE id='italian'")
    assert execute_repertoire_game_refresh_slice(old_sweep) is False
    with database.read_connection() as connection:
        replacement = connection.execute("SELECT * FROM background_tasks WHERE kind='repertoire_game_refresh'").fetchone()
        assert replacement['generation'] > old_sweep['generation']
        assert replacement['state'] == 'queued'
        assert json.loads(replacement['payload_json']) == {'after_game_id': ''}


@pytest.mark.parametrize('admission', ['pgn', 'paste', 'repair'])
def test_canonical_batch_admissions_certify_every_verified_route_at_one_final_revision(prefix_database, quiet_prefix_writes, admission):
    from app.services.canonical_prefix import line_origin, read_prefix
    route = [*ITALIAN, 'f8c5', 'c2c3']
    downstream = prefix_projection(route)['ending_fen']
    continuations = [['g8f6', 'd2d4'], ['a7a6', 'd2d3']]
    add_line(route)
    if admission == 'repair':
        add_line(['d7d6', 'd2d4'], 'first-repair', downstream)
        add_line(['d7d6', 'd2d3'], 'second-repair', downstream)
    apply_preview(prepare_prefix())
    if admission == 'pgn':
        import asyncio
        from io import BytesIO
        from fastapi import UploadFile
        from app.main import import_pgn
        text = f'[Event "First"]\n[SetUp "1"]\n[FEN "{downstream}"]\n\n4... Nf6 5. d4 *\n\n[Event "Second"]\n[SetUp "1"]\n[FEN "{downstream}"]\n\n4... a6 5. d3 *'
        asyncio.run(import_pgn(UploadFile(filename='italian.pgn', file=BytesIO(text.encode())), 'white', 3, None))
    elif admission == 'paste':
        from app.services.analysis_paste import build_paste_preview, parse_pasted_lines, commit_pasted_lines
        with database.connection() as connection:
            text = 'Nf6 (a6 d3) d4'
            preview = build_paste_preview(connection, text, downstream, None)
            commit_pasted_lines(connection, text, downstream, None, preview['preview_token'], [{'index': index, 'repertoire_id': 'italian', 'acknowledge_conflict': True} for index in range(2)], preview, parse_pasted_lines(text, downstream))
    else:
        from app.services.repertoire_integrity import resolve_issue
        # Both conflicting lines share the trained decision after ...d6. One
        # guided repair replaces both in a single foreground transaction.
        continuations = [['d7d6', 'e1g1']]
        decision_fen = prefix_projection([*route, 'd7d6'])['ending_fen']
        with database.connection() as connection:
            from app.services.repertoire_integrity import sweep_repertoire
            sweep_repertoire(connection, 'italian')
            issue = connection.execute('SELECT * FROM repertoire_integrity_issues WHERE fen_key=?', (' '.join(decision_fen.split()[:4]),)).fetchone()
            assert issue is not None
            result = resolve_issue(connection, 'italian', issue['id'], issue['signature'], 'e1g1')
            assert result['changed_line_count'] == 2
    with database.read_connection() as connection:
        current = read_prefix(connection, 'italian')
        for moves in continuations:
            ending_fen = prefix_projection([*route, *moves])['ending_fen']
            assert line_origin(connection, current['preview_id'], ending_fen) == [*route, *moves]
            row = connection.execute('SELECT source_revision FROM canonical_prefix_positions WHERE preview_id=? AND fen_key=? AND in_scope=1', (current['preview_id'], ' '.join(ending_fen.split()[:4]))).fetchone()
            assert row[0] == current['source_revision']


def test_canonical_unrelated_integrity_repair_preserves_authored_standalone_source(prefix_database):
    from app.services.repertoire_integrity import resolve_issue, sweep_repertoire
    add_line(['e2e4', 'e7e5', 'g1f3'], 'first')
    add_line(['e2e4', 'e7e5', 'f1c4'], 'second')
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('standalone','italian','response',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(['d2d4'])))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('italian','standalone')")
        sweep_repertoire(connection, 'italian')
        decision_key = ' '.join(prefix_projection(['e2e4', 'e7e5'])['ending_fen'].split()[:4])
        issue = connection.execute('SELECT * FROM repertoire_integrity_issues WHERE fen_key=?', (decision_key,)).fetchone()
        assert issue is not None
        result = resolve_issue(connection, 'italian', issue['id'], issue['signature'], 'g1f3')
        assert result['changed_line_count'] > 0
        assert connection.execute("SELECT archived FROM cards WHERE id='standalone'").fetchone()[0] == 0
        assert connection.execute("SELECT 1 FROM repertoire_cards WHERE repertoire_id='italian' AND card_id='standalone'").fetchone()


def test_canonical_card_promotion_does_not_invalidate_generated_shared_membership(prefix_database, quiet_prefix_writes):
    from app.main import revise_card
    from app.models import CardRevisionRequest
    from app.services.cards import card_id
    from app.services.canonical_prefix import read_prefix, line_origin
    from app.services.canonical_scope_freshness import coverage_run_is_current
    from app.services import repertoire_coverage as coverage
    first = card_id(chess.STARTING_FEN, ITALIAN)
    replacement = card_id(chess.STARTING_FEN, [*ITALIAN, 'f8c5'])
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        for identifier, moves in [(first, ITALIAN), (replacement, [*ITALIAN, 'f8c5'])]:
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES(?,'italian','response',?,?,'2026-10-02',0)", (identifier, chess.STARTING_FEN, json.dumps(moves)))
            connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('italian',?,0)", (identifier,))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('other',?,0)", (replacement,))
        connection.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('other-route','other','Other route','white',?,?,'2026-10-03')", (chess.STARTING_FEN, json.dumps([*ITALIAN, 'f8c5'])))
    _set_other_prefix('other', ITALIAN)
    run_id = coverage.enqueue_coverage_refresh('other')
    with database.connection() as connection:
        connection.execute("UPDATE repertoire_coverage_runs SET status='complete' WHERE id=?", (run_id,))
        before = read_prefix(connection, 'other')['source_revision']
        prefix = read_prefix(connection, 'other')
        ending_fen = prefix_projection([*ITALIAN, 'f8c5'])['ending_fen']
        assert line_origin(connection, prefix['preview_id'], ending_fen) == [*ITALIAN, 'f8c5']
    revise_card(first, CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=[*ITALIAN, 'f8c5'], history_mode='preserve', expected_revision=1))
    with database.read_connection() as connection:
        assert read_prefix(connection, 'other')['source_revision'] == before
        assert line_origin(connection, prefix['preview_id'], ending_fen) == [*ITALIAN, 'f8c5']
        run = connection.execute("SELECT * FROM repertoire_coverage_runs WHERE id=?", (run_id,)).fetchone()
        assert coverage_run_is_current(connection, run, 'other')


def test_canonical_graph_cleanup_removes_generated_link_from_authored_shared_card(prefix_database):
    from app.services.canonical_prefix import read_prefix
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('shared-authored','italian','response',?,?,'2026-10-02')", (chess.STARTING_FEN, json.dumps(ITALIAN)))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('italian','shared-authored',1),('other','shared-authored',0)")
        connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-10-03','shared-authored',0)")
        before = read_prefix(connection, 'italian')['source_revision']
    enqueue_opening_graph_rebuild('other')
    execute_opening_graph_rebuild(claim_task('opening_graph_rebuild'))
    with database.read_connection() as connection:
        assert not connection.execute("SELECT 1 FROM repertoire_cards WHERE repertoire_id='other' AND card_id='shared-authored'").fetchone()
        assert connection.execute("SELECT 1 FROM repertoire_cards WHERE repertoire_id='italian' AND card_id='shared-authored'").fetchone()
        assert connection.execute("SELECT archived FROM cards WHERE id='shared-authored'").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM daily_queue WHERE card_id='shared-authored'").fetchone()[0] == 'queued'
        assert read_prefix(connection, 'italian')['source_revision'] == before


def test_canonical_generated_prefix_split_preserves_membership_and_scope(prefix_database):
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    from app.services.prefix_split import apply_prefix_split
    from app.services.canonical_prefix import read_prefix, line_origin
    from app.services.canonical_scope_freshness import game_scope_generation, coverage_run_is_current
    from app.services import repertoire_coverage as coverage
    add_line([*ITALIAN, 'f8c5', 'c2c3'])
    apply_preview(prepare_prefix())
    enqueue_opening_graph_rebuild('italian')
    execute_opening_graph_rebuild(claim_task('opening_graph_rebuild'))
    run_id = coverage.enqueue_coverage_refresh('italian')
    with database.connection() as connection:
        source = connection.execute("SELECT * FROM cards WHERE kind='prefix' AND canonical_route_source=0 AND json_array_length(moves_json)>=5 LIMIT 1").fetchone()
        assert source is not None
        prefix = read_prefix(connection, 'italian')
        ending_fen = prefix_projection([*ITALIAN, 'f8c5', 'c2c3'])['ending_fen']
        assert line_origin(connection, prefix['preview_id'], ending_fen) == [*ITALIAN, 'f8c5', 'c2c3']
        before = prefix['source_revision']
        generation = game_scope_generation(connection)
        refresh = connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='repertoire_game_refresh'").fetchone()[0]
        result = apply_prefix_split(connection, source['id'], source['revision'])
        for child in [result['parent']['card_id'], result['continuation']['card_id']]:
            assert connection.execute("SELECT canonical_route_source FROM cards WHERE id=?", (child,)).fetchone()[0] == 0
            assert connection.execute("SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id='italian' AND card_id=?", (child,)).fetchone()[0] == 0
        assert read_prefix(connection, 'italian')['source_revision'] == before
        assert game_scope_generation(connection) == generation
        assert line_origin(connection, prefix['preview_id'], ending_fen) == [*ITALIAN, 'f8c5', 'c2c3']
        assert coverage_run_is_current(connection, connection.execute('SELECT * FROM repertoire_coverage_runs WHERE id=?', (run_id,)).fetchone(), 'italian')
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='repertoire_game_refresh'").fetchone()[0] == refresh


@pytest.mark.parametrize('owner_link_source', [None, 0, 1])
def test_canonical_card_owner_fallback_respects_explicit_membership(prefix_database, owner_link_source):
    from app.services.canonical_prefix import read_prefix
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES('owner-source','italian','response',?,?,'2026-10-03',0)", (chess.STARTING_FEN, json.dumps(ITALIAN)))
        if owner_link_source is not None:
            connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('italian','owner-source',?)", (owner_link_source,))
        before = read_prefix(connection, 'italian')['source_revision']
        connection.execute("UPDATE cards SET canonical_route_source=1 WHERE id='owner-source'")
        after = read_prefix(connection, 'italian')['source_revision']
        assert (after > before) == (owner_link_source != 0)


def test_canonical_structural_edit_invalidates_both_authored_shared_memberships(prefix_database):
    from app.services.canonical_prefix import read_prefix
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-03')")
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('shared-source','italian','response',?,?,'2026-10-03')", (chess.STARTING_FEN, json.dumps(ITALIAN)))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('italian','shared-source',1),('other','shared-source',1)")
        before = {identifier: read_prefix(connection, identifier)['source_revision'] for identifier in ['italian', 'other']}
        connection.execute("UPDATE cards SET moves_json=? WHERE id='shared-source'", (json.dumps([*ITALIAN, 'f8c5']),))
        for identifier in before:
            assert read_prefix(connection, identifier)['source_revision'] > before[identifier]


@pytest.mark.parametrize("mutation", ["revise", "archive"])
def test_canonical_card_mutation_without_coverage_work_reports_actionable_recheck(prefix_database, quiet_prefix_writes, mutation):
    from app import main
    from app.models import CardRevisionRequest
    from app.services import repertoire_coverage as coverage
    from app.services.cards import card_id

    add_line([*ITALIAN, "f8c5", "c2c3"])
    identifier = card_id(chess.STARTING_FEN, ITALIAN)
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,'italian','response',?,?,'2026-10-02')", (identifier, chess.STARTING_FEN, json.dumps(ITALIAN)))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('italian',?)", (identifier,))
    apply_preview(prepare_prefix())
    old_run = coverage.enqueue_coverage_refresh("italian")
    with database.connection() as connection:
        connection.execute("UPDATE repertoire_coverage_runs SET status='complete' WHERE id=?", (old_run,))
        connection.execute("UPDATE repertoire_coverage_nodes SET explorer_status='complete',explorer_games=1000 WHERE run_id=?", (old_run,))
        connection.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,required,covered,blended_probability,source_state) SELECT id,'f8c5',1,1,1,'explorer-only' FROM repertoire_coverage_nodes WHERE run_id=?", (old_run,))
    assert coverage.coverage_summary("italian")["is_complete"]
    if mutation == "revise":
        main.revise_card(identifier, CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=[*ITALIAN, "f8c5"], history_mode="preserve", expected_revision=1))
    else:
        main.archive_card(identifier)
    with database.read_connection() as connection:
        assert connection.execute("SELECT id FROM repertoire_coverage_runs WHERE repertoire_id='italian' ORDER BY created_at DESC LIMIT 1").fetchone()[0] == old_run
    summary = coverage.coverage_summary("italian")
    assert summary["status"] == "failed"
    assert summary["run_id"] is None and not summary["is_complete"]
    assert summary["required_branches"] == summary["covered_branches"] == 0
    assert summary["probability_coverage"] is None
    assert "Canonical prefix" in summary["last_error"] and "refresh coverage" in summary["last_error"]
    assert coverage.coverage_gaps("italian") == []
    apply_preview(prepare_prefix())
    replacement_run = coverage.enqueue_coverage_refresh("italian")
    recovered = coverage.coverage_summary("italian")
    assert replacement_run != old_run and recovered["run_id"] == replacement_run
    assert recovered["status"] == "queued" and not recovered["last_error"]


def test_canonical_prefix_without_coverage_work_is_not_started(prefix_database):
    with database.connection() as connection:
        connection.execute("UPDATE repertoires SET canonical_prefix_moves_json=?,canonical_prefix_revision=1 WHERE id='italian'", (json.dumps(ITALIAN),))
    from app.services.repertoire_coverage import coverage_summary
    summary = coverage_summary("italian")
    assert summary["run_id"] is None and summary["status"] == "not-started"


@pytest.mark.parametrize("observation", ["adoption", "cleanup"])
@pytest.mark.parametrize("mutation", ["revise", "archive"])
def test_canonical_shared_card_owner_cleanup_never_resurrects_generated_source(prefix_database, quiet_prefix_writes, observation, mutation):
    from app import main
    from app.models import CardRevisionRequest
    from app.services.cards import card_id
    from app.services.canonical_prefix import read_prefix
    from app.services.canonical_prefix_preview import _next_item
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild

    first = card_id(chess.STARTING_FEN, ITALIAN)
    replacement = card_id(chess.STARTING_FEN, [*ITALIAN, "f8c5"])
    def source_item(connection):
        return _next_item(connection, {"id": "source-inspection", "repertoire_id": "other"}, {"phase": "cards", "cursor": ""})
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        for identifier, owner, moves in [(first, 'italian', ITALIAN), (replacement, 'other', [*ITALIAN, 'f8c5'])]:
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES(?,?,'response',?,?,'2026-10-02',0)", (identifier, owner, chess.STARTING_FEN, json.dumps(moves)))
            connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0)", (owner, identifier))
        assert source_item(connection) is None
        source_before = read_prefix(connection, 'other')['source_revision']
    main.revise_card(first, CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=[*ITALIAN, 'f8c5'], history_mode='preserve', expected_revision=1))
    with database.read_connection() as connection:
        assert read_prefix(connection, 'other')['source_revision'] == source_before
        if observation == 'adoption':
            assert source_item(connection) is None
        authored_before = read_prefix(connection, 'italian')['source_revision']
    enqueue_opening_graph_rebuild('other')
    execute_opening_graph_rebuild(claim_task('opening_graph_rebuild'))
    with database.read_connection() as connection:
        assert not connection.execute("SELECT 1 FROM repertoire_cards WHERE repertoire_id='other' AND card_id=?", (replacement,)).fetchone()
        assert connection.execute("SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id='italian' AND card_id=?", (replacement,)).fetchone()[0] == 1
        preserved = connection.execute("SELECT repertoire_id,archived,revision FROM cards WHERE id=?", (replacement,)).fetchone()
        assert preserved['repertoire_id'] == 'italian' and preserved['archived'] == 0
        assert source_item(connection) is None
        assert read_prefix(connection, 'other')['source_revision'] == source_before
        assert read_prefix(connection, 'italian')['source_revision'] == authored_before
    if mutation == 'revise':
        main.revise_card(replacement, CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=[*ITALIAN, 'f8c5', 'c2c3'], history_mode='preserve', expected_revision=preserved['revision']))
    else:
        main.archive_card(replacement)
    with database.read_connection() as connection:
        assert source_item(connection) is None
        assert read_prefix(connection, 'other')['source_revision'] == source_before
        assert read_prefix(connection, 'italian')['source_revision'] > authored_before


@pytest.mark.parametrize("owner_scope", ["generated-owner", "unlinked-authored-owner"])
@pytest.mark.parametrize("cleanup_path", ["graph", "integrity"])
def test_canonical_last_generated_membership_cleanup_preserves_history_without_owner_resurrection(prefix_database, owner_scope, cleanup_path):
    from app.services.opening_graph import enqueue_opening_graph_rebuild, execute_opening_graph_rebuild
    from app.services.canonical_prefix_preview import _next_item
    from app.services.canonical_prefix import read_prefix
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-02')")
        owner = 'other' if owner_scope == 'generated-owner' else 'italian'
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES('orphan-source',?,'response',?,?,'2026-10-02',1)", (owner, chess.STARTING_FEN, json.dumps(ITALIAN)))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('other','orphan-source',0)")
        connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('orphan-source','correct','2026-10-02',1,2)")
        connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-10-03','orphan-source',0)")
        revisions = {scope: read_prefix(connection, scope)['source_revision'] for scope in ('italian','other')}
    if cleanup_path == 'graph':
        enqueue_opening_graph_rebuild('other')
        execute_opening_graph_rebuild(claim_task('opening_graph_rebuild'))
    else:
        from app.services.repertoire_integrity import _archive_unsupported_card
        with database.connection() as connection:
            assert _archive_unsupported_card(connection, 'other', 'orphan-source')
    with database.read_connection() as connection:
        assert not connection.execute("SELECT 1 FROM repertoire_cards WHERE card_id='orphan-source'").fetchone()
        preserved = connection.execute("SELECT archived,canonical_route_source FROM cards WHERE id='orphan-source'").fetchone()
        assert preserved['canonical_route_source'] == 1
        assert preserved['archived'] == (owner_scope == 'generated-owner')
        assert connection.execute("SELECT COUNT(*) FROM reviews WHERE card_id='orphan-source'").fetchone()[0] == 1
        if cleanup_path == 'graph':
            assert connection.execute("SELECT status FROM daily_queue WHERE card_id='orphan-source'").fetchone()[0] == ('superseded' if owner_scope == 'generated-owner' else 'queued')
        assert _next_item(connection, {'id': 'inspection', 'repertoire_id': 'other'}, {'phase': 'cards', 'cursor': ''}) is None
        if owner_scope == 'unlinked-authored-owner':
            assert _next_item(connection, {'id': 'inspection', 'repertoire_id': 'italian'}, {'phase': 'cards', 'cursor': ''})['id'] == 'orphan-source'
        assert {scope: read_prefix(connection, scope)['source_revision'] for scope in revisions} == revisions


def publish_state_action_fixture():
    from app.services.repertoire_opportunities import _publish
    add_line([*ITALIAN, 'f8c5', 'c2c3'])
    apply_preview(prepare_prefix())
    with database.connection() as connection:
        _publish(connection, repertoire_id='italian', kind='missing_response',
                 fen_key=position_key_for_test(prefix_projection(ITALIAN)['ending_fen']),
                 target='a7a6', card_id=None, opponent_move_uci='a7a6', score=1,
                 evidence={'supporting_games': 5})
        return connection.execute('SELECT id FROM repertoire_opportunities').fetchone()[0]


def invalidate_state_action_scope(invalidation):
    with database.connection() as connection:
        if invalidation == 'prefix':
            connection.execute("UPDATE repertoires SET canonical_prefix_revision=canonical_prefix_revision+1 WHERE id='italian'")
        elif invalidation == 'source':
            connection.execute("UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id='italian'")
        else:
            connection.execute('UPDATE repertoire_game_scope SET generation=generation+1 WHERE id=1')


@pytest.mark.parametrize('action', ['dismiss', 'acknowledge', 'snooze'])
@pytest.mark.parametrize('invalidation', ['prefix', 'source', 'global'])
def test_canonical_discovery_state_actions_reject_stale_scope_without_mutation(prefix_database, action, invalidation):
    from fastapi import HTTPException
    from app import main
    from app.services.canonical_scope_freshness import opportunity_is_current, scope_identity, game_scope_generation
    opportunity_id = publish_state_action_fixture()
    with database.connection() as connection:
        connection.execute("UPDATE repertoire_opportunities SET seen_at='2026-10-01',snoozed_until='2026-10-08',dismissed_evidence_json=? WHERE id=?",
                           (json.dumps({'supporting_games': 2}), opportunity_id))
    invalidate_state_action_scope(invalidation)
    if invalidation == 'source':
        # Source writes also advance the global epoch. Keep that independent
        # identity current so this case can only be rejected by the source fence.
        with database.connection() as connection:
            connection.execute('UPDATE repertoire_opportunities SET game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1) WHERE id=?', (opportunity_id,))
    with database.read_connection() as connection:
        before = dict(connection.execute('SELECT * FROM repertoire_opportunities WHERE id=?', (opportunity_id,)).fetchone())
        current_identity = {**scope_identity(connection, 'italian'), 'game_scope_generation': game_scope_generation(connection)}
        expected_stale_field = {'prefix': 'canonical_prefix_revision', 'source': 'canonical_scope_source_revision', 'global': 'game_scope_generation'}[invalidation]
        assert {field for field, value in current_identity.items() if before[field] != value} == {expected_stale_field}
        assert not opportunity_is_current(connection, before)
    with pytest.raises(HTTPException) as rejection:
        getattr(main, action + '_repertoire_opportunity')('italian', opportunity_id, None)
    assert rejection.value.status_code == 409
    assert 'refresh' in rejection.value.detail.lower()
    with database.read_connection() as connection:
        assert dict(connection.execute('SELECT * FROM repertoire_opportunities WHERE id=?', (opportunity_id,)).fetchone()) == before


@pytest.mark.parametrize('action', ['dismiss', 'acknowledge', 'snooze'])
@pytest.mark.parametrize('row_state', ['current', 'missing', 'inactive', 'wrong-repertoire'])
def test_canonical_discovery_state_actions_preserve_current_and_inactive_contracts(prefix_database, action, row_state):
    from fastapi import HTTPException
    from app import main
    opportunity_id = publish_state_action_fixture()
    with database.connection() as connection:
        connection.execute("UPDATE repertoire_opportunities SET snoozed_until='2026-10-08' WHERE id=?", (opportunity_id,))
        if row_state == 'inactive':
            connection.execute("UPDATE repertoire_opportunities SET status='resolved' WHERE id=?", (opportunity_id,))
    handler = getattr(main, action + '_repertoire_opportunity')
    if row_state != 'current':
        with pytest.raises(HTTPException) as rejection:
            handler('other' if row_state == 'wrong-repertoire' else 'italian',
                    'absent' if row_state == 'missing' else opportunity_id, None)
        assert rejection.value.status_code == 404
        assert rejection.value.detail == ('Active opportunity not found' if action == 'dismiss' else 'Active discovery not found')
        return
    assert handler('italian', opportunity_id, None) == { {'dismiss': 'dismissed', 'acknowledge': 'acknowledged', 'snooze': 'snoozed'}[action]: True }
    with database.read_connection() as connection:
        saved = connection.execute('SELECT * FROM repertoire_opportunities WHERE id=?', (opportunity_id,)).fetchone()
        if action == 'dismiss':
            assert saved['status'] == 'dismissed'
            assert saved['dismissed_evidence_json'] == saved['evidence_json']
        elif action == 'acknowledge':
            assert saved['seen_at'] is not None and saved['snoozed_until'] is None
        else:
            assert saved['seen_at'] is not None and saved['snoozed_until'] is not None


def test_canonical_rejected_stale_dismissal_cannot_suppress_current_republication(prefix_database):
    from fastapi import HTTPException
    from app import main
    from app.services.repertoire_opportunities import _publish
    opportunity_id = publish_state_action_fixture()
    invalidate_state_action_scope('source')
    with pytest.raises(HTTPException) as rejection:
        main.dismiss_repertoire_opportunity('italian', opportunity_id, None)
    assert rejection.value.status_code == 409
    with database.connection() as connection:
        _publish(connection, repertoire_id='italian', kind='missing_response',
                 fen_key=position_key_for_test(prefix_projection(ITALIAN)['ending_fen']),
                 target='a7a6', card_id=None, opponent_move_uci='a7a6', score=1,
                 evidence={'supporting_games': 5})
        republished = connection.execute('SELECT * FROM repertoire_opportunities WHERE id=?', (opportunity_id,)).fetchone()
        assert republished['status'] == 'active'
        assert republished['dismissed_evidence_json'] is None
    assert main.discoveries_feed()['total'] == 1


@pytest.mark.parametrize('action', ['dismiss', 'acknowledge', 'snooze'])
def test_postgres_discovery_state_actions_lock_scope_before_opportunity(prefix_database, action):
    from app import opportunity_commands
    opportunity_id = publish_state_action_fixture()
    class NativeStateActionDatabase:
        def __init__(self, connection):
            self.connection = connection
            self.statements = []
        def execute(self, statement, parameters=()):
            self.statements.append(statement)
            return self.connection.execute(statement.replace('%s', '?').replace(' FOR UPDATE', ''), parameters)
        execute_native = execute
    with database.connection() as connection:
        native = NativeStateActionDatabase(connection)
        getattr(opportunity_commands, action + '_opportunity')(native, {'repertoire_id': 'italian', 'opportunity_id': opportunity_id})
        metadata_lock = next(index for index, statement in enumerate(native.statements) if 'FROM repertoires' in statement and 'FOR UPDATE' in statement)
        global_lock = next(index for index, statement in enumerate(native.statements) if 'FROM repertoire_game_scope' in statement and 'FOR UPDATE' in statement)
        row_lock = next(index for index, statement in enumerate(native.statements) if 'FROM repertoire_opportunities' in statement and 'FOR UPDATE' in statement)
        assert metadata_lock < global_lock < row_lock


def selected_batch_pgn(lines):
    import chess.pgn
    rendered = []
    for line_index, (starting_fen, moves) in enumerate(lines):
        game = chess.pgn.Game()
        game.setup(chess.Board(starting_fen))
        game.headers['Event'] = 'Selected batch ' + str(line_index)
        node = game
        for move in moves:
            node = node.add_variation(chess.Move.from_uci(move))
        node.comment = 'Selected route annotation'
        rendered.append(game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=True)))
    return '\n\n'.join(rendered)


def admit_selected_batch(admission, lines, selections=None, before_commit=None):
    text = selected_batch_pgn(lines)
    if admission == 'pgn':
        import asyncio
        from io import BytesIO
        from fastapi import UploadFile
        from app import main
        from contextlib import contextmanager
        original_connection = main.connection
        @contextmanager
        def observed_connection(*args, **kwargs):
            with original_connection(*args, **kwargs) as connection:
                yield connection
                if before_commit:
                    before_commit(connection)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(main, 'connection', observed_connection)
            return asyncio.run(main.import_pgn(UploadFile(filename='italian.pgn', file=BytesIO(text.encode())), 'white', 3, None))
    from app.services.analysis_paste import build_paste_preview, parse_pasted_lines, commit_pasted_lines
    with database.connection() as connection:
        preview = build_paste_preview(connection, text, None, None)
        chosen = selections if selections is not None else [
            {'index': index, 'repertoire_id': 'italian', 'acknowledge_conflict': True}
            for index in range(len(lines))]
        result = commit_pasted_lines(connection, text, None, None, preview['preview_token'], chosen, preview, parse_pasted_lines(text, None))
        if before_commit:
            before_commit(connection)
        return result


def selected_batch_snapshot():
    tables = ['repertoires', 'repertoire_lines', 'repertoire_line_training_depths',
              'position_annotations', 'canonical_prefix_positions', 'repertoire_game_scope', 'background_tasks']
    with database.read_connection() as connection:
        return {table: sorted([dict(row) for row in connection.execute('SELECT * FROM ' + table)], key=repr)
                for table in tables}


def canonical_route_fixture():
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[2] / 'tests/fixtures/canonical-prefix-routes.json').read_text())


def selected_batch_fixture(*, repeated=False, current_longer_origin=False):
    add_line(ITALIAN)
    fixture = canonical_route_fixture()
    if current_longer_origin:
        add_line(fixture['current_longer_connector'], 'longer-current-origin')
    apply_preview(prepare_prefix())
    connector = fixture['repeated_connector'] if repeated else [*ITALIAN, 'f8c5', 'c2c3']
    continuation = fixture['continuation'] if repeated else ['g8f6', 'd2d4']
    return [(chess.STARTING_FEN, connector),
            (prefix_projection(connector)['ending_fen'], continuation)]


@pytest.mark.parametrize('admission', ['pgn', 'paste'])
@pytest.mark.parametrize('reverse_order', [False, True])
@pytest.mark.parametrize('current_longer_origin', [False, True])
def test_canonical_selected_batch_connector_admits_new_fen_continuation_in_either_order(prefix_database, quiet_prefix_writes, admission, reverse_order, current_longer_origin):
    from app.services.canonical_prefix import line_origin, read_prefix
    lines = selected_batch_fixture(repeated=True, current_longer_origin=current_longer_origin)
    expected_origin = lines[0][1][:7]
    expected_route = [*expected_origin, *lines[1][1]]
    selected = list(reversed(lines)) if reverse_order else lines
    before = selected_batch_snapshot()
    observed = []
    def observe_uncommitted(connection):
        assert selected_batch_snapshot() == before
        assert connection.execute("SELECT COUNT(*) FROM repertoire_lines WHERE repertoire_id='italian'").fetchone()[0] == 3 + int(current_longer_origin)
        observed.append(True)
    admit_selected_batch(admission, [*selected, selected[0]], before_commit=observe_uncommitted)
    assert observed == [True]
    with database.read_connection() as connection:
        current = read_prefix(connection, 'italian')
        assert connection.execute("SELECT COUNT(*) FROM repertoire_lines WHERE repertoire_id='italian'").fetchone()[0] == 3 + int(current_longer_origin)
        assert line_origin(connection, current['preview_id'], lines[1][0]) == expected_origin
        assert line_origin(connection, current['preview_id'], prefix_projection(expected_route)['ending_fen']) == expected_route
        for starting_fen, moves in lines:
            assert connection.execute('SELECT COUNT(*) FROM repertoire_lines WHERE repertoire_id=? AND start_fen=? AND moves_json=?',
                                      ('italian', starting_fen, json.dumps(moves))).fetchone()[0] == 1
        for route in (expected_origin, expected_route):
            stored = connection.execute('SELECT route_json,ply,in_scope,source_revision FROM canonical_prefix_positions WHERE preview_id=? AND fen_key=? AND in_scope=1',
                                        (current['preview_id'], position_key_for_test(prefix_projection(route)['ending_fen']))).fetchone()
            assert tuple(stored) == (json.dumps(route), len(route), 1, current['source_revision'])
        expected_positions = {}
        for validation in (validate_scoped_line(chess.STARTING_FEN, lines[0][1], ITALIAN, []),
                           validate_scoped_line(lines[1][0], lines[1][1], ITALIAN, expected_origin)):
            for position in validation['positions']:
                key = (position['fen_key'], position['in_scope'])
                value = (position['ply'], position['route_json'])
                expected_positions[key] = min(expected_positions.get(key, value), value)
        expected_rows = sorted((key[0], value[1], value[0], key[1], current['source_revision']) for key, value in expected_positions.items())
        assert sorted(tuple(row) for row in connection.execute('SELECT fen_key,route_json,ply,in_scope,source_revision FROM canonical_prefix_positions WHERE preview_id=? AND source_revision=?',
                                                             (current['preview_id'], current['source_revision']))) == expected_rows


@pytest.mark.parametrize('admission', ['pgn', 'paste'])
def test_canonical_selected_batch_resolves_multi_hop_continuations(prefix_database, quiet_prefix_writes, admission):
    from app.services.canonical_prefix import line_origin, read_prefix
    lines = selected_batch_fixture()
    second_route = [*lines[0][1], *lines[1][1]]
    lines.append((prefix_projection(second_route)['ending_fen'], ['d7d6', 'e1g1']))
    admit_selected_batch(admission, list(reversed(lines)))
    with database.read_connection() as connection:
        current = read_prefix(connection, 'italian')
        full_route = [*second_route, *lines[2][1]]
        assert line_origin(connection, current['preview_id'], prefix_projection(full_route)['ending_fen']) == full_route
        assert connection.execute("SELECT COUNT(*) FROM repertoire_lines WHERE repertoire_id='italian'").fetchone()[0] == 4


@pytest.mark.parametrize('admission', ['pgn', 'paste'])
@pytest.mark.parametrize('invalid_kind', ['disconnected', 'prefix-conflicting'])
@pytest.mark.parametrize('reverse_order', [False, True])
def test_canonical_selected_batch_rejects_disconnected_or_prefix_conflicting_routes_atomically(prefix_database, quiet_prefix_writes, admission, invalid_kind, reverse_order):
    from fastapi import HTTPException
    lines = selected_batch_fixture(repeated=True)
    if invalid_kind == 'disconnected':
        invalid_line = (prefix_projection(['d2d4', 'd7d5', 'c2c4'])['ending_fen'], ['g8f6', 'b1c3'])
    else:
        invalid_line = (chess.STARTING_FEN, ['e2e4', 'e7e5', 'g1f3', 'd7d6', 'f1c4'])
    before = selected_batch_snapshot()
    with pytest.raises(HTTPException) as rejection:
        selected = [*lines, invalid_line]
        admit_selected_batch(admission, list(reversed(selected)) if reverse_order else selected)
    assert rejection.value.status_code == 409
    assert selected_batch_snapshot() == before


def test_canonical_selected_batch_never_borrows_another_repertoires_routes(prefix_database, quiet_prefix_writes):
    from fastapi import HTTPException
    lines = selected_batch_fixture()
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-05')")
        connection.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('other-root','other','Root','white',?,?,'2026-10-05')", (chess.STARTING_FEN, json.dumps(ITALIAN)))
        preview = request_preview(connection, 'other', ITALIAN)
    for _ in range(100):
        task = claim_task('canonical_prefix_preview')
        if task is None:
            break
        execute_prefix_preview_slice(task)
    else:
        pytest.fail('Other destination prefix fixture did not finish')
    with database.connection() as connection:
        save_prefix(connection, {'repertoire_id': 'other', 'request': {'preview_id': preview['preview_id'], 'expected_revision': preview['revision']}})
    before = selected_batch_snapshot()
    with pytest.raises(HTTPException) as rejection:
        admit_selected_batch('paste', lines, [
            {'index': 0, 'repertoire_id': 'italian', 'acknowledge_conflict': True},
            {'index': 1, 'repertoire_id': 'other', 'acknowledge_conflict': True}])
    assert rejection.value.status_code == 409
    assert selected_batch_snapshot() == before


@pytest.mark.parametrize('invalid_source', ['unselected-connector', 'stale-certificate'])
def test_canonical_selected_batch_ignores_unselected_connectors_and_stale_certificates(prefix_database, quiet_prefix_writes, invalid_source):
    from fastapi import HTTPException
    lines = selected_batch_fixture()
    if invalid_source == 'stale-certificate':
        add_line(lines[0][1], 'previous-connector')
        apply_preview(prepare_prefix())
        add_line([*ITALIAN, 'g8f6', 'd2d3'], 'certificate-invalidating-source')
    before = selected_batch_snapshot()
    with pytest.raises(HTTPException) as rejection:
        admit_selected_batch('paste', lines, [{'index': 1, 'repertoire_id': 'italian', 'acknowledge_conflict': True}])
    assert rejection.value.status_code == 409
    assert selected_batch_snapshot() == before


@pytest.mark.parametrize('admission', ['pgn', 'paste'])
def test_canonical_selected_batch_preserves_duplicates_and_certifies_final_source_revision(prefix_database, quiet_prefix_writes, admission):
    from app.services.canonical_prefix import line_origin, read_prefix
    lines = selected_batch_fixture()
    unrelated_route = [*ITALIAN, 'g8f6', 'd2d3']
    add_line(unrelated_route, 'unrelated-current-route')
    apply_preview(prepare_prefix())
    selected = [*lines, lines[0], (chess.STARTING_FEN, unrelated_route)]
    result = admit_selected_batch(admission, selected)
    if admission == 'paste':
        assert [item['duplicate'] for item in result['saved']] == [False, False, True, True]
    else:
        assert result.unique_lines == 3
    with database.read_connection() as connection:
        current = read_prefix(connection, 'italian')
        for route in (lines[0][1], [*lines[0][1], *lines[1][1]]):
            ending_fen = prefix_projection(route)['ending_fen']
            assert line_origin(connection, current['preview_id'], ending_fen) == route
            assert connection.execute('SELECT source_revision FROM canonical_prefix_positions WHERE preview_id=? AND fen_key=? AND in_scope=1', (current['preview_id'], position_key_for_test(ending_fen))).fetchone()[0] == current['source_revision']
        assert line_origin(connection, current['preview_id'], prefix_projection(unrelated_route)['ending_fen']) is None
        if admission == 'pgn':
            assert connection.execute("SELECT comment FROM position_annotations WHERE repertoire_id='italian' AND fen_key=?", (position_key_for_test(prefix_projection(lines[0][1])['ending_fen']),)).fetchone()[0] == 'Selected route annotation'
        before_revision = current['source_revision']
        before_lines = connection.execute("SELECT COUNT(*) FROM repertoire_lines WHERE repertoire_id='italian'").fetchone()[0]
        before_certificates = [tuple(row) for row in connection.execute('SELECT * FROM canonical_prefix_positions ORDER BY preview_id,fen_key,in_scope')]
    replayed = admit_selected_batch(admission, selected)
    if admission == 'paste':
        assert all(item['duplicate'] for item in replayed['saved'])
    with database.read_connection() as connection:
        assert read_prefix(connection, 'italian')['source_revision'] == before_revision
        assert connection.execute("SELECT COUNT(*) FROM repertoire_lines WHERE repertoire_id='italian'").fetchone()[0] == before_lines
        assert [tuple(row) for row in connection.execute('SELECT * FROM canonical_prefix_positions ORDER BY preview_id,fen_key,in_scope')] == before_certificates


@pytest.mark.parametrize('reverse_order', [False, True])
def test_canonical_selected_batch_validation_uses_ranked_origins_without_persisting_certificates(prefix_database, reverse_order):
    from app.services.canonical_prefix import ensure_batch_lines_in_scope
    add_line(ITALIAN)
    apply_preview(prepare_prefix())
    connector = [*ITALIAN, 'c6b8', 'c4f1', 'g8f6', 'f3g1', 'f6g8', 'g1f3']
    repeated_position = prefix_projection(connector)['ending_fen']
    assert position_key_for_test(repeated_position) == position_key_for_test(prefix_projection(ITALIAN[:3])['ending_fen'])
    candidates = [('italian', chess.STARTING_FEN, connector), ('italian', repeated_position, ['g8f6', 'f1c4'])]
    if reverse_order:
        candidates.reverse()
    before = selected_batch_snapshot()
    with database.connection() as connection:
        validations = ensure_batch_lines_in_scope(connection, candidates)
        continuation_index = 0 if reverse_order else 1
        # The connector first reaches this position at ply 7; later repetitions
        # cannot displace the shortest verified in-scope origin.
        assert validations[continuation_index]['origin'] == connector[:7]
    assert selected_batch_snapshot() == before


@pytest.mark.parametrize('scenario', [
    'repeated-forward', 'repeated-reverse', 'short-then-long', 'long-then-short',
    'separate-verified-routes', 'tie-forward', 'tie-reverse', 'newer-longer', 'older-shorter',
])
def test_canonical_route_certification_preserves_best_verified_origin_for_each_source_revision(prefix_database, scenario):
    """CP-1/7: assert committed certificates, including epoch replacement."""
    from app.services.canonical_prefix import read_prefix, store_positions, line_origin
    fixture = canonical_route_fixture()
    repeated = fixture['repeated_connector']
    short = repeated[:7]
    preferred_tie = repeated[:9]
    alternative_tie = fixture['equal_ply_alternative']
    cases = {
        'repeated-forward': ([(repeated, False, False, 0)], short, 0),
        'repeated-reverse': ([(repeated, True, False, 0)], short, 0),
        'short-then-long': ([(short, False, True, 0), (repeated, False, True, 0)], short, 0),
        'long-then-short': ([(repeated, False, True, 0), (short, False, True, 0)], short, 0),
        'separate-verified-routes': ([(fixture['current_longer_connector'], False, True, 0), (short, False, True, 0)], short, 0),
        'tie-forward': ([(preferred_tie, False, True, 0), (alternative_tie, False, True, 0)], preferred_tie, 0),
        'tie-reverse': ([(alternative_tie, False, True, 0), (preferred_tie, False, True, 0)], preferred_tie, 0),
        'newer-longer': ([(short, False, True, 0), (repeated, False, True, 1)], repeated, 1),
        'older-shorter': ([(repeated, False, True, 1), (short, False, True, 0)], repeated, 1),
    }
    calls, expected_route, revision_offset = cases[scenario]
    add_line(ITALIAN)
    apply_preview(prepare_prefix())
    with database.read_connection() as connection:
        initial = read_prefix(connection, 'italian')
    advanced = False
    for route, reverse_positions, endpoint_only, offset in calls:
        if offset and not advanced:
            add_line([*ITALIAN, 'f8c5'], 'newer-source')
            advanced = True
        validation = validate_scoped_line(chess.STARTING_FEN, route, ITALIAN, [])
        assert validation['status'] == 'valid'
        positions = validation['positions'][-1:] if endpoint_only else validation['positions']
        with database.connection() as connection:
            store_positions(connection, initial['preview_id'], list(reversed(positions)) if reverse_positions else positions,
                            source_revision=initial['source_revision'] + offset)
    target = position_key_for_test(prefix_projection(expected_route)['ending_fen'])
    with database.read_connection() as connection:
        stored = connection.execute('SELECT route_json,ply,in_scope,source_revision FROM canonical_prefix_positions WHERE preview_id=? AND fen_key=? AND in_scope=1',
                                    (initial['preview_id'], target)).fetchone()
        assert tuple(stored) == (json.dumps(expected_route), len(expected_route), 1, initial['source_revision'] + revision_offset)
        assert line_origin(connection, initial['preview_id'], prefix_projection(expected_route)['ending_fen']) == expected_route
        if scenario.startswith('repeated-'):
            outside = connection.execute('SELECT route_json,ply,in_scope,source_revision FROM canonical_prefix_positions WHERE preview_id=? AND fen_key=? AND in_scope=0',
                                         (initial['preview_id'], target)).fetchone()
            assert tuple(outside) == (json.dumps(ITALIAN[:3]), 3, 0, initial['source_revision'])


def seed_canonical_publication_lifecycle():
    from datetime import datetime, timezone
    from app import main
    from app.services.cards import card_id
    route = [*ITALIAN, 'f8c5', 'c2c3']
    add_line(route)
    identifier = card_id(chess.STARTING_FEN, ITALIAN)
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,'italian','response',?,?,'2026-10-05')", (identifier, chess.STARTING_FEN, json.dumps(ITALIAN)))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('italian',?)", (identifier,))
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','other.pgn','2026-10-05')")
        connection.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('lifecycle-game','lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,?)", (datetime.now(timezone.utc).isoformat(), chess.STARTING_FEN, json.dumps([*route, 'a7a6', 'd2d4'])))
    main.make_main_repertoire('italian', None)
    apply_preview(prepare_prefix())
    return identifier, position_key_for_test(prefix_projection(route)['ending_fen'])


def refresh_canonical_publications():
    """Exercise admissions and real bounded workers; only transport is stubbed."""
    from app import main
    from app.services import repertoire_coverage as coverage, repertoire_opportunities as opportunities
    from app.services.repertoire_comparison import compare_all_games
    from app.services.durable_tasks import complete_task
    compare_all_games()
    admitted = main.refresh_repertoire_coverage('italian', None)
    processed_nodes = 0
    for _ in range(30):
        node = coverage.claim_coverage_node()
        if node is None:
            break
        assert node['run_id'] == admitted['run_id']
        coverage.execute_coverage_node(node)
        processed_nodes += 1
    else:
        pytest.fail('Canonical lifecycle coverage did not finish its bounded fixture')
    assert processed_nodes > 0
    assert main.repertoire_coverage('italian')['status'] == 'complete'
    main.refresh_repertoire_opportunities('italian', None)
    for _ in range(80):
        task = claim_task('repertoire_opportunity')
        if task is None:
            break
        assert task['deduplication_key'] == 'italian'
        if not opportunities.execute_opportunity_slice(task):
            assert complete_task(task['id'], task['generation'], task['lease_token'])
    else:
        pytest.fail('Canonical lifecycle opportunity refresh did not finish')
    return admitted['run_id']


@pytest.fixture
def canonical_explorer_transport(monkeypatch):
    from app.services import repertoire_coverage as coverage
    def response(fen, *_):
        board = chess.Board(fen)
        moves = [move for move in ('f8c5', 'g8f6', 'a7a6') if chess.Move.from_uci(move) in board.legal_moves]
        assert moves
        return {'moves': [{'uci': move, 'white': 500, 'draws': 0, 'black': 0} for move in moves]}
    monkeypatch.setattr(coverage, '_fetch_explorer', response)
    previous_token = coverage.get_explorer_session_token()
    coverage.set_explorer_session_token('fixture-ephemeral-token')
    yield
    coverage.set_explorer_session_token(previous_token)


def assert_canonical_publication_visible(target_key):
    from app import main
    from app.services.canonical_scope_freshness import scope_identity, game_scope_generation
    visible = main.repertoire_opportunities('italian')['opportunities']
    selected = next(item for item in visible if item['fen_key'] == target_key and item['opponent_move_uci'] == 'a7a6')
    assert selected['id'] in {item['id'] for item in main.discoveries_feed()['discoveries']}
    assert main.repertoire_coverage_gaps('italian')['gaps']
    assert main.repertoire_statistics_summary('italian', 'all')['games']['matched'] == 1
    with database.read_connection() as connection:
        current = {**scope_identity(connection, 'italian'), 'game_scope_generation': game_scope_generation(connection)}
        publication = dict(connection.execute('SELECT * FROM repertoire_opportunities WHERE id=?', (selected['id'],)).fetchone())
        assert all(publication[field] == value for field, value in current.items())
        for table in ('game_repertoire_matches', 'repertoire_comparisons', 'repertoire_decision_events'):
            assert connection.execute(f"SELECT 1 FROM current_{table} WHERE game_id='lifecycle-game'").fetchone()
    return selected['id']


def revise_lifecycle_source(identifier):
    from app import main
    from app.models import CardRevisionRequest
    main.revise_card(identifier, CardRevisionRequest(starting_fen=chess.STARTING_FEN, moves=[*ITALIAN, 'f8c5'], history_mode='preserve', expected_revision=1), None)


def test_canonical_scope_lifecycle_hides_stale_publications_rejects_actions_and_recovers(prefix_database, canonical_explorer_transport):
    """CP-4/5/6 through public reads, actual writes and ordinary refresh."""
    from fastapi import HTTPException
    from app import main
    from app.services.canonical_prefix import read_prefix
    identifier, target_key = seed_canonical_publication_lifecycle()
    old_run = refresh_canonical_publications()
    opportunity_id = assert_canonical_publication_visible(target_key)
    with database.read_connection() as connection:
        old_prefix = read_prefix(connection, 'italian')
        before = dict(connection.execute('SELECT * FROM repertoire_opportunities WHERE id=?', (opportunity_id,)).fetchone())
    revise_lifecycle_source(identifier)
    assert main.repertoire_coverage('italian')['run_id'] is None
    assert main.repertoire_coverage_gaps('italian')['gaps'] == []
    assert main.repertoire_opportunities('italian')['opportunities'] == []
    assert main.discoveries_feed()['total'] == 0
    assert main.repertoire_statistics_summary('italian', 'all')['games']['matched'] == 0
    for action in (main.dismiss_repertoire_opportunity, main.acknowledge_repertoire_opportunity, main.snooze_repertoire_opportunity):
        with pytest.raises(HTTPException) as rejection:
            action('italian', opportunity_id, None)
        assert rejection.value.status_code == 409 and 'refresh' in rejection.value.detail.lower()
        with database.read_connection() as connection:
            assert dict(connection.execute('SELECT * FROM repertoire_opportunities WHERE id=?', (opportunity_id,)).fetchone()) == before
            for table in ('game_repertoire_matches', 'repertoire_comparisons', 'repertoire_decision_events'):
                assert not connection.execute(f"SELECT 1 FROM current_{table} WHERE game_id='lifecycle-game'").fetchone()
    apply_preview(prepare_prefix())
    assert refresh_canonical_publications() != old_run
    assert assert_canonical_publication_visible(target_key) == opportunity_id
    with database.read_connection() as connection:
        current = read_prefix(connection, 'italian')
        assert current['revision'] == old_prefix['revision'] and current['source_revision'] > old_prefix['source_revision']
        assert current['preview_id'] != old_prefix['preview_id']
        assert {row[0] for row in connection.execute('SELECT source_revision FROM canonical_prefix_positions WHERE preview_id=?', (current['preview_id'],))} == {current['source_revision']}


@pytest.mark.parametrize('dimension', ['prefix', 'source', 'global'])
def test_canonical_publication_identities_advance_independently_through_product_commands(prefix_database, canonical_explorer_transport, dimension):
    """Separate identity fences while preserving intentional global coupling."""
    from app import main
    from app.services.canonical_prefix import read_prefix
    from app.services.canonical_scope_freshness import game_scope_generation
    identifier, target_key = seed_canonical_publication_lifecycle()
    refresh_canonical_publications()
    opportunity_id = assert_canonical_publication_visible(target_key)
    with database.read_connection() as connection:
        before = read_prefix(connection, 'italian')
        before_global = game_scope_generation(connection)
    if dimension == 'prefix':
        apply_preview(prepare_prefix([*ITALIAN, 'f8c5']))
    elif dimension == 'source':
        revise_lifecycle_source(identifier)
    else:
        main.make_main_repertoire('other', None)
    with database.read_connection() as connection:
        after = read_prefix(connection, 'italian')
        assert (after['revision'] > before['revision']) == (dimension == 'prefix')
        assert (after['source_revision'] > before['source_revision']) == (dimension == 'source')
        assert (after['preview_id'] != before['preview_id']) == (dimension == 'prefix')
        assert game_scope_generation(connection) > before_global
        after_global = game_scope_generation(connection)
    assert main.repertoire_opportunities('italian')['opportunities'] == []
    assert main.discoveries_feed()['total'] == 0
    apply_preview(prepare_prefix(after['moves']))
    with database.read_connection() as connection:
        rechecked = read_prefix(connection, 'italian')
        assert (rechecked['revision'], rechecked['source_revision'], game_scope_generation(connection)) == (after['revision'], after['source_revision'], after_global)
    refresh_canonical_publications()
    assert assert_canonical_publication_visible(target_key) == opportunity_id
