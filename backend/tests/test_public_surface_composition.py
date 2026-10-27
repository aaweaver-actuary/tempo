"""Root surface and actual first repertoire caller routing; not full DAG proof."""
import importlib

from test_public_surface_infrastructure import cold_import


def test_composition_public_import_has_no_operational_startup():
    cold_import("app.product_composition.interfaces.analysis")


def test_composition_reuses_the_existing_typed_entry_points():
    public = importlib.import_module("app.product_composition.interfaces.analysis")
    implementation = importlib.import_module("app.product_composition.analysis")
    assert public.__all__ == ["CoverageRequest", "coverage_settings_digest", "prepare_coverage"]
    for name in public.__all__:
        assert getattr(public, name) is getattr(implementation, name)


def test_actual_reserved_snapshot_caller_uses_public_projection_before_source_locks(monkeypatch):
    from app.repertoire import interfaces
    from tempo_legacy_database.adapters.authored_repertoires import product_snapshot
    from test_product_snapshot_reservation import setup_port
    port, _ = setup_port(monkeypatch)
    original = interfaces.project_repertoire
    calls = []
    def project(facts):
        assert port.events == ["writer", "isolation", "read"]
        calls.append(True)
        return original(facts)
    monkeypatch.setattr(interfaces, "project_repertoire", project)
    result = product_snapshot.reserve_current_snapshot(port, "rep")
    assert result.membership.scope_repertoire_ids == ("rep",) and calls == [True]
    assert port.events.count("read") == 2


def test_actual_current_snapshot_caller_uses_public_projection_once(monkeypatch):
    from app.repertoire import interfaces
    from tempo_legacy_database.adapters.authored_repertoires import product_snapshot
    from test_product_snapshot import repertoire_facts
    facts = repertoire_facts(); marker = object(); calls = []
    monkeypatch.setattr(product_snapshot, "read_repertoire", lambda *_: facts)
    def project(supplied):
        assert supplied is facts
        calls.append(True)
        return marker
    monkeypatch.setattr(interfaces, "project_repertoire", project)
    assert product_snapshot.current_snapshot(object(), "rep") is marker and calls == [True]
