"""Candidate-bounded permanent-deletion filtering in tactical refresh slices."""
from types import SimpleNamespace

import pytest

from app.services import postgres_queue_refresh


@pytest.mark.parametrize("unrelated_tombstone_count", [0, 10_000])
@pytest.mark.parametrize("exhausted", [False, True])
def test_tactical_deleted_candidate_batches_preserve_rotated_order(monkeypatch, unrelated_tombstone_count, exhausted):
    records_by_pack = {
        "pack-a": [{"PuzzleId": f"a-{index}", "FEN": "source"} for index in range(3)],
        "pack-b": [{"PuzzleId": "b-0", "FEN": "source"}],
        "pack-c": [{"PuzzleId": f"c-{index}", "FEN": "source"} for index in range(19)],
    }
    tombstones = {f"unrelated-{index}" for index in range(unrelated_tombstone_count)}
    tombstones.update(f"c-{index}" for index in range(19))
    tombstones.add("a-0")
    if exhausted:
        tombstones.update(("a-2", "b-0"))
    lookup_batches = []

    def read(statement, parameters=(), *, native=False):
        if "deleted_cards" in statement:
            assert "WHERE card_id=ANY(%s::text[])" in statement, "Unbounded tombstone read"
            assert native
            candidate_ids = parameters[0]
            assert 1 <= len(candidate_ids) <= 16
            assert not any(identifier.startswith("unrelated-") for identifier in candidate_ids)
            lookup_batches.append(list(candidate_ids))
            return [(identifier,) for identifier in candidate_ids if identifier in tombstones]
        if "tactics_new_per_day" in statement:
            return [(1,)]
        if statement == postgres_queue_refresh.DAILY_TACTIC_COUNT_SQL:
            return [(0,)]
        if "tactic_pack_activation" in statement:
            return [("pack-b",), ("pack-a",), ("pack-c",)]
        if "tactic_rotation" in statement:
            return [("pack-b",)]
        if "tactic_progress" in statement:
            return [("a-1",)]
        raise AssertionError(statement)

    monkeypatch.setattr(postgres_queue_refresh, "_bounded_read", read)
    monkeypatch.setattr(postgres_queue_refresh, "pack_records", records_by_pack.__getitem__)
    monkeypatch.setattr(postgres_queue_refresh, "validate_puzzle_record", lambda record: (record["PuzzleId"], ["e2e4"]))
    monkeypatch.setattr(postgres_queue_refresh, "card_id", lambda fen, solution: fen)
    prepared = postgres_queue_refresh._prepare_tactical_introduction("2026-10-08")
    assert lookup_batches[:3] == [[f"c-{index}" for index in range(16)], ["c-16", "c-17", "c-18"], ["a-0", "a-2"]]
    if exhausted:
        assert prepared is None
        assert lookup_batches[3:] == [["b-0"]]
    else:
        assert prepared == {"pack_id": "pack-a", "puzzle_id": "a-2", "card_id": "a-2",
                            "training_fen": "a-2", "solution_json": '["e2e4"]',
                            "source_fen": "source", "rotation_cursor": "pack-b"}
        assert len(lookup_batches) == 3


def test_tactical_publication_rechecks_deleted_candidate_before_reserving_allowance():
    statements = []

    class PublicationDatabase:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))
            if "pg_advisory_xact_lock" in statement:
                return SimpleNamespace(fetchone=lambda: None)
            assert statement == "SELECT 1 FROM deleted_cards WHERE card_id=%s"
            return SimpleNamespace(fetchone=lambda: (1,))

    assert postgres_queue_refresh._publish_tactical_introduction(
        PublicationDatabase(), "2026-10-08", {"card_id": "deleted-after-preparation"},
    ) is True
    assert statements == [
        ("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:card-edit:deleted-after-preparation",)),
        ("SELECT 1 FROM deleted_cards WHERE card_id=%s", ("deleted-after-preparation",)),
    ]


def test_pr102_tactical_deletion_fixture_is_nonterminal_and_uuid_independent(monkeypatch):
    import importlib.util
    from pathlib import Path
    import chess
    from app.services.cards import card_id
    from app.services.puzzles import validate_puzzle_record

    script_path = Path(__file__).resolve().parents[2] / "scripts/check_postgres_deletion.py"
    monkeypatch.syspath_prepend(str(script_path.parent))
    specification = importlib.util.spec_from_file_location("pr102_deletion_proof", script_path)
    rehearsal = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(rehearsal)
    first = rehearsal.tactical_deletion_fixture_records("first-random-invocation")
    second = rehearsal.tactical_deletion_fixture_records("another-random-invocation")
    assert len(first) == len(second) == 3
    assert [(record["FEN"], record["Moves"]) for record in first] == [
        (record["FEN"], record["Moves"]) for record in second]
    identifiers = []
    for record in first:
        board = chess.Board(record["FEN"])
        assert board.is_valid() and not board.is_game_over()
        for uci_move in record["Moves"]:
            move = chess.Move.from_uci(uci_move)
            assert move in board.legal_moves
            board.push(move)
        identifiers.append(card_id(*validate_puzzle_record(record)))
    assert len(set(identifiers)) == 3
