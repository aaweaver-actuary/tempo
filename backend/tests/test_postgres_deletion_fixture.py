"""Disposable deletion fixture must preserve uncertain shared state on setup errors."""

from contextlib import contextmanager

import pytest
from database.tests import check_postgres_deletion as fixture


def test_deletion_fixture_setup_failure_preserves_unknown_retained_and_main_state(
    monkeypatch,
):
    original = RuntimeError("controlled setup failure")
    statements = []
    calls = 0

    class CleanupDatabase:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))

        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))

    @contextmanager
    def connection():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise original
        yield CleanupDatabase()

    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    monkeypatch.setattr(fixture.postgres, "connection", connection)
    with pytest.raises(RuntimeError, match="controlled setup failure") as caught:
        fixture.main_check()
    assert caught.value is original
    assert calls == 2
    assert not any(
        "DELETE FROM repertoires WHERE id=%s" in sql for sql, _ in statements
    )
    assert not any("UPDATE repertoires SET is_main" in sql for sql, _ in statements)
    assert len(statements) == 3
    assert all(
        not args[0]
        or all(str(value).startswith("deletion-proof-") for value in args[0])
        for _, args in statements
    )


def test_deletion_fixture_keeps_setup_error_when_cleanup_also_fails(monkeypatch):
    original = RuntimeError("controlled setup failure")
    calls = 0

    @contextmanager
    def connection():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise original
        raise ValueError("controlled cleanup failure")
        yield

    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    monkeypatch.setattr(fixture.postgres, "connection", connection)
    with pytest.raises(RuntimeError, match="controlled setup failure") as caught:
        fixture.main_check()
    assert caught.value is original
    assert any("controlled cleanup failure" in note for note in caught.value.__notes__)


@pytest.mark.parametrize(
    ("existed", "created", "should_remove"),
    [
        (None, False, False),
        (None, True, False),
        (True, True, False),
        (False, False, False),
        (False, True, True),
    ],
)
def test_retained_cleanup_requires_confirmed_absence_and_creation(
    existed, created, should_remove
):
    statements = []

    class Database:
        def execute(self, statement, parameters):
            statements.append((statement, parameters))

    fixture.remove_owned_retained_repertoire(
        Database(), retained_existed=existed, retained_created=created
    )
    assert statements == (
        [("DELETE FROM repertoires WHERE id=%s", (fixture.RETAINED_REPERTOIRE_ID,))]
        if should_remove
        else []
    )


def test_tactical_cleanup_failure_retains_original_proof_failure():
    original = RuntimeError("controlled tactical proof failure")
    cleanup = ValueError("controlled tactical cleanup failure")
    invoked = []

    def fail_cleanup():
        invoked.append(True)
        raise cleanup

    with pytest.raises(
        RuntimeError, match="controlled tactical proof failure"
    ) as caught:
        try:
            raise original
        finally:
            fixture.preserve_tactical_cleanup_failure(fail_cleanup)
    assert caught.value is original and invoked == [True]
    assert any(
        "controlled tactical cleanup failure" in note for note in caught.value.__notes__
    )
    with pytest.raises(
        ValueError, match="controlled tactical cleanup failure"
    ) as caught_cleanup:
        fixture.preserve_tactical_cleanup_failure(fail_cleanup)
    assert caught_cleanup.value is cleanup


def test_deletion_fixture_manifest_owner_reads_bound_snapshot_without_writes(
    monkeypatch,
):
    import ast

    import chess

    from app.study import position_checkpoint

    tree = ast.parse(fixture.__loader__.get_source(fixture.__name__))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "queue_manifests" for alias in node.names)
    ]
    assert len(imports) == 1 and imports[0].module == "app.study.position_checkpoint"
    calls = []
    fen = chess.STARTING_FEN
    row = {
        "queue_entry_id": 11,
        "id": 42,
        "repertoire_id": "original-repertoire",
        "card_id": "original-card",
        "revision": 1,
        "start_fen": fen,
        "moves_json": '["e2e4"]',
        "trained_color": "white",
        "effective_trained_color": "white",
    }

    class Cursor:
        def fetchall(self):
            return [row]

    class ReadOnlyDatabase:
        def execute_native(self, statement, parameters):
            assert statement.lstrip().startswith("SELECT ")
            calls.append((statement, parameters))
            return Cursor()

    @contextmanager
    def connection():
        yield ReadOnlyDatabase()

    monkeypatch.setattr(position_checkpoint, "read_connection", connection)
    card = {
        "id": "original-card",
        "queue_entry_id": 11,
        "content_type": "opening",
        "revision": 1,
        "start_fen": fen,
        "moves": ["e2e4"],
        "trained_color": "white",
    }
    mismatched = {**card, "queue_entry_id": 12}
    position_checkpoint.queue_manifests([card, mismatched])
    assert len(calls) == 1 and calls[0][1] == ([11, 12],)
    assert card["opening_decision_manifest"]["presentation_snapshot_id"] == 42
    assert card["opening_decision_manifest"]["repertoire_id"] == "original-repertoire"
    assert card["opening_decision_manifest"]["card_id"] == "original-card"
    assert "opening_decision_manifest" not in mismatched
    assert (
        "Normal review remains available" in mismatched["opening_evidence_diagnostic"]
    )
