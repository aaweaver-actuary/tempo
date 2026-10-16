"""Study priority: bound examined candidates, not only returned unlocks."""

from app.services import queue_application


def test_postgres_unlock_pages_advance_past_rejected_candidates_without_parent_scan():
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows

    class Database:
        def __init__(self): self.calls = []
        def execute_native(self, statement, parameters):
            self.calls.append((statement, parameters))
            if "state='locked'" in statement and statement.startswith("SELECT id FROM cards"):
                return Result([(f"card-{index:04}",) for index in range(17)])
            assert "bounded_cards AS MATERIALIZED" in statement
            assert "publication.state='ready'" in statement
            assert "FROM cards parent\n    CROSS JOIN LATERAL" not in statement
            return Result([])
        def execute(self, statement, parameters):
            raise AssertionError("Rejected candidates must not issue an unbounded UPDATE")

    database = Database()
    cursor = queue_application._unlock_eligible_opening_cards(
        database, '2026-10-16', after_card_id='', batch_size=8)
    assert cursor == 'card-0015'
    assert database.calls[0][1] == ('', 17)
    assert len(database.calls[1][1][0]) == 16


def test_postgres_unlock_dense_page_preserves_eight_write_bound_and_commit_recheck():
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows

    class Database:
        def __init__(self): self.updated = []
        def execute_native(self, statement, parameters):
            if statement.startswith("SELECT id FROM cards"):
                return Result([(f"card-{index:04}",) for index in range(20)])
            return Result([(f"card-{index:04}",) for index in range(8)])
        def execute(self, statement, parameters):
            assert "opening_graph_publications" in statement
            assert "invalidated_at IS NULL" in statement
            self.updated.extend(parameters)

    database = Database()
    assert queue_application._unlock_eligible_opening_cards(
        database, '2026-10-16', after_card_id='', batch_size=8) == 'card-0007'
    assert database.updated == [f"card-{index:04}" for index in range(8)]


def test_study_priority_maintenance_setup_timeouts_and_closed_connections_back_off_without_losing_work(monkeypatch):
    import psycopg
    from app import tasks
    now = [100.0]
    monkeypatch.setattr(tasks.time, 'monotonic', lambda: now[0])
    calls = []
    @tasks.bounded_maintenance
    def owned_recovery_regression():
        calls.append(now[0])
        if len(calls) == 1: raise psycopg.errors.TransactionTimeout('setup timeout')
        if len(calls) == 2: raise psycopg.InterfaceError('closed connection')
        return 'durable checkpoint resumed'
    assert owned_recovery_regression() is False
    assert owned_recovery_regression() is False and len(calls) == 1
    now[0] += 1
    assert owned_recovery_regression() is False and len(calls) == 2
    now[0] += 1
    assert owned_recovery_regression() is False and len(calls) == 2
    now[0] += 1
    assert owned_recovery_regression() == 'durable checkpoint resumed'
    assert 'owned_recovery_regression' not in tasks._maintenance_cooldowns
