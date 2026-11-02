"""Contract fakes; PostgreSQL lock/transaction behavior needs native proof."""
from types import SimpleNamespace

import pytest
from tempo_evidence_math.position_learning import Hold, PositionDefinition, PositionKey
from tempo_legacy_database.adapters.study import position_owner_epoch as owner


class Result:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row


class Database:
    def __init__(self):
        self.read_only = False
        self.raw = SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS")))
        self.owner = {"mode": "active", "epoch": "e1", "source_digest": "digest"}
        self.origin = {"admission_repertoire_id": "rep"}
        self.busy = False
        self.calls = []
    def execute_native(self, sql, args):
        self.calls.append((sql, args))
        if "pg_try_advisory" in sql: return Result((not self.busy,))
        if "study_position_owners" in sql: return Result(self.owner)
        if "queue_attempt_origins" in sql: return Result(self.origin)
        raise AssertionError(sql)


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(owner, "fence_position_source", lambda db, prepared: None)
    definition = PositionDefinition(PositionKey("rep", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"), "e2e4", "v1")
    return SimpleNamespace(authored_token=SimpleNamespace(repertoire_id="rep", original_source_digest="digest"),
        routes=(SimpleNamespace(decisions=(SimpleNamespace(definition=definition),)),))


def fence(db, source, **kwargs):
    return owner.fence_position_owner_in_writer(db, source, definition=kwargs.get("definition", source.routes[0].decisions[0].definition), epoch=kwargs.get("epoch", "e1"),
        queue_entry_id=kwargs.get("queue_entry_id", 1), card_id="card", revision=3)


@pytest.mark.parametrize("mode,disposition", [("inactive", "legacy"), ("shadow", "legacy"),
    ("staged", "hold"), ("paused", "hold"), ("active", "position")])
def test_modes_and_disabled_default(mode, disposition):
    db = Database(); db.owner["mode"] = mode
    assert owner.owner_mode(db, "rep", "digest").disposition == disposition
    db.owner = None
    assert owner.owner_mode(db, "rep", None).disposition == "legacy"


def test_source_epoch_and_transaction_hold():
    db = Database()
    assert owner.owner_mode(db, "rep", None).reason == "position_owner_identity_missing"
    assert owner.owner_mode(db, "rep", "old").reason == "position_owner_source_changed"
    db.busy = True
    assert owner.owner_mode(db, "rep", "digest").reason == "position_owner_busy"
    db.read_only = True
    assert owner.owner_mode(db, "rep", "digest").reason == "borrowed_owner_writer_transaction_required"


def test_new_issuance_requires_original_scope_and_epoch(source):
    db = Database()
    assert fence(db, source).disposition == "position"
    assert fence(db, source, epoch="old").reason == "position_owner_epoch_changed"
    for origin in (None, {"admission_repertoire_id": None}, {"admission_repertoire_id": "other"}):
        db.origin = origin
        assert fence(db, source).reason == "position_admission_unproven"
    assert "FOR SHARE SKIP LOCKED" in db.calls[-1][0]


@pytest.mark.parametrize("mode", ["inactive", "shadow", "staged", "paused"])
def test_fence_never_authorizes_a_nonactive_owner(source, mode):
    db = Database(); db.owner["mode"] = mode
    assert fence(db, source).disposition == "hold"


def test_source_fence_precedes_owner_and_propagates_hold(source, monkeypatch):
    db = Database()
    monkeypatch.setattr(owner, "fence_position_source", lambda db, prepared: Hold("source_changed"))
    assert fence(db, source).reason == "source_changed"
    assert db.calls == []


def test_unpublished_response_is_held(source):
    db = Database()
    definition = source.routes[0].decisions[0].definition
    other = PositionDefinition(definition.key, "d2d4", definition.policy_version)
    assert fence(db, source, definition=other).reason == "position_definition_unproven"


def test_native_owner_lock_and_epoch_visibility():
    """Opt-in draft: task-owned DB only; no server or container is started."""
    import os
    from pathlib import Path
    from uuid import uuid4
    dsn = os.environ.get("TEMPO_POSITION_TEST_DSN")
    if not dsn:
        pytest.skip("requires task-owned disposable TEMPO_POSITION_TEST_DSN")
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    class Borrowed:
        read_only = False
        def __init__(self, raw): self.raw = raw
        def execute_native(self, query, args):
            # Real owner reads use mapping rows; advisory scalar uses tuples.
            return self.raw.cursor(row_factory=None if "pg_try_advisory" in query else dict_row).execute(query, args)

    with psycopg.connect(dsn, autocommit=True) as admin:
        name = admin.execute("SELECT current_database()").fetchone()[0]
        assert name.startswith("tempo_position_test_"), "refusing non-task-owned database"
        schema = "position_f3_" + uuid4().hex
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            admin.execute("CREATE TABLE tempo_schema_migrations (version integer PRIMARY KEY)")
            migration = Path(__file__).resolve().parents[2] / "tempo_legacy_database/ddl/migrations/057_position_study.sql"
            admin.execute(migration.read_text())
            admin.execute("INSERT INTO study_position_epochs VALUES ('rep','e1','digest',now()), ('rep','e2','new',now())")
            admin.execute("INSERT INTO study_position_owners VALUES ('rep','e1','active',now())")
            with psycopg.connect(dsn) as first, psycopg.connect(dsn) as second:
                first.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                second.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                assert owner.owner_mode(Borrowed(first), "rep", "digest").epoch == "e1"
                # F4 must use this exclusive key before writing. It cannot
                # change the row while a reader's transaction owns its share.
                assert not second.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                                          (owner.owner_lock_key("rep"),)).fetchone()[0]
                second.rollback()
                first.commit()
                second.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                assert second.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                                      (owner.owner_lock_key("rep"),)).fetchone()[0]
                second.execute("UPDATE study_position_owners SET epoch='e2' WHERE repertoire_id='rep'")
                first.execute("BEGIN")
                assert owner.owner_mode(Borrowed(first), "rep", "digest").reason == "position_owner_busy"
                first.rollback()
                second.commit()
                first.execute("BEGIN")
                assert owner.owner_mode(Borrowed(first), "rep", "digest").reason == "position_owner_source_changed"
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
