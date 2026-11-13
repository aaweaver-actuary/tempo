"""Pure fakes exercise owner preparation/fencing; not PostgreSQL lock proof."""

from app.repertoire import position_study_source as _source_preparation
from copy import deepcopy
from types import SimpleNamespace

from tempo_evidence_math.position_learning import Hold
from tempo_storage.authored_repertoires import position_study_source as owner
from database.tests.position_study.source_facts import repertoire_facts


class Result:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class Database:
    def __init__(self, facts):
        self.facts = facts
        self.read_only = False
        self.raw = SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS")))
        self.busy = False
        self.line_busy = False
        self.calls = []
        self.integrity = dict(integrity_status="clean", scan_status="idle", scan_generation="scan:2",
            checked_at="2026-10-26T12:00:00Z", scan_completed_sources=1, scan_total_sources=1,
            scan_error=None, graph_task_generation=3, graph_task_state="complete", scan_task_id="scan",
            scan_task_generation=2, scan_task_state="complete", payload_json='{"repertoire_id":"rep","graph_generation":3}')
    def execute_native(self, sql, params):
        self.calls.append(sql)
        if "pg_try_advisory" in sql: return Result(row=(not self.busy,))
        if "FROM repertoire_lines WHERE" in sql:
            assert "FOR SHARE SKIP LOCKED" in sql
            return Result(rows=[] if self.line_busy else [{k: row[k] for k in owner._LINE} for row in self.facts["lines"]])
        return Result(row=self.facts["repertoire"] | self.integrity)


def setup(monkeypatch):
    facts = repertoire_facts()
    db = Database(facts)
    monkeypatch.setattr(_source_preparation, "read_repertoire", lambda database, rep: deepcopy(database.facts))
    return db


def test_owner_prepares_and_fences_original_provenance_and_authored_content(monkeypatch):
    db = setup(monkeypatch)
    prepared = _source_preparation.prepare_position_source(db, "rep")
    assert not isinstance(prepared, Hold)
    assert prepared.routes and prepared.authored_token.original_source_digest == prepared.snapshot.membership.digest
    assert owner.fence_position_source(db, prepared) is None
    assert any("FOR SHARE" in q and "SKIP LOCKED" in q for q in db.calls)
    assert all("opening_graph_steps" not in q for q in db.calls)
    assert prepared.authored_token.as_dict()["authored_digest"] == prepared.authored_token.authored_digest


def test_physical_change_is_not_authored_change_but_source_route_change_holds(monkeypatch):
    db = setup(monkeypatch)
    prepared = _source_preparation.prepare_position_source(db, "rep")
    db.facts["cards"][0]["revision"] += 1
    assert owner.fence_position_source(db, prepared) is None
    db.facts["lines"][0]["moves_json"] = '["d2d4"]'
    assert owner.fence_position_source(db, prepared).reason == "position_source_authored_content_changed"


def test_source_and_integrity_changes_and_graph_busy_hold(monkeypatch):
    db = setup(monkeypatch)
    prepared = _source_preparation.prepare_position_source(db, "rep")
    db.busy = True
    assert owner.fence_position_source(db, prepared).reason == "position_source_graph_busy"
    db.busy = False
    db.facts["repertoire"]["scope_source_revision"] += 1
    assert owner.fence_position_source(db, prepared).reason == "position_source_authority_changed"
    db.facts["repertoire"]["scope_source_revision"] -= 1
    db.integrity["scan_status"] = "running"
    assert owner.fence_position_source(db, prepared).reason == "position_source_fence_unavailable"
    assert isinstance(_source_preparation.prepare_position_source(db, "rep"), Hold)


def test_untrusted_construction_and_nonwriter_are_rejected(monkeypatch):
    db = setup(monkeypatch)
    prepared = _source_preparation.prepare_position_source(db, "rep")
    try:
        owner.PreparedPositionSource(None, prepared.snapshot, prepared.routes, prepared.authored_token)
        assert False
    except ValueError:
        pass
    db.read_only = True
    assert owner.fence_position_source(db, prepared).reason == "borrowed_owner_writer_transaction_required"


def test_conflicting_authored_answers_are_not_certified(monkeypatch):
    db = setup(monkeypatch)
    line = deepcopy(db.facts["lines"][0]); line.update(id="other", moves_json='["d2d4"]')
    db.facts["lines"].append(line)
    result = _source_preparation.prepare_position_source(db, "rep")
    assert isinstance(result, Hold) and "own_response_conflict" in result.detail


def test_busy_authored_line_cannot_be_certified_as_complete(monkeypatch):
    db = setup(monkeypatch)
    prepared = _source_preparation.prepare_position_source(db, "rep")
    db.line_busy = True
    assert owner.fence_position_source(db, prepared).reason == "position_source_authored_content_changed"


def test_native_id_only_edit_and_busy_row_cannot_cross_fence():
    """Two real sessions: ID-only edit waits, and a prelocked row causes hold."""
    import json
    import os
    from pathlib import Path
    import uuid

    import chess
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    import pytest
    from test_product_snapshot_postgres import DDL
    from tempo_storage.postgres import tempo_row_factory

    dsn = os.environ.get("TEMPO_POSITION_SOURCE_TEST_DSN")
    if not dsn:
        pytest.skip("explicit task-owned PostgreSQL DSN required")
    params = conninfo_to_dict(dsn)
    if params.get("host") not in {"localhost", "127.0.0.1"} or not params.get("dbname", "").startswith("tempo_position_"):
        raise RuntimeError("explicit local task-owned tempo_position_* database required")
    schema = "position_source_" + uuid.uuid4().hex
    migration = (Path(__file__).parents[2] / "tempo_storage/ddl/migrations/035_canonical_publication_freshness.sql").read_text()
    function = "CREATE OR REPLACE FUNCTION advance_canonical_prefix_source()" + migration.split("CREATE OR REPLACE FUNCTION advance_canonical_prefix_source()", 1)[1].split("END $$;", 1)[0] + "END $$;"
    with psycopg.connect(dsn, autocommit=True) as setup_db:
        setup_db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        opts = f"-c search_path={schema} -c statement_timeout=5000"
        with psycopg.connect(dsn, autocommit=True, options=opts) as setup_db:
            setup_db.execute(DDL)
            setup_db.execute("""CREATE TABLE repertoire_integrity_state(repertoire_id text PRIMARY KEY,
                status text,scan_status text,scan_generation text,checked_at text,
                scan_completed_sources bigint,scan_total_sources bigint,scan_error text);
                CREATE TABLE background_tasks(id text,kind text,deduplication_key text,
                generation bigint,state text,payload_json text)""")
            setup_db.execute(function)
            setup_db.execute("CREATE TRIGGER canonical_line_source AFTER INSERT OR UPDATE OR DELETE ON repertoire_lines FOR EACH ROW EXECUTE FUNCTION advance_canonical_prefix_source()")
            setup_db.execute("INSERT INTO legacy_learning_identity VALUES(1,'fixture')")
            setup_db.execute("INSERT INTO repertoires VALUES('rep','Synthetic',7,0,'[]')")
            setup_db.execute("INSERT INTO opening_graph_publications VALUES('rep',3,'ready')")
            setup_db.execute("INSERT INTO repertoire_lines VALUES('route','rep','Route','white',%s,%s)",
                             (chess.STARTING_FEN, json.dumps(["e2e4"])))
            setup_db.execute("INSERT INTO repertoire_integrity_state VALUES('rep','clean','idle','scan:2','2026-10-26T12:00:00Z',1,1,NULL)")
            setup_db.execute("INSERT INTO background_tasks VALUES('graph','opening_graph_rebuild','rep',3,'complete','{}'),('scan','integrity_scan','rep',2,'complete',%s)",
                             (json.dumps(dict(repertoire_id="rep", graph_generation=3)),))

        class Port:
            read_only = False
            def __init__(self, raw): self.raw = raw
            def execute_native(self, statement, parameters): return self.raw.execute(statement, parameters)

        with psycopg.connect(dsn, options=opts, row_factory=tempo_row_factory) as raw:
            port = Port(raw)
            prepared = _source_preparation.prepare_position_source(port, "rep")
            assert not isinstance(prepared, Hold)
            raw.commit()
            # With a line already locked by another writer, SKIP LOCKED cannot
            # turn a partial authored relation into a successful fence.
            with psycopg.connect(dsn, options=opts) as other:
                other.execute("SELECT id FROM repertoire_lines WHERE id='route' FOR UPDATE")
                raw.execute("SELECT 1")
                assert owner.fence_position_source(port, prepared).reason == "position_source_authored_content_changed"
                raw.rollback()
                other.rollback()
            raw.execute("SELECT 1")
            assert owner.fence_position_source(port, prepared) is None
            with psycopg.connect(dsn, options=opts) as other:
                other.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    other.execute("UPDATE repertoire_lines SET id='renamed' WHERE id='route'")
                other.rollback()
            raw.rollback()
            with psycopg.connect(dsn, options=opts) as other:
                other.execute("UPDATE repertoire_lines SET id='renamed' WHERE id='route'")
            raw.execute("SELECT 1")
            assert owner.fence_position_source(port, prepared).reason == "position_source_authored_content_changed"
            raw.rollback()
    finally:
        with psycopg.connect(dsn, autocommit=True) as cleanup:
            cleanup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_foreign_structural_and_forged_owner_values_never_authorize_writer(monkeypatch):
    db = setup(monkeypatch)
    prepared = _source_preparation.prepare_position_source(db, 'rep')
    foreign = SimpleNamespace(snapshot=prepared.snapshot, routes=prepared.routes,
                              authored_token=prepared.authored_token)
    assert owner.fence_position_source(db, foreign).reason == 'position_source_owner_preparation_required'
    forged = object.__new__(owner.PreparedPositionSource)
    for name in ('snapshot', 'routes', 'authored_token'):
        object.__setattr__(forged, name, getattr(prepared, name))
    assert owner.fence_position_source(db, forged).reason == 'position_source_owner_preparation_required'
    object.__setattr__(forged, '_seal', object())
    assert owner.fence_position_source(db, forged).reason == 'position_source_owner_preparation_required'
    assert owner.fence_position_source(db, prepared) is None


def test_owner_publication_facts_are_detached_and_preserve_full_provenance(monkeypatch):
    db = setup(monkeypatch)
    prepared = _source_preparation.prepare_position_source_with_facts(db, 'rep')
    assert not isinstance(prepared, Hold)
    token = prepared.prepared.authored_token
    original = prepared.publication_facts()
    assert set(original) == {'repertoire', 'lines', 'cards', 'memberships', 'graphs'}
    assert token.original_source_digest == prepared.prepared.snapshot.membership.digest
    original['lines'][0]['moves_json'] = '[]'
    original['cards'][0]['revision'] += 1
    detached = prepared.publication_facts()
    assert detached['lines'][0]['moves_json'] != '[]'
    assert detached['cards'][0]['revision'] == db.facts['cards'][0]['revision']
    assert owner.fence_position_source(db, prepared.prepared) is None
