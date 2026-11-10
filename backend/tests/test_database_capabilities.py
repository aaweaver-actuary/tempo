"""Selected legacy storage capabilities preserve both owner shapes."""

import sqlite3
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from tempo_legacy_database.adapters.postgres_store import PostgresConnection, TempoRow
from tempo_legacy_database.capabilities import BorrowedTransaction, ReadConnection


def test_row_shapes_match_sqlite_positional_and_mapping_access():
    db = sqlite3.connect(":memory:")
    try:
        db.row_factory = sqlite3.Row
        sqlite_row = db.execute("SELECT 7 AS first, ? AS second", ("value",)).fetchone()
        postgres_row = TempoRow(("first", "second"), (7, "value"))
        for row in (sqlite_row, postgres_row):
            assert row[0] == row["first"] == 7
            assert tuple(row) == (7, "value")
            assert dict(row) == {"first": 7, "second": "value"}
            assert len(row) == 2
    finally:
        db.close()


def test_postgres_borrowed_transaction_fact_accepts_only_intrans():
    info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
    db = PostgresConnection(SimpleNamespace(info=info))
    for status in TransactionStatus:
        info.transaction_status = status
        assert db.in_transaction is (status == TransactionStatus.INTRANS)


def test_retained_publication_refuses_unowned_transaction_before_work():
    from app.repertoire.changes.retained import publish_validated_change
    from app.repertoire.changes.ports import ChangeError

    with pytest.raises(ChangeError, match="borrowed_transaction_required"):
        publish_validated_change(SimpleNamespace(in_transaction=False), None)


def test_borrowed_capabilities_do_not_expose_transaction_lifetime():
    for capability in (ReadConnection, BorrowedTransaction):
        assert "commit" not in capability.__dict__
        assert "close" not in capability.__dict__
