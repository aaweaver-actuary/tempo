"""Leases and retries must not erase a PostgreSQL slice's saved phase."""

from app import postgres_store
from app.services import durable_tasks


class Cursor:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class Database:
    def __init__(self):
        self.statements = []
        self.task = {"id": "task-one", "kind": "opening_graph_rebuild",
                     "generation": 2, "state": "queued", "phase": "link",
                     "payload_json": '{"repertoire_id":"opening-one"}',
                     "attempt_count": 1, "max_attempts": 5,
                     "lease_token": "lease-one"}

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        if statement.startswith("SELECT * FROM background_tasks"):
            return Cursor(self.task)
        if statement.startswith("SELECT attempt_count,max_attempts"):
            return Cursor(self.task)
        if statement.startswith("UPDATE background_tasks") and "state='leased'" in statement:
            self.task["state"] = "leased"
            return Cursor(rowcount=1)
        return Cursor(rowcount=1)


def test_postgres_durable_claim_preserves_phase_after_expired_lease(monkeypatch):
    database = Database()
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(durable_tasks, "submit_background_write",
                        lambda operation, *, label: operation(database))
    claimed = durable_tasks.claim_task("opening_graph_rebuild")
    assert claimed["phase"] == "link"
    assert claimed["payload"] == {"repertoire_id": "opening-one"}
    reclaim_sql = database.statements[0][0]
    claim_sql = next(statement for statement, _ in database.statements
                     if "SET state='leased'" in statement)
    assert "phase=" not in reclaim_sql
    assert "phase=" not in claim_sql


def test_postgres_durable_retry_and_contention_preserve_phase(monkeypatch):
    database = Database()
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(durable_tasks, "submit_background_write",
                        lambda operation, *, label: operation(database))
    assert durable_tasks.fail_task("task-one", 2, "lease-one", RuntimeError("temporary"))["state"] == "retrying"
    retry_sql = next(statement for statement, _ in database.statements
                     if "SET state=?,phase=" in statement)
    assert "phase=phase" in retry_sql
    database.statements.clear()
    assert durable_tasks.defer_task_for_contention("task-one", 2, "lease-one")
    yield_sql = next(statement for statement, _ in database.statements
                     if "SET state='retrying'" in statement)
    assert "phase=" not in yield_sql
