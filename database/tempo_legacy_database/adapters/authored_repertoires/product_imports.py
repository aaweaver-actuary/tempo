"""Raw retained-import receipt/card reads and locks on the borrowed database.

Application approval policy and command execution remain with Repertoire.
No connection, transaction, commit, rollback or receipt mutation is opened here.
"""
from __future__ import annotations

from psycopg.pq import TransactionStatus

from ..commands.receipts import storage as receipts


class RetainedImportStorage:
    def __init__(self, database):
        self.database = database

    def read_operation(self, operation_id: str) -> tuple[object, ...] | None:
        row = receipts.operation_status(self.database, operation_id)
        return tuple(row) if row is not None else None

    def card_rows(self, segment_ids: list[str]) -> tuple[dict[str, object], ...]:
        if not segment_ids:
            return ()
        rows = self.database.execute_native(
            "SELECT id,revision,start_fen,moves_json,trained_color,archived,superseded_by "
            "FROM cards WHERE id=ANY(%s::text[]) ORDER BY id",
            (segment_ids,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def in_transaction(self) -> bool:
        return self.database.raw.info.transaction_status == TransactionStatus.INTRANS

    def lock_original(self, operation_id: str) -> None:
        receipts.lock_operation(
            receipts.receipt_writer(self.database), operation_id
        )
