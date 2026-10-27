"""Cheap source-owner reservation contract; no database/provider/engine process."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.repertoire import project_repertoire
from tempo_legacy_database.adapters.authored_repertoires import product_snapshot as storage
from test_product_snapshot import repertoire_facts


class LockBusy(RuntimeError):
    sqlstate = "55P03"


class Cursor:
    def __init__(self, values): self.values = values
    def fetchone(self): return self.values[0] if self.values else None
    def fetchall(self): return self.values


class OwnerPort:
    def __init__(self):
        self.raw = SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS")))
        self.read_only = False
        self.events = []
        self.calls = []
        self.busy = None
        self.writer = True
        self.isolation = "read committed"
        self.overflow = None
    def execute_native(self, statement, parameters=()):
        stage = statement.split("*/", 1)[0].rsplit(":", 1)[-1]
        self.events.append(stage); self.calls.append((stage, statement, parameters))
        if stage == self.busy: raise LockBusy()
        values = {
            "writer": [(self.writer,)], "isolation": [(self.isolation,)], "scope-cards": [("physical",)], "graph-task": [("graph-task",)],
            "graph-reservation": [(True,)], "repertoire": [("rep",)],
            "identity": [("fixture",)], "publication": [("rep",)], "lines": [("route",)],
            "card-reservation": [(True,)], "cards": [("physical",)],
            "memberships": [("rep", "physical")], "graphs": [("route", 0)],
            "integrity-blocks": [], "deletions": [],
        }[stage]
        if stage == self.overflow: values = [("overflow",)] * (storage.MAX_SOURCE_ROWS + 1)
        return Cursor(values)
    def commit(self): raise AssertionError("caller commit authority")
    def rollback(self): raise AssertionError("caller rollback authority")


def setup_port(monkeypatch, *, after=None):
    port = OwnerPort(); before = repertoire_facts(); calls = []
    def read(database, repertoire_id):
        assert database is port and repertoire_id == "rep"
        port.events.append("read"); calls.append(True)
        return deepcopy(after if after is not None and len(calls) > 1 else before)
    monkeypatch.setattr(storage, "read_repertoire", read)
    return port, before


def test_reservation_returns_same_public_snapshot_after_fresh_full_fact_read(monkeypatch):
    port, before = setup_port(monkeypatch)
    result = storage.reserve_current_snapshot(port, "rep")
    expected = project_repertoire(before)
    assert result.membership.digest == expected.membership.digest
    assert result.membership.source_revision == expected.membership.source_revision
    assert result.membership.revision == expected.membership.revision
    assert result.decision_presentations == expected.decision_presentations
    assert port.events == ["writer", "isolation", "read", "graph-task", "graph-reservation", "graph-reservation",
        "repertoire", "identity", "publication", "lines", "scope-cards", "card-reservation", "cards",
        "memberships", "graphs", "integrity-blocks", "deletions", "read"]
    assert port.calls[3][2] == ("tempo:prefix-transition:reservations",)
    assert port.calls[4][2] == ("tempo:opening-graph:rep",)
    assert next(item for item in port.calls if item[0] == "card-reservation")[2] == ("tempo:card-edit:physical",)


def test_reservation_prepares_projection_before_source_locks(monkeypatch):
    port, _ = setup_port(monkeypatch)
    import app.repertoire.snapshot as projection
    original = projection.project_repertoire
    def project(facts):
        assert port.events == ["writer", "isolation", "read"]
        return original(facts)
    monkeypatch.setattr(projection, "project_repertoire", project)
    storage.reserve_current_snapshot(port, "rep")


@pytest.mark.parametrize("stage", ["graph-task", "repertoire", "cards", "memberships", "integrity-blocks", "deletions"])
def test_reservation_yields_on_busy_source_fact_without_own_commit_or_rollback(monkeypatch, stage):
    port, _ = setup_port(monkeypatch); port.busy = stage
    with pytest.raises(storage.SnapshotReservationUnavailable, match="snapshot_source_busy"):
        storage.reserve_current_snapshot(port, "rep")
    assert port.events.count("read") == 1


@pytest.mark.parametrize("attribute,value", [("introduced_at", "2026-10-21"), ("archived", 1),
    ("integrity_blocked", True), ("deleted", True), ("revision", 2)])
def test_reservation_refuses_changed_card_facts_even_with_same_source_counter(monkeypatch, attribute, value):
    after = repertoire_facts(); after["cards"][0][attribute] = value
    port, before = setup_port(monkeypatch, after=after)
    assert before["repertoire"] == after["repertoire"]
    with pytest.raises(storage.SnapshotReservationUnavailable, match="snapshot_source_changed"):
        storage.reserve_current_snapshot(port, "rep")
    assert port.events.count("read") == 2


@pytest.mark.parametrize("mode", ["readonly_port", "readonly_transaction", "idle"])
def test_reservation_requires_an_existing_writer_transaction(monkeypatch, mode):
    port, _ = setup_port(monkeypatch)
    if mode == "readonly_port": port.read_only = True
    elif mode == "readonly_transaction": port.writer = False
    else: port.raw.info.transaction_status.name = "IDLE"
    with pytest.raises(storage.SnapshotReservationUnavailable, match="borrowed_writer_transaction_required"):
        storage.reserve_current_snapshot(port, "rep")
    assert "read" not in port.events


def test_reservation_refuses_auxiliary_overflow_instead_of_partial_fence(monkeypatch):
    port, _ = setup_port(monkeypatch); port.overflow = "integrity-blocks"
    with pytest.raises(storage.SnapshotReservationUnavailable, match="snapshot_reservation_exceeds_bound"):
        storage.reserve_current_snapshot(port, "rep")
    assert port.events.count("read") == 1


def test_reservation_yields_on_existing_advisory_owner_contention(monkeypatch):
    port, _ = setup_port(monkeypatch)
    original = port.execute_native
    def execute(statement, parameters=()):
        result = original(statement, parameters)
        return Cursor([(False,)]) if "pg_try_advisory" in statement else result
    port.execute_native = execute
    with pytest.raises(storage.SnapshotReservationUnavailable, match="snapshot_source_busy"):
        storage.reserve_current_snapshot(port, "rep")
    assert "repertoire" not in port.events


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable"])
def test_reservation_refuses_a_historical_transaction_snapshot(monkeypatch, isolation):
    port, _ = setup_port(monkeypatch); port.isolation = isolation
    with pytest.raises(storage.SnapshotReservationUnavailable, match="borrowed_read_committed_transaction_required"):
        storage.reserve_current_snapshot(port, "rep")
    assert "read" not in port.events


def test_reservation_also_holds_existing_nonopening_candidate_cards(monkeypatch):
    port, _ = setup_port(monkeypatch)
    original = port.execute_native
    def execute(statement, parameters=()):
        result = original(statement, parameters)
        if "snapshot-reserve:scope-cards" in statement or "snapshot-reserve:cards" in statement:
            return Cursor([("not-opening",), ("physical",)])
        return result
    port.execute_native = execute
    storage.reserve_current_snapshot(port, "rep")
    assert [call[2] for call in port.calls if call[0] == "card-reservation"] == [
        ("tempo:card-edit:not-opening",), ("tempo:card-edit:physical",)]
    assert next(call for call in port.calls if call[0] == "cards")[2] == (["not-opening", "physical"],)


def test_reservation_refuses_candidate_card_overflow(monkeypatch):
    port, _ = setup_port(monkeypatch); port.overflow = "scope-cards"
    with pytest.raises(storage.SnapshotReservationUnavailable, match="snapshot_reservation_exceeds_bound"):
        storage.reserve_current_snapshot(port, "rep")
    assert "card-reservation" not in port.events
