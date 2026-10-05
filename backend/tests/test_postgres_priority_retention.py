"""Retention must select bounded rows from stale preparation manifests."""

from contextlib import contextmanager

from app.services import priority_retention


def test_postgres_priority_retention_selects_exact_stale_manifest_and_keeps_locked_rows_pending(monkeypatch):
    """A huge active preparation must not be scanned to find or exclude stale rows."""
    prepared_rows = {(2, 0), (2, 1), (4, 0)}
    manifests = {2, 4}
    locked_keys = {(2, 0), (2, 1)}
    inspected_generations = []
    followups = []
    statements = []

    class Cursor:
        def __init__(self, rows=()):
            self.rows = list(rows)

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def __iter__(self):
            return iter(self.rows)

    class Database:
        def execute(self, statement, parameters=()):
            statements.append(statement)
            if "FROM background_tasks" in statement:
                assert any("FROM repertoire_priority_jobs" in previous and "FOR UPDATE" in previous
                           for previous in statements[:-1]), "Producer and retention lock order must match"
                return Cursor([{"generation": 1, "lease_token": "lease", "state": "leased"}])
            if "FROM repertoire_priority_publications" in statement:
                return Cursor([{"generation": 3}])
            if "FROM repertoire_priority_jobs" in statement:
                return Cursor([{"generation": 4, "status": "running"}])
            if "SELECT generation FROM repertoire_priority_preparations" in statement:
                manifest_selection_sql = priority_retention.postgres_store.postgres_sql(statement)
                assert "ORDER BY generation" in manifest_selection_sql and "NULLS FIRST" not in manifest_selection_sql, (
                    "Translated manifest ordering must match the primary-key index"
                )
                return Cursor([{"generation": generation} for generation in sorted(manifests)
                               if generation != parameters[1]][:1])
            if "SELECT generation,ordinal FROM repertoire_priority_prepared_rows" in statement:
                prepared_selection_sql = priority_retention.postgres_store.postgres_sql(statement)
                assert "ORDER BY ordinal" in prepared_selection_sql and "NULLS FIRST" not in prepared_selection_sql, (
                    "Translated prepared-row ordering must stop at one primary-key slice"
                )
                assert "generation=?" in statement and "generation<>" not in statement, (
                    "Candidate rows must use one stale manifest generation, not filter the large active generation"
                )
                inspected_generations.append(parameters[1])
                return Cursor([{"generation": generation, "ordinal": ordinal}
                               for generation, ordinal in sorted(prepared_rows)
                               if generation == parameters[1] and (generation, ordinal) not in locked_keys][:parameters[2]])
            if "DELETE FROM repertoire_priority_preparations" in statement:
                assert parameters[1] == parameters[3] and "generation=?" in statement
                if not any(key[0] == parameters[1] for key in prepared_rows):
                    manifests.remove(parameters[1])
                return Cursor()
            if "FROM repertoire_card_priority_generations" in statement:
                return Cursor()
            if "SELECT 1 FROM repertoire_priority_prepared_rows" in statement:
                raise AssertionError("Manifest existence already covers prepared rows through their foreign key")
            if "SELECT 1 FROM repertoire_priority_preparations" in statement:
                return Cursor([(1,)] if any(generation != parameters[1] for generation in manifests) else [])
            raise AssertionError(statement)

        def executemany(self, statement, parameters):
            for _, generation, ordinal in parameters:
                assert "DELETE FROM repertoire_priority_prepared_rows" in statement
                prepared_rows.remove((generation, ordinal))

    database = Database()

    @contextmanager
    def connection(*, background):
        assert background
        original_executemany = database.executemany

        def delete_rows(statement, parameters):
            if "DELETE FROM repertoire_priority_preparations" in statement:
                for _, generation in parameters:
                    manifests.remove(generation)
            else:
                original_executemany(statement, parameters)

        database.executemany = delete_rows
        try:
            yield database
        finally:
            database.executemany = original_executemany

    monkeypatch.setattr(priority_retention, "connection", connection)
    monkeypatch.setattr(priority_retention.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(priority_retention.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(priority_retention, "enqueue_task_in_transaction", lambda *_args, **_kwargs: followups.append(True))
    task = {"id": "retention", "kind": "priority_retention", "generation": 1,
            "lease_token": "lease", "payload": {"repertoire_id": "rep"}}
    assert priority_retention.execute_priority_retention_slice(task) is True
    assert prepared_rows == {(2, 0), (2, 1), (4, 0)} and manifests == {2, 4}
    locked_keys.clear()
    assert priority_retention.execute_priority_retention_slice(task) is False
    assert prepared_rows == {(4, 0)} and manifests == {4}
    assert priority_retention.execute_priority_retention_slice(task) is False
    assert inspected_generations == [2, 2] and followups == [True]
