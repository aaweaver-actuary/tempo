"""Check with mypy --warn-unused-ignores and the owner source on MYPYPATH."""
import sqlite3

from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.capabilities import BorrowedTransaction, ReadConnection


def sqlite_connection_matches(database: sqlite3.Connection) -> None:
    reader: ReadConnection = database
    borrowed: BorrowedTransaction = database


def postgres_connection_matches(database: PostgresConnection) -> None:
    reader: ReadConnection = database
    borrowed: BorrowedTransaction = database


def consumers_do_not_own_lifetime(reader: ReadConnection, borrowed: BorrowedTransaction) -> None:
    reader.commit()  # type: ignore[attr-defined]
    reader.close()  # type: ignore[attr-defined]
    borrowed.commit()  # type: ignore[attr-defined]
    borrowed.close()  # type: ignore[attr-defined]
