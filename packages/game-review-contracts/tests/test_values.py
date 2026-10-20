import ast
from pathlib import Path

import chess
import pytest
from pydantic import ValidationError
from tempo_game_review_contracts import ActionRequest, CanonicalGame, RepertoireSnapshot
from tempo_game_review_contracts.replay import replay_line


def game(**changes):
    return CanonicalGame(**dict(workspace_id="fixture", game_id="game", game_revision=1,
        source_id="pgn", source_revision=1, source_digest="0" * 64,
        starting_fen=chess.STARTING_FEN, moves=["e2e4"], learner_color="white", played_at=None) | changes)


def test_unknown_game_time_is_explicit_and_known_time_needs_zone():
    assert game().played_at is None
    assert game(played_at="2026-10-20T12:00:00Z").played_at.endswith("Z")
    with pytest.raises(ValidationError):
        game(played_at="2026-10-20T12:00:00")
    with pytest.raises(ValidationError):
        game(played_at="not-a-time")


def test_values_reject_extra_fields_and_mutable_attribute_assignment():
    with pytest.raises(ValidationError):
        game(learning_eligible=True)
    with pytest.raises(ValidationError):
        game().game_revision = 2


def test_snapshot_preserves_explicit_unavailable_scope_without_inventing_routes():
    snapshot = RepertoireSnapshot(workspace_id="fixture", snapshot_id="snapshot", revision=1,
        digest="1" * 64, source_revision="owner-r1", captured_at="2026-10-20T12:00:00Z",
        scope_id="selected", scope_repertoire_ids=["repertoire"], complete=False,
        available=False, unavailable_reason="owner unavailable", routes=[])
    assert not snapshot.available and not snapshot.complete and snapshot.routes == []
    with pytest.raises(ValidationError):
        snapshot.model_copy().model_validate(snapshot.model_dump() | {"captured_at": "2026-10-20T12:00:00"})


def test_shared_replay_retains_start_terminal_legal_ep_and_rejects_illegal_line():
    positions = replay_line(chess.STARTING_FEN, ["e2e4", "e7e5"])
    assert [position["ply"] for position in positions] == [0, 1, 2]
    assert positions[1]["fen"].split()[3] == "e3"
    assert positions[2]["move"] is None
    with pytest.raises(ValueError):
        replay_line(chess.STARTING_FEN, ["e2e5"])


def test_action_placeholder_has_no_executable_mutation_payload():
    assert ActionRequest(workspace_id="fixture", report_id="report").report_id == "report"
    with pytest.raises(ValidationError):
        ActionRequest(workspace_id="fixture", report_id="report", sql="anything")


def test_shared_contract_imports_no_feature_or_storage_implementation():
    forbidden = ("tempo_game_ingestion", "tempo_repertoire_comparison", "tempo_game_evidence",
                 "tempo_learning", "tempo_content", "psycopg", "httpx", "fastapi", "sqlite3")
    for source in (Path(__file__).parents[1] / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(name.startswith(forbidden) for name in names)
