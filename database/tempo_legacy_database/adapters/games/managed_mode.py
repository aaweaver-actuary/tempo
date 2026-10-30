"""One Games-owned managed-mode row; no Study table access or runtime migration.

Read snapshots are outside writers. The actual handoff fence borrows the existing
short writer and holds one shared row lock through its commit; disable/transition
updates that same row. No provider, chess preparation or second connection occurs
inside the fence.
"""
from __future__ import annotations

from contextlib import contextmanager
import socket

from app.games.feedback.interfaces.managed_mode import ManagedGamesModeIntent, ManagedModeAdmissionDenied

MODE_SCHEMA_SQL = """
CREATE TABLE product_feedback_managed_mode (
    workspace_id TEXT PRIMARY KEY,
    generation BIGINT NOT NULL CHECK(generation>0),
    mode TEXT NOT NULL CHECK(mode IN ('collect-only','feedback')),
    phase TEXT NOT NULL CHECK(phase IN ('inactive','transitioning','active')),
    payload JSONB NOT NULL
);
"""


def _intent(row, workspace):
    if row is None:
        return None
    intent = ManagedGamesModeIntent.model_validate_json(row[0])
    if (intent.workspace_id, intent.generation, intent.mode, intent.phase) != (workspace, row[1], row[2], row[3]):
        raise ManagedModeAdmissionDenied()
    return intent


def read_feedback_mode(connections, workspace: str) -> ManagedGamesModeIntent | None:
    """One bounded read-only snapshot; an absent row is not authorization."""
    with connections() as database:
        database.execute("SET TRANSACTION READ ONLY")
        row = database.execute("SELECT payload::text,generation,mode,phase FROM product_feedback_managed_mode WHERE workspace_id=%s",
                               (workspace,)).fetchone()
        return _intent(row, workspace)


def feedback_handoff_admitted(workspace: str, connections, *, runtime_id: str | None = None) -> bool:
    """Outside-writer hint only; acceptance must also take the real row fence."""
    try:
        intent = read_feedback_mode(connections, workspace)
        return intent is not None and intent.admits(runtime_id or socket.gethostname())
    except Exception:
        return False


def fence_feedback_handoff(database, workspace: str, *, runtime_id: str | None = None) -> ManagedGamesModeIntent:
    """Public Games capability called first by qualified NEW handoff composition.

    Mode row -> existing Games source/configuration fences -> Study card/effect
    locks. The caller owns the one bounded writer/commit. A disable which commits
    first denies this handoff; a handoff holding this lock commits before disable.
    Already accepted Study obligations do not consume this mode fence.
    """
    raw = getattr(database, "raw", database)
    if getattr(database, "read_only", False) or raw.info.transaction_status.name != "INTRANS":
        raise ManagedModeAdmissionDenied()
    execute = getattr(database, "execute_native", None) or database.execute
    row = execute("SELECT payload::text,generation,mode,phase FROM product_feedback_managed_mode WHERE workspace_id=%s FOR SHARE NOWAIT",
                  (workspace,)).fetchone()
    intent = _intent(row, workspace)
    if intent is None or not intent.admits(runtime_id or socket.gethostname()):
        raise ManagedModeAdmissionDenied()
    return intent


class ManagedGamesModeStore:
    def __init__(self, connections):
        if getattr(connections, "read_only", False):
            raise ValueError("managed_mode_writer_profile_required")
        self.connections = connections

    @contextmanager
    def _writer(self):
        with self.connections() as database:
            with database.transaction():
                database.execute("SET LOCAL lock_timeout = '100ms'")
                database.execute("SET LOCAL statement_timeout = '250ms'")
                yield database

    def record(self, intent: ManagedGamesModeIntent, *, expected_generation: int) -> ManagedGamesModeIntent:
        if intent.generation != expected_generation + 1:
            raise ValueError("managed_mode_generation_mismatch")
        with self._writer() as database:
            if expected_generation == 0:
                row = database.execute("""INSERT INTO product_feedback_managed_mode(workspace_id,generation,mode,phase,payload)
                    VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT(workspace_id) DO NOTHING RETURNING generation""",
                    (intent.workspace_id, intent.generation, intent.mode, intent.phase, intent.model_dump_json())).fetchone()
            else:
                row = database.execute("""UPDATE product_feedback_managed_mode SET generation=%s,mode=%s,phase=%s,payload=%s::jsonb
                    WHERE workspace_id=%s AND generation=%s RETURNING generation""",
                    (intent.generation, intent.mode, intent.phase, intent.model_dump_json(), intent.workspace_id, expected_generation)).fetchone()
            if row is None:
                raise ValueError("managed_mode_generation_changed")
        return intent
