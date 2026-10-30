"""Command/storage boundaries retain caller rollback and existing replay behavior."""
import ast
import json
from pathlib import Path
import sqlite3

import pytest

from app.annotation_commands import save_position_annotation
from app.teaching_commands import record_teaching_state


class CommandSqlite:
    """Run these portable statements in memory; this is not native PostgreSQL proof."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace(" FOR UPDATE", ""), parameters)

    def execute_native(self, statement, parameters=()):
        return self.execute(statement.replace("%s", "?"), parameters)


@pytest.fixture
def command_database():
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE repertoires(id TEXT PRIMARY KEY);
            CREATE TABLE cards(id TEXT PRIMARY KEY,archived INTEGER);
            CREATE TABLE position_annotations(repertoire_id TEXT,fen_key TEXT,comment TEXT,
                arrows_json TEXT,squares_json TEXT,updated_at TEXT,PRIMARY KEY(repertoire_id,fen_key));
            CREATE TABLE teaching_states(card_id TEXT,revision INTEGER,ply INTEGER,taught_at TEXT,
                PRIMARY KEY(card_id,revision,ply));
            INSERT INTO repertoires VALUES('rep');
            INSERT INTO cards VALUES('card',0);
        """)
        yield CommandSqlite(connection)


def test_remaining_command_handlers_delegate_all_sql_execution_to_database_owner():
    application = Path(__file__).resolve().parents[1] / "app"
    for module in ("account", "activity", "annotation", "teaching"):
        path = application / f"{module}_commands.py"
        calls = [node for node in ast.walk(ast.parse(path.read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {"execute", "execute_native", "executemany", "cursor"}]
        assert not calls, f"{path.name} still performs SQL/cursor operations"


def test_teaching_replay_keeps_original_timestamp_and_caller_rollback(command_database):
    payload = {"card_id": "card", "teaching_state": {"revision": 2, "ply": 3}}
    first = record_teaching_state(command_database, payload)
    assert record_teaching_state(command_database, payload) == first
    assert command_database.connection.execute("SELECT COUNT(*) FROM teaching_states").fetchone()[0] == 1
    assert command_database.connection.in_transaction
    command_database.connection.rollback()
    assert command_database.connection.execute("SELECT COUNT(*) FROM teaching_states").fetchone()[0] == 0


def test_annotation_json_and_empty_delete_preserve_caller_transaction(command_database):
    annotation = {
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "comment": "  Plan  ", "arrows": [{"from": "e2", "to": "e4", "color": "green"}],
        "squares": [{"square": "d4", "color": "red"}],
    }
    first = save_position_annotation(command_database, {"repertoire_id": "rep", "annotation": annotation})
    row = command_database.connection.execute("SELECT * FROM position_annotations").fetchone()
    assert (row["comment"], json.loads(row["arrows_json"]), json.loads(row["squares_json"])) == (
        "Plan", first["arrows"], first["squares"])
    assert row["updated_at"] == first["updatedAt"]
    cleared = save_position_annotation(command_database, {
        "repertoire_id": "rep", "annotation": {"fen": annotation["fen"], "comment": " ", "arrows": [], "squares": []},
    })
    assert cleared["fenKey"] == first["fenKey"]
    assert command_database.connection.in_transaction
    assert command_database.connection.execute("SELECT COUNT(*) FROM position_annotations").fetchone()[0] == 0
