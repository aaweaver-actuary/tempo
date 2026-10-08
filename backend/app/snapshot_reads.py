"""SQL evidence for a prepared command; no Python decoding/hashing under locks."""
from dataclasses import dataclass
from .postgres_store import TempoRow


@dataclass(frozen=True)
class SnapshotRead:
    query: str
    parameters: tuple
    rows: tuple[str, ...]
    uses_sha256_evidence: bool = False


def snapshot_rows(database, query, parameters, *, uses_sha256_evidence=False):
    # RecordingReader orders JSON strings in Python. Locale collation can ignore
    # punctuation and reorder multi-digit IDs, falsely rejecting unchanged rows.
    expression = evidence_expression(uses_sha256_evidence)
    statement = (f'SELECT evidence FROM (SELECT {expression} evidence '
                 f'FROM ({query}) captured) serialized ORDER BY evidence COLLATE "C"')
    return tuple(row[0] for row in database.execute_native(statement, parameters).fetchall())


def evidence_expression(uses_sha256_evidence):
    raw_json = 'row_to_json(captured)::text'
    return f"encode(sha256(convert_to({raw_json},'UTF8')),'hex')" if uses_sha256_evidence else raw_json


class RecordingCursor:
    def __init__(self, database, query, parameters, reads):
        self.database, self.query, self.parameters, self.reads = database, query, parameters, reads

    def _record(self, rows, *, uses_sha256_evidence):
        self.reads.append(SnapshotRead(self.query, tuple(self.parameters),
                                       tuple(sorted(row['__transition_evidence'] for row in rows)), uses_sha256_evidence))
        return [TempoRow(tuple(row.keys())[:-1], tuple(row)[:-1]) for row in rows]

    def _execute(self, *, uses_sha256_evidence):
        expression = evidence_expression(uses_sha256_evidence)
        return self.database.execute_native(
            f'SELECT captured.*,{expression} __transition_evidence FROM ({self.query}) captured', self.parameters)

    def fetchone(self):
        row = self._execute(uses_sha256_evidence=False).fetchone()
        result = self._record([row] if row else [], uses_sha256_evidence=False)
        return result[0] if result else None

    def fetchall(self):
        # Bulk rows retain native PostgreSQL values for the unchanged planner.
        # A server-computed SHA256 binds their exact raw JSON without sending a
        # second potentially 4 MiB payload through the bounded read transaction.
        rows = self._execute(uses_sha256_evidence=True).fetchall()
        return self._record(rows, uses_sha256_evidence=True)


class RecordingReader:
    def __init__(self, database, reads):
        self.database, self.reads = database, reads

    def execute_native(self, query, parameters=()):
        # Select scalar/raw or bulk/digest evidence at fetch time, in one query.
        return RecordingCursor(self.database, query, parameters, self.reads)
