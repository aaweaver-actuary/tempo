"""Retained Study HTTP keeps physical reads and writes in the storage owner.

Original chapter import/router and cursor tests retired with the old editor.
Runtime frozen presentation, attempt, self-assessment and feedback coverage is
owned by test_frozen_study_routes.py (B31).
"""
import ast
from pathlib import Path
import re

from app import study_exercise_routes
from tempo_storage.study_attempts import storage


def test_study_http_keeps_database_operations_in_storage_owner():
    assert study_exercise_routes.storage is storage
    for node in ast.walk(ast.parse(Path(study_exercise_routes.__file__).read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "execute",
                "execute_native",
                "executemany",
                "configured",
            }
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Preserve the original English "Select at least one source record".
            assert not re.search(r"\bSELECT\b[\s\S]*\bFROM\b", node.value, re.I)
            assert not any(
                sql in node.value.upper()
                for sql in (
                    "INSERT INTO ",
                    "UPDATE CARDS ",
                    "UPDATE STUDY_",
                    "DELETE FROM ",
                )
            )
