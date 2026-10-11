"""Queue fixture cleanup must preserve newer runtime ordering."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest


@pytest.mark.parametrize("current_position,expected_position,restored,changed,removed", [
    (64, 0, 1, 0, 0), (63, 63, 0, 1, 0), (None, None, 0, 0, 1),
], ids=["unchanged", "runtime_reordered", "runtime_removed"])
def test_training_fixture_cleanup_accounts_for_runtime_order_without_overwriting_it(
    monkeypatch, capsys, current_position, expected_position, restored, changed, removed,
):
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repository / "scripts"))
    specification = importlib.util.spec_from_file_location("training_transition_fixture_cleanup", repository / "scripts/check_postgres_training_transition.py")
    fixture = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(fixture)
    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE daily_queue(id INTEGER PRIMARY KEY,position INTEGER)")
    if current_position is not None:
        database.execute("INSERT INTO daily_queue VALUES(1,?)", (current_position,))

    class FixtureConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            database.commit()

        def execute(self, statement, parameters=()):
            return database.execute(statement.replace("%s", "?").replace(" FOR UPDATE", ""), parameters)

    identifier = "training-transition-01234567-89ab-4cde-8f01-23456789abcd"
    cleaned_identifiers = []
    monkeypatch.setattr(fixture, "configure", lambda: None)
    monkeypatch.setattr(fixture, "cleanup", cleaned_identifiers.append)
    monkeypatch.setattr(fixture.psycopg, "connect", lambda _: FixtureConnection())
    monkeypatch.setattr(sys, "argv", ["fixture", "cleanup", identifier, json.dumps([[1, 0, 64]])])
    try:
        fixture.main()
        result = json.loads(capsys.readouterr().out)
        assert sum(result.get(key, 0) for key in ["restored_queue_positions", "runtime_reordered_positions", "runtime_removed_entries"]) == 1
        assert result == {"restored_queue_positions": restored, "runtime_reordered_positions": changed, "runtime_removed_entries": removed}
        row = database.execute("SELECT position FROM daily_queue WHERE id=1").fetchone()
        assert (row[0] if row else None) == expected_position
        assert cleaned_identifiers == [identifier]
    finally:
        database.close()
