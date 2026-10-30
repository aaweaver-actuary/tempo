"""Regressions for comparing a legacy snapshot with an extended PG schema."""

from contextlib import contextmanager
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from database.tooling.migrate_sqlite_to_postgres import destination_fingerprint


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



def test_postgres_fingerprint_preserves_complete_ordered_digest_across_batches():
    rows = [
        (f"key-{index:04d}", None if index % 7 == 0 else index, index / 8.0,
         bytes([index % 256, (index // 256) % 256]), f"雪-é-{index}")
        for index in range(2501)
    ]
    consumed = []
    configured_at_execute = []

    class Cursor:
        def __init__(self):
            self.itersize = 100

        def __enter__(self):
            return self

        def __exit__(self, *_arguments):
            return False

        def execute(self, _statement):
            configured_at_execute.append(self.itersize)

        def __iter__(self):
            for offset in range(0, len(rows), self.itersize):
                for row in rows[offset:offset + self.itersize]:
                    consumed.append(row)
                    yield row

    class Database:
        @contextmanager
        def transaction(self):
            yield

        def cursor(self, *, name):
            assert name == "verify_batch_rows"
            return Cursor()

    count, digest = destination_fingerprint(
        Database(), "batch_rows", ["id", "nullable", "floating", "binary", "text"],
        ["id"], {"id"},
    )
    # Captured from the unchanged helper at 991e5f3279dc96c6ebbca0a9c278b8b69a324431.
    assert count == 2501
    assert consumed == rows
    assert digest == "37e3b76e823ed0a6ea4d11e744ccd3c412cd19dc72721601347fde73661b6442"
    # The first 2,000 rows have a different frozen digest: tail rows matter.
    assert digest != "01a2e581131e3c9e64d60ff89d7ba2d53d486e8c32d14b8a5e3949d2b0f78e3a"
    assert configured_at_execute == [1000]


@pytest.mark.parametrize("table_name,trigger_name", [
    ("repertoire_lines", "canonical_line_source"),
    ("repertoire_cards", "canonical_link_source"),
    ("cards", "canonical_card_insert_source"),
])
def test_canonical_prefix_snapshot_copy_preserves_source_revision_and_restores_trigger(tmp_path, table_name, trigger_name):
    from contextlib import nullcontext
    import sqlite3
    from database.tooling.migrate_sqlite_to_postgres import copy_table
    source = sqlite3.connect(tmp_path / "source.db")
    source.execute(f'CREATE TABLE "{table_name}"(id TEXT PRIMARY KEY)')
    source.execute(f'INSERT INTO "{table_name}" VALUES(\'line\')')
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
    count, _ = copy_table(source, Destination(), table_name, ["id"], ["id"])
    source.close()
    assert count == 1 and rows == [("line",)]
    assert "DISABLE TRIGGER" in statements[0] and trigger_name in statements[0]
    assert "COPY" in statements[1]
    assert "ENABLE TRIGGER" in statements[2] and trigger_name in statements[2]
    assert "tempo_migration_progress" in statements[3]
