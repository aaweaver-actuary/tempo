import ast
import hashlib
import json
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


# Full replay dictionaries frozen from the pre-optimization shared owner.
@pytest.mark.parametrize("fen,moves,expected", [('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
  ('e2e4', 'e7e5', 'g1f3'),
  '673a41fa3f45d0c072a89a9d4a8748d1f34c10385db14a63a78bd4e6138b2ff5'),
 ('4k3/8/8/3pP3/8/8/8/4K3 w - d6 17 42',
  ('e5d6', 'e8d7'),
  '1edfb2f580a12ad93258b45d56925c0ea5ad772a546738a990c5e176be979733'),
 ('4r1k1/8/8/3pP3/8/8/8/4K3 w - d6 13 11',
  ('e5e6', 'g8f8'),
  '3b05b405ae66d8ca72464ee623724a5e2bb4c711e51e6590370f996ec99e86c9'),
 ('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 4 17',
  ('e1g1', 'e8c8'),
  '7027592076f26aff5b268f09bef33187b1e7641cd8f1f6f8a7d8284607500f6e'),
 ('4k3/8/8/8/8/8/8/4K3 w - - 99 45',
  ('e1e2', 'e8e7'),
  'ce81549e5939cb922b4f12ba0cbfb332c5289637b7477704fbd7bf7f98b5d0e9'),
 ('rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1',
  ('c7c5', 'b1c3'),
  'fbf8f1772067dbbb3365d090479600be82cc8131577c2a7ca15cd9875cc4079f')], ids=['ordinary', 'legal-ep', 'pinned-ep', 'castling', 'clocks', 'irrelevant-ep'])
def test_shared_replay_renders_once_and_preserves_all_position_facts(monkeypatch, fen, moves, expected):
    original, calls = chess.Board.fen, []
    def render(board, *args, **kwargs):
        calls.append(kwargs.get("en_passant"))
        return original(board, *args, **kwargs)
    monkeypatch.setattr(chess.Board, "fen", render)
    values = replay_line(fen, moves)
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected
    assert calls == ["fen"] * (len(moves) + 1)


@pytest.mark.parametrize("fen,moves,reason", [
    (None, [], "invalid_starting_position"),
    ("8/8/8/8/8/8/8/8 w - - 0 1", [], "invalid_starting_position"),
    ("4r1k1/8/8/3pP3/8/8/8/4K3 w - d6 13 11", ["e5d6"], "illegal_index_source"),
    (chess.STARTING_FEN, ["e2e5"], "illegal_index_source"),
])
def test_shared_replay_rejects_invalid_roots_and_illegal_moves(fen, moves, reason):
    with pytest.raises(ValueError, match=reason):
        replay_line(fen, moves)
