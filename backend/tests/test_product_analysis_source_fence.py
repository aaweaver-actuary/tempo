"""Composition contract only; fake owner effects do not prove native locking."""
from dataclasses import replace

import pytest

from app.analysis.jobs.ports import AnalysisFence
from app.product_composition import analysis_publications as composition
from app.repertoire import project_repertoire
from tempo_legacy_database.adapters.analysis.publications import (
    AnalysisPublicationError, PostgresAnalysisPublications,
)
from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import SnapshotReservationUnavailable
from test_product_analysis_publications import Owner, prepared
from test_product_snapshot import repertoire_facts


def source_fence(snapshot):
    member = snapshot.membership
    return prepared().fence.model_copy(update=dict(repertoire_id=member.scope_repertoire_ids[0],
        repertoire_revision=member.revision, source_revision=member.source_revision,
        snapshot_digest=member.digest))


def prepared_for(snapshot):
    value = prepared()
    fence = source_fence(snapshot)
    def bound(ranking):
        return ranking.model_copy(update={"based_on": ranking.based_on.model_copy(update={
            "repertoire_revisions": ((fence.repertoire_id, fence.repertoire_revision),)})})
    ranking = replace(value.ranking, source_revision=fence.source_revision, snapshot_digest=fence.snapshot_digest,
        gaps=bound(value.ranking.gaps), introductions=bound(value.ranking.introductions))
    return replace(value, fence=fence, ranking=ranking)


@pytest.mark.parametrize("reserve", [False, True])
def test_source_fence_reserves_exact_borrowed_database_before_analysis_facts(monkeypatch, reserve):
    snapshot = project_repertoire(repertoire_facts())
    database = object(); events = []
    def source(supplied, repertoire_id):
        assert supplied is database and repertoire_id == "rep"
        events.append("source")
        return snapshot
    def generations(supplied, factual):
        assert supplied is database and factual is snapshot and events == ["source"]
        events.append("analysis")
        return source_fence(factual)
    monkeypatch.setattr(composition, "reserve_current_snapshot", source)
    result = composition.CurrentRepertoireAnalysisFence(generations)(database, "rep", reserve=reserve)
    assert isinstance(result, AnalysisFence) and result == source_fence(snapshot)
    assert events == ["source", "analysis"]


@pytest.mark.parametrize("field,value", [("repertoire_id", "other"), ("repertoire_revision", 1),
    ("source_revision", "stale-source"), ("snapshot_digest", "f" * 64)])
def test_source_fence_refuses_analysis_facts_bound_to_another_source(monkeypatch, field, value):
    snapshot = project_repertoire(repertoire_facts())
    monkeypatch.setattr(composition, "reserve_current_snapshot", lambda *_: snapshot)
    current = composition.CurrentRepertoireAnalysisFence(
        lambda *_: source_fence(snapshot).model_copy(update={field: value}))
    with pytest.raises(AnalysisPublicationError, match="analysis_current_fence_source_mismatch"):
        current(object(), "rep", reserve=True)


def test_source_fence_cannot_satisfy_a_different_requested_scope(monkeypatch):
    snapshot = project_repertoire(repertoire_facts())
    monkeypatch.setattr(composition, "reserve_current_snapshot", lambda *_: snapshot)
    with pytest.raises(AnalysisPublicationError, match="analysis_current_fence_source_mismatch"):
        composition.CurrentRepertoireAnalysisFence(lambda *_: source_fence(snapshot))(object(), "other", reserve=True)


def test_source_fence_yields_before_analysis_facts_when_source_is_busy(monkeypatch):
    def busy(*_):
        raise SnapshotReservationUnavailable("snapshot_source_busy")
    monkeypatch.setattr(composition, "reserve_current_snapshot", busy)
    current = composition.CurrentRepertoireAnalysisFence(lambda *_: pytest.fail("Analysis acquired before source"))
    with pytest.raises(SnapshotReservationUnavailable, match="snapshot_source_busy"):
        current(object(), "rep", reserve=True)


@pytest.mark.parametrize("fault", ["missing", "failed"])
def test_source_fence_never_substitutes_default_analysis_generations(monkeypatch, fault):
    snapshot = project_repertoire(repertoire_facts())
    monkeypatch.setattr(composition, "reserve_current_snapshot", lambda *_: snapshot)
    def unavailable(*_):
        if fault == "failed":
            raise RuntimeError("generation_owner_unavailable")
        return None
    with pytest.raises((RuntimeError, AnalysisPublicationError)):
        composition.CurrentRepertoireAnalysisFence(unavailable)(object(), "rep", reserve=True)


def test_publication_source_change_preserves_last_result_and_releases_same_transaction(monkeypatch):
    facts = repertoire_facts()
    snapshots = [project_repertoire(facts)]
    original = prepared_for(snapshots[0])
    owner = Owner(original.fence)
    def source(database, repertoire_id):
        assert database is owner.active and database.open and repertoire_id == "rep"
        owner.reserved = database
        return snapshots[0]
    def generations(database, snapshot):
        assert database is owner.reserved
        return source_fence(snapshot)
    monkeypatch.setattr(composition, "reserve_current_snapshot", source)
    adapter = PostgresAnalysisPublications(owner.transaction,
        current_fence=composition.CurrentRepertoireAnalysisFence(generations))
    assert adapter.publish_if_current(original) and adapter.current_result("rep") == original
    facts["cards"][0]["introduced_at"] = "2026-10-21T00:00:00Z"
    snapshots[0] = project_repertoire(facts)
    assert snapshots[0].membership.revision == original.fence.repertoire_revision
    assert not adapter.publish_if_current(original)
    assert adapter.current_result("rep") is None and adapter.latest_result("rep") == original
    assert owner.active is owner.reserved is None and owner.writes == 1
