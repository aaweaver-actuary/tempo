"""Pure fakes exercise owner preparation/fencing; not PostgreSQL lock proof."""
from copy import deepcopy
from types import SimpleNamespace

from tempo_evidence_math.position_learning import Hold
from tempo_legacy_database.adapters.authored_repertoires import position_study_source as owner
from test_product_snapshot import repertoire_facts


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
        self.calls = []
        self.integrity = dict(integrity_status="clean", scan_status="idle", scan_generation="scan:2",
            checked_at="2026-10-26T12:00:00Z", scan_completed_sources=1, scan_total_sources=1,
            scan_error=None, graph_task_generation=3, graph_task_state="complete", scan_task_id="scan",
            scan_task_generation=2, scan_task_state="complete", payload_json='{"repertoire_id":"rep","graph_generation":3}')
    def execute_native(self, sql, params):
        self.calls.append(sql)
        if "pg_try_advisory" in sql: return Result(row=(not self.busy,))
        if "FROM repertoire_lines WHERE" in sql:
            return Result(rows=[{k: row[k] for k in owner._LINE} for row in self.facts["lines"]])
        return Result(row=self.facts["repertoire"] | self.integrity)


def setup(monkeypatch):
    facts = repertoire_facts()
    db = Database(facts)
    monkeypatch.setattr(owner, "read_repertoire", lambda database, rep: deepcopy(database.facts))
    return db


def test_owner_prepares_and_fences_original_provenance_and_authored_content(monkeypatch):
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, "rep")
    assert not isinstance(prepared, Hold)
    assert prepared.routes and prepared.authored_token.original_source_digest == prepared.snapshot.membership.digest
    assert owner.fence_position_source(db, prepared) is None
    assert any("FOR SHARE" in q and "SKIP LOCKED" in q for q in db.calls)
    assert all("opening_graph_steps" not in q for q in db.calls)
    assert prepared.authored_token.as_dict()["authored_digest"] == prepared.authored_token.authored_digest


def test_physical_change_is_not_authored_change_but_source_route_change_holds(monkeypatch):
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, "rep")
    db.facts["cards"][0]["revision"] += 1
    assert owner.fence_position_source(db, prepared) is None
    db.facts["lines"][0]["moves_json"] = '["d2d4"]'
    assert owner.fence_position_source(db, prepared).reason == "position_source_authored_content_changed"


def test_source_and_integrity_changes_and_graph_busy_hold(monkeypatch):
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, "rep")
    db.busy = True
    assert owner.fence_position_source(db, prepared).reason == "position_source_graph_busy"
    db.busy = False
    db.facts["repertoire"]["scope_source_revision"] += 1
    assert owner.fence_position_source(db, prepared).reason == "position_source_authority_changed"
    db.facts["repertoire"]["scope_source_revision"] -= 1
    db.integrity["scan_status"] = "running"
    assert owner.fence_position_source(db, prepared).reason == "position_source_fence_unavailable"
    assert isinstance(owner.prepare_position_source(db, "rep"), Hold)


def test_untrusted_construction_and_nonwriter_are_rejected(monkeypatch):
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, "rep")
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
    result = owner.prepare_position_source(db, "rep")
    assert isinstance(result, Hold) and "own_response_conflict" in result.detail
