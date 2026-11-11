"""Focused transaction-contract checks; these do not replace a PostgreSQL run."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from tempo_storage import postgres


class Driver:
    def __init__(self):
        self.calls = []
        self.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)

    def execute(self, statement, parameters=()):
        self.calls.append((statement, parameters))
        return self


class Pool:
    def __init__(self):
        self.driver = Driver()
        self.options = None
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def connection(self, **options):
        self.options = options
        try:
            yield self.driver
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.committed = True


def test_native_sql_and_parameters_reach_driver_unchanged():
    raw = Driver()
    database = postgres.PostgresConnection(raw)
    sql = "SELECT payload::jsonb ->> 'move' FROM cards WHERE id=%s FOR UPDATE"
    parameters = ("card-1",)
    assert database.execute(sql, parameters) is raw
    assert raw.calls == [(sql, parameters)]
    assert database.in_transaction
    raw.info.transaction_status = TransactionStatus.INERROR
    assert not database.in_transaction


def test_named_and_positional_results_preserve_query_values():
    row = postgres.TempoRow(("id", "count"), ("card-1", 4))
    assert row["id"] == row[0] == "card-1"
    assert tuple(row) == ("card-1", 4)
    assert dict(row) == {"id": "card-1", "count": 4}


def test_authoritative_reads_use_writer_pool_but_read_only_transaction(monkeypatch):
    pool = Pool()
    selections = []

    def select(read_only, *, background=False):
        selections.append((read_only, background))
        return pool

    monkeypatch.setattr(postgres, "_pool", select)
    with postgres.connection(read_only=True, authoritative=True, background=True,
                             repeatable_read=True) as database:
        assert database.read_only
    assert selections == [(False, True)]
    assert pool.options == {"timeout": 0.1}
    assert pool.driver.calls[:2] == [
        ("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ", ()),
        ("SET TRANSACTION READ ONLY", ()),
    ]
    assert pool.driver.calls[2][1] == ("250ms", "25ms")
    assert pool.committed and not pool.rolled_back


def test_failed_writer_rolls_back_without_reporting_commit(monkeypatch):
    pool = Pool()
    monkeypatch.setattr(postgres, "_pool", lambda *args, **kwargs: pool)
    with pytest.raises(RuntimeError, match="save failed"):
        with postgres.connection(background=True) as database:
            database.execute("UPDATE cards SET due_date=%s WHERE id=%s", ("tomorrow", "card-1"))
            raise RuntimeError("save failed")
    assert pool.rolled_back and not pool.committed


def test_publication_profile_preserves_bound_and_rejects_reader(monkeypatch):
    pool = Pool()
    monkeypatch.setattr(postgres, "_pool", lambda *args, **kwargs: pool)
    with postgres.connection(background=True, queue_refresh_publication=True):
        pass
    assert pool.driver.calls[0][1] == ("500ms", "25ms")
    with pytest.raises(ValueError, match="background writer"):
        with postgres.connection(read_only=True, background=True, queue_refresh_publication=True):
            pytest.fail("Invalid profile must be rejected before yielding")
