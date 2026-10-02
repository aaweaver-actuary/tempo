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



def test_canonical_prefix_snapshot_copy_preserves_source_revision_and_restores_trigger(tmp_path):
    from contextlib import nullcontext
    import sqlite3
    from migrate_sqlite_to_postgres import copy_table
    source = sqlite3.connect(tmp_path / "source.db")
    source.execute("CREATE TABLE repertoire_lines(id TEXT PRIMARY KEY)")
    source.execute("INSERT INTO repertoire_lines VALUES('line')")
    statements = []
    rows = []
    class Copy:
        def write_row(self, row):
            rows.append(row)
    class Cursor:
        def execute(self, statement, parameters=()):
            statements.append(str(statement))
        def copy(self, statement):
            statements.append(str(statement))
            return nullcontext(Copy())
    class Destination:
        def transaction(self):
            return nullcontext()
        def cursor(self):
            return nullcontext(Cursor())
    count, _ = copy_table(source, Destination(), "repertoire_lines", ["id"], ["id"])
    source.close()
    assert count == 1 and rows == [("line",)]
    assert "DISABLE TRIGGER" in statements[0] and "canonical_line_source" in statements[0]
    assert "COPY" in statements[1]
    assert "ENABLE TRIGGER" in statements[2] and "canonical_line_source" in statements[2]
    assert "tempo_migration_progress" in statements[3]
