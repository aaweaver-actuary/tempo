"""Read-only visible queue checksum retained for the snapshot reader.

Adaptive-priority reclamation and compaction CLI execution is retired.
The existing snapshot tool imports queue_order_checksum; this module opens no
connection and performs no maintenance or snapshot action.
"""

import hashlib
import json
import sqlite3


def _encoded_row(row: sqlite3.Row) -> bytes:
    """Encode a SQLite row as a JSON byte string."""
    values = [value.hex() if isinstance(value, bytes) else value for value in row]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def queue_order_checksum(database: sqlite3.Connection) -> str:
    """Hash the exact visible queue sequence before and after maintenance."""
    digest = hashlib.sha256()
    for queue_entry in database.execute(
        """SELECT queue_date,position,cycle,card_id,status FROM daily_queue
           ORDER BY queue_date,position,id"""
    ):
        digest.update(_encoded_row(queue_entry))
        digest.update(b"\n")
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit("Adaptive-priority cleanup CLI is retired; no database was opened or changed.")
