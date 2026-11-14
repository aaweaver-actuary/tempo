"""Scoped settings/evidence/coverage composition and savepoint ownership checks."""

import ast
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import repertoire_settings, review_commands
from app.contracts.review_conflicts import ReviewConflict
from app.services import study_mode


def test_six_control_compositions_delegate_all_sql_and_savepoints_to_database_owner():
    application = Path(__file__).resolve().parents[1] / "app"
    for relative in (
        "repertoire_settings.py",
        "review_commands.py",
        "opening_evidence_api.py",
        "services/study_mode.py",
        "services/postgres_coverage_candidates.py",
        "services/postgres_coverage_recovery.py",
    ):
        path = application / relative
        calls = [
            node
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"execute", "execute_native", "executemany", "cursor"}
        ]
        assert not calls, f"{relative} still executes SQL or savepoint statements"


def test_repertoire_limit_and_study_mode_facts_keep_inheritance_and_missing_settings_error():
    with sqlite3.connect(":memory:") as database:
        database.executescript("""
            CREATE TABLE settings(id INTEGER PRIMARY KEY,new_cards_per_day INTEGER,study_only_mode INTEGER);
            CREATE TABLE repertoires(id TEXT PRIMARY KEY,new_cards_per_day INTEGER);
            INSERT INTO settings VALUES(1,10,0);
            INSERT INTO repertoires VALUES('inherited',NULL),('overridden',3);
        """)
        assert repertoire_settings.effective_opening_limits(database) == {
            "inherited": 10,
            "overridden": 3,
        }
        assert repertoire_settings.repertoire_settings_response(
            database, "inherited"
        ) == {
            "repertoire_id": "inherited",
            "new_cards_per_day": None,
            "effective_new_cards_per_day": 10,
        }
        assert repertoire_settings.repertoire_settings_response(
            database, "overridden"
        ) == {
            "repertoire_id": "overridden",
            "new_cards_per_day": 3,
            "effective_new_cards_per_day": 3,
        }
        assert study_mode.study_only_enabled(database) is False
        database.execute("UPDATE settings SET study_only_mode=1")
        assert study_mode.study_only_enabled(database) is True
        database.execute("DELETE FROM settings")
        with pytest.raises(HTTPException) as error:
            study_mode.study_only_enabled(database)
        assert error.value.status_code == 503


def test_system_repertoire_settings_rejection_precedes_storage_read():
    class NoRead:
        def execute(self, *_arguments):
            raise AssertionError("System-repertoire guard read storage")

    with pytest.raises(HTTPException) as error:
        repertoire_settings.repertoire_settings_response(
            NoRead(), next(iter(repertoire_settings.SYSTEM_REPERTOIRES))
        )
    assert error.value.status_code == 404


def test_handled_review_conflict_rolls_back_only_savepoint_and_keeps_outer_transaction(
    monkeypatch,
):
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE TABLE evidence(value TEXT)")
        database.execute("INSERT INTO evidence VALUES('saved-checkpoint')")
        assert database.in_transaction

        def submit(_database, _payload):
            database.execute("INSERT INTO evidence VALUES('new-conflicting-effect')")
            raise ReviewConflict("stale_review", "Stored attempt changed")

        monkeypatch.setattr(review_commands, "submit_review", submit)
        result = review_commands.reconcile_review(SimpleNamespace(raw=database), {})
        assert result == {
            "persisted": False,
            "conflict": {
                "code": "stale_review",
                "message": "Stored attempt changed",
                "retryable": False,
            },
        }
        assert database.execute("SELECT value FROM evidence").fetchall() == [
            ("saved-checkpoint",)
        ]
        assert database.in_transaction
        database.rollback()
        assert database.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
