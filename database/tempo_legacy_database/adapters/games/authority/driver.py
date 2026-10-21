"""Existing command-owned writer guard and borrowed tuple-row DBAPI adapter."""

from psycopg.rows import tuple_row


class BorrowedTupleConnection:
    def __init__(self, database):
        if database.read_only:
            raise RuntimeError("Legacy evidence requires the command writer transaction")
        self.raw = database.raw
        self.info = self.raw.info

    def execute(self, statement, parameters=()):
        return self.raw.cursor(row_factory=tuple_row).execute(statement, parameters)


def require_writer(database):
    if database.read_only:
        raise RuntimeError("Legacy evidence requires the command writer transaction")
