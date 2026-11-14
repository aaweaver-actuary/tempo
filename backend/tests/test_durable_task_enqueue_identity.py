"""#144: events/results must use the identity selected by the UPSERT."""

import pytest

from app.services import durable_tasks


@pytest.mark.parametrize(
    "existing",
    [
        None,
        {"id": "existing-task", "generation": 7, "created_at": "2026-10-15T12:00:00Z"},
    ],
    ids=["concurrent-first-creator", "existing-generation-replacement"],
)
def test_enqueue_events_and_result_use_postgres_returned_identity(
    monkeypatch, existing
):
    authoritative = {
        "id": "database-selected-task",
        "generation": 23,
        "created_at": "2026-10-15T12:00:00Z",
        "replaced_pending_generation": 1,
    }
    events = []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters):
            if statement == durable_tasks._TASK_BY_KIND_SQL:
                return Cursor(existing)
            if statement.startswith(durable_tasks._TASK_UPSERT_SQL):
                assert statement.endswith(" RETURNING *")
                return Cursor(authoritative)
            assert statement == durable_tasks._TASK_BY_ID_SQL
            assert parameters == ("database-selected-task",)
            return Cursor(authoritative)

    monkeypatch.setattr(
        durable_tasks,
        "_record_event",
        lambda db, task_id, generation, event, phase, **kwargs: events.append(
            (task_id, generation, event)
        ),
    )
    result = durable_tasks.enqueue_task_in_transaction(
        Database(), "identity-regression", "coalesced", {"value": 1}
    )
    assert result == authoritative
    assert events == [
        ("database-selected-task", 23, "enqueued"),
        ("database-selected-task", 23, "generation_replaced"),
    ]


def test_visible_tasks_compares_text_timestamps_as_instants_without_rewriting_history():
    """Q0: /system/tasks failed with text >= timestamptz on native PostgreSQL.

    This focused adapter check guards the storage type boundary and borrowed
    read behavior; the real integrity-recovery browser case executes the SQL.
    """
    import re

    from tempo_storage.durable_tasks import storage

    retained = [
        {
            "id": "original-task",
            "state": "complete",
            "updated_at": "2026-11-12T09:00:00-05:00",
        }
    ]
    calls = []

    class BorrowedReader:
        def execute(self, statement):
            calls.append(statement)
            # The column remains TEXT for retained history. Comparing text to
            # CURRENT_TIMESTAMP is invalid; lexicographic comparison would be
            # wrong for offset-bearing timestamps. Keep the cast on the column.
            assert re.search(
                r"updated_at\s*::\s*timestamptz\s*>=\s*\(CURRENT_TIMESTAMP\s*-\s*INTERVAL '1 day'\)",
                statement,
            )
            assert "state!='complete' OR" in statement
            assert statement.lstrip().startswith("SELECT ")
            return self

        def fetchall(self):
            return retained

        def __getattr__(self, name):
            raise AssertionError("Unexpected transaction/history mutation: " + name)

    assert storage.visible_tasks(BorrowedReader()) is retained
    assert len(calls) == 1
    assert retained[0]["updated_at"] == "2026-11-12T09:00:00-05:00"
