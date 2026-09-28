"""Regressions for comparing a legacy snapshot with an extended PG schema."""

from contextlib import contextmanager
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from migrate_sqlite_to_postgres import destination_fingerprint


def test_postgres_cutover_digest_ignores_new_target_columns():
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_arguments):
            return False

        def execute(self, statement):
            statements.append(statement.as_string())

        def __iter__(self):
            return iter([("game-one", 2)])

    class Database:
        @contextmanager
        def transaction(self):
            yield

        def cursor(self, *, name):
            assert name == "verify_imported_games"
            return Cursor()

    assert destination_fingerprint(
        Database(), "imported_games", ["id", "analysis_version"], ["id"], {"id"}
    )[0] == 1
    assert statements == [
        'SELECT "id", "analysis_version" FROM "imported_games" '
        'ORDER BY "id" COLLATE "C"'
    ]
