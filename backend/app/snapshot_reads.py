"""Raw SQL evidence for a prepared command; no decoding or hashing under locks."""
from dataclasses import dataclass
from .postgres_store import TempoRow


@dataclass(frozen=True)
class SnapshotRead:
    query: str
    parameters: tuple
    rows: tuple[str, ...]


def snapshot_rows(database, query, parameters):
    # RecordingReader orders JSON strings in Python. Locale collation can ignore
    # punctuation and reorder multi-digit IDs, falsely rejecting unchanged rows.
    statement = (f'SELECT evidence FROM (SELECT row_to_json(captured)::text evidence '
                 f'FROM ({query}) captured) serialized ORDER BY evidence COLLATE "C"')
    return tuple(row[0] for row in database.execute_native(statement, parameters).fetchall())


class RecordingCursor:
    def __init__(self, cursor, query, parameters, reads):
        self.cursor, self.query, self.parameters, self.reads = cursor, query, parameters, reads

    def _record(self, rows):
        self.reads.append(SnapshotRead(self.query, tuple(self.parameters),
                                       tuple(sorted(row['__transition_evidence'] for row in rows))))
        return [TempoRow(tuple(row.keys())[:-1], tuple(row)[:-1]) for row in rows]

    def fetchone(self):
        row = self.cursor.fetchone()
        result = self._record([row] if row else [])
        return result[0] if result else None

    def fetchall(self):
        return self._record(self.cursor.fetchall())


class RecordingReader:
    def __init__(self, database, reads):
        self.database, self.reads = database, reads

    def execute_native(self, query, parameters=()):
        # Capture evidence from the same returned rows without a second query.
        cursor = self.database.execute_native(
            f'SELECT captured.*,row_to_json(captured)::text __transition_evidence FROM ({query}) captured', parameters)
        return RecordingCursor(cursor, query, parameters, self.reads)
