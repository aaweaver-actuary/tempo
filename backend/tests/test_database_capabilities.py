"""Selected legacy storage capabilities preserve both owner shapes."""
import sqlite3
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from tempo_legacy_database import connections
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, TempoRow
from tempo_legacy_database.capabilities import BorrowedTransaction, ReadConnection


def test_row_shapes_match_sqlite_positional_and_mapping_access():
    db = sqlite3.connect(':memory:')
    try:
        db.row_factory = sqlite3.Row
        sqlite_row = db.execute('SELECT 7 AS first, ? AS second', ('value',)).fetchone()
        postgres_row = TempoRow(('first', 'second'), (7, 'value'))
        for row in (sqlite_row, postgres_row):
            assert row[0] == row['first'] == 7
            assert tuple(row) == (7, 'value')
            assert dict(row) == {'first': 7, 'second': 'value'}
            assert len(row) == 2
    finally:
        db.close()


def test_read_scope_is_query_only_and_scope_owns_lifetime(tmp_path, monkeypatch):
    path = tmp_path / 'owned.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE proof (value INTEGER)')
        db.execute('INSERT INTO proof VALUES (1)')
    monkeypatch.setattr(connections, 'DB_PATH', path)
    monkeypatch.setattr(connections.postgres_store, 'configured', lambda: False)
    with connections.read_connection() as db:
        assert db.execute('SELECT value FROM proof').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            db.execute('INSERT INTO proof VALUES (2)')
    assert 'commit' not in ReadConnection.__dict__ and 'close' not in ReadConnection.__dict__
    assert 'commit' not in BorrowedTransaction.__dict__ and 'close' not in BorrowedTransaction.__dict__


def test_postgres_borrowed_transaction_fact_accepts_only_intrans():
    info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
    db = PostgresConnection(SimpleNamespace(info=info))
    for status in TransactionStatus:
        info.transaction_status = status
        assert db.in_transaction is (status == TransactionStatus.INTRANS)


def test_retained_publication_refuses_unowned_transaction_before_work():
    from app.repertoire.changes.retained import publish_validated_change
    from app.repertoire.changes.ports import ChangeError
    with pytest.raises(ChangeError, match='borrowed_transaction_required'):
        publish_validated_change(SimpleNamespace(in_transaction=False), None)


def test_sqlite_reports_its_existing_transaction_state():
    db = sqlite3.connect(':memory:')
    try:
        assert not db.in_transaction
        db.execute('BEGIN')
        assert db.in_transaction
        db.rollback()
        assert not db.in_transaction
    finally:
        db.close()
