"""Pure publication codec/semantic checks; no native atomicity claim."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import sys

import chess
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
for path in ("database", "packages/evidence-math/src", "contracts/analysis-jobs/src"):
    sys.path.insert(0, str(ROOT / path))

from tempo_evidence_math import ProbabilityEvidence as ProbabilityBounds, move_universe, unknown_distribution
from app.analysis.coverage.ranking import CandidateDetail, CoverageRanking, Diagnostic, RouteSupport, rank_candidates
from app.analysis.jobs.ports import AnalysisFence, PreparedAnalysis
from app.contracts.product_v02.analysis import PopulationSelection, ProbabilityEvidence, RankingCandidate, RankingInput
from app.contracts.product_v02.repertoire import DecisionPresentation
from tempo_legacy_database.adapters.analysis import publications as storage
from tempo_legacy_database.adapters.analysis.publications import (
    AnalysisPublicationError, PostgresAnalysisPublications, decode_publication, encode_publication,
)


def prepared() -> PreparedAnalysis:
    """Synthetic public values, including nested bounds/provenance and tuples."""
    fence = AnalysisFence(repertoire_id="rep", repertoire_revision=1, source_revision="source:1",
        snapshot_digest="a" * 64, settings_revision=1, settings_digest="b" * 64,
        personal_revision=1, evidence_revision=1)
    position = move_universe(chess.STARTING_FEN)
    evidence = ProbabilityEvidence(evidence_id="evidence:1", position=position,
        provider="synthetic-explorer", model=None, basis="population",
        population=PopulationSelection(rating_min=1600, rating_max=1799, time_controls=("rapid",)),
        personal_account=None, observed_at="2026-10-26T00:00:00Z",
        distribution=unknown_distribution(position), counts=None, unavailable_reason=None)
    presentation = DecisionPresentation(decision_id="decision", repertoire_id="rep",
        position_key=position.fen_key, selected_response_uci="e2e4", presentation_id="existing-card",
        presentation_revision=1, repertoire_revision=1, introduction="unintroduced", introduced_at=None,
        available=True)
    reach = ProbabilityBounds.bounded(.02, .2, "synthetic-unknown-mass")
    support = RouteSupport("route", 0, (), ProbabilityBounds.exact(1), None, reach, (evidence.evidence_id,))
    detail = CandidateDetail("decision", "introduction", "rep", position.fen_key, "e2e4",
        chess.STARTING_FEN, True, reach, reach, (support,), (presentation,), (evidence,), (),
        (("game:1", "2026-10-25T00:00:00Z", "rapid", 0),), "c" * 64)
    ranked = rank_candidates(RankingInput(repertoire_revisions=(("rep", 1),),
        candidates=(RankingCandidate(decision_id="decision", repertoire_id="rep", reach_probability=.02,
            evidence_support=None, uncertainty=.18, personal_encounters=1),),
        evidence_ids=(evidence.evidence_id,)))
    gaps = rank_candidates(RankingInput(repertoire_revisions=(("rep", 1),), candidates=(), evidence_ids=()))
    ranking = CoverageRanking(gaps, ranked, (detail,), (Diagnostic("route", 0, "synthetic", .8),),
        "synthetic-policy", fence.source_revision, fence.snapshot_digest, "d" * 64, "e" * 64)
    return PreparedAnalysis(fence, "ready", ranking)


def sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Cursor:
    def __init__(self, database: Database, row: tuple[object, ...] | None = None):
        self.database, self.row = database, row

    def fetchone(self) -> tuple[object, ...] | None:
        assert self.database.open
        return self.row


class Database:
    """Typed fake driver: classifies operations, never freezes SQL literals."""
    def __init__(self, owner: Owner):
        self.owner, self.open = owner, True
        self.rows, self.writes = dict(owner.rows), 0

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> Cursor:
        assert self.open and self.owner.active is self
        operation = statement.split(None, 1)[0]
        if operation == "SELECT":
            return Cursor(self, self.rows.get(parameters[0]))
        assert operation == "INSERT"
        assert self.owner.reserved is self
        repertoire_id, version, payload, digest = parameters
        self.rows[repertoire_id] = (version, payload, digest)
        self.writes += 1
        return Cursor(self)


class Owner:
    """Synthetic reservation and transaction owner, without real source locks."""
    def __init__(self, fence: AnalysisFence):
        self.fence, self.rows = fence, {}
        self.active = self.reserved = None
        self.commits = self.releases = self.writes = self.fence_reads = 0
        self.fail_commit = False

    @contextmanager
    def transaction(self) -> Iterator[Database]:
        assert self.active is None
        database = self.active = Database(self)
        try:
            yield database
            if self.fail_commit:
                raise RuntimeError("synthetic commit failure")
            self.rows = database.rows
            self.writes += database.writes
            self.commits += 1
        finally:
            database.open = False
            self.active = self.reserved = None
            self.releases += 1

    def current(self, database: storage.PublicationDatabase, repertoire_id: str, *, reserve: bool) -> AnalysisFence:
        assert database is self.active and database.open
        self.fence_reads += 1
        if reserve:
            self.reserved = database
        return self.fence

    def adapter(self) -> PostgresAnalysisPublications:
        return PostgresAnalysisPublications(self.transaction, current_fence=self.current)


def test_publication_codec_roundtrips_actual_public_values_and_nested_immutability():
    original = prepared()
    payload = encode_publication(original)
    result = decode_publication(payload, sha256(payload))
    assert result == original and result is not original
    assert type(result) is PreparedAnalysis and type(result.ranking) is CoverageRanking
    assert result.ranking.settings_digest != result.fence.settings_digest
    assert result.ranking.details[0].population_evidence[0].distribution.unknown_mass == 1
    assert isinstance(result.ranking.details[0].supports, tuple)
    assert isinstance(result.ranking.details[0].supports[0].prefix, tuple)
    assert result.ranking.details[0].supports[0].position_reach == ProbabilityBounds.exact(1)
    with pytest.raises(FrozenInstanceError):
        result.ranking.policy_version = "changed"
    with pytest.raises(ValidationError):
        result.fence.personal_revision = 2
    with pytest.raises(TypeError):
        result.ranking.details[0].personal_sources[0][0] = "changed"
    assert encode_publication(result) == payload


def test_publication_codec_refuses_nonfinite_and_exact_utf8_budget_overflow(monkeypatch):
    value = prepared()
    invalid = replace(value, ranking=replace(value.ranking, diagnostics=(Diagnostic("route", 0, "bad", float("nan")),)))
    with pytest.raises(AnalysisPublicationError, match="analysis_publication_invalid"):
        encode_publication(invalid)
    value = replace(value, ranking=replace(value.ranking, policy_version="synthetic-\u00e9"))
    payload = encode_publication(value)
    monkeypatch.setattr(storage, "MAX_PUBLICATION_BYTES", len(payload.encode("utf-8")) - 1)
    with pytest.raises(AnalysisPublicationError, match="analysis_publication_too_large"):
        encode_publication(value)
    with pytest.raises(AnalysisPublicationError, match="analysis_publication_too_large"):
        decode_publication(payload, sha256(payload))


@pytest.mark.parametrize("corruption", ["bad-hash", "invalid-json", "extra-field", "duplicate-key", "nonfinite"])
def test_publication_codec_refuses_corrupt_or_ambiguous_payloads(corruption):
    payload = encode_publication(prepared())
    if corruption == "bad-hash":
        digest = "0" * 64
    else:
        if corruption == "invalid-json":
            payload = "{"
        elif corruption == "extra-field":
            body = json.loads(payload)
            body["ignored-by-dataclass"] = True
            payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        elif corruption == "duplicate-key":
            payload = '{"status":"ready",' + payload[1:]
        else:
            payload = payload.replace('"unknown_mass":0.8', '"unknown_mass":NaN')
        digest = sha256(payload)
    with pytest.raises(AnalysisPublicationError):
        decode_publication(payload, digest)


def test_publication_uses_actual_owner_facts_and_identical_replay_is_inert():
    value = prepared()
    owner = Owner(value.fence)
    adapter = owner.adapter()
    assert adapter.current("rep") == value.fence
    assert owner.rows == {} and owner.active is None
    assert adapter.publish_if_current(value)
    assert owner.writes == 1 and owner.active is owner.reserved is None
    assert adapter.publish_if_current(value)
    assert owner.writes == 1 and owner.releases == 3
    assert adapter.current_result("rep") == value


@pytest.mark.parametrize("field,new_value", [
    ("repertoire_revision", 2), ("source_revision", "source:2"), ("snapshot_digest", "f" * 64),
    ("settings_revision", 2), ("settings_digest", "f" * 64),
    ("personal_revision", 2), ("evidence_revision", 2),
])
def test_publication_compares_each_owner_generation_and_preserves_historical_fence(field, new_value):
    value = prepared()
    owner = Owner(value.fence)
    adapter = owner.adapter()
    assert adapter.publish_if_current(value)
    saved = dict(owner.rows)
    owner.fence = value.fence.model_copy(update={field: new_value})
    assert adapter.current("rep") == owner.fence
    assert not adapter.publish_if_current(value)
    assert owner.rows == saved and owner.writes == 1
    assert adapter.current_result("rep") is None
    assert adapter.latest_result("rep") == value
    assert adapter.latest_result("rep").fence == value.fence
    assert owner.active is owner.reserved is None


@pytest.mark.parametrize("status", ["setup_required", "unavailable", "cancelled"])
def test_nonready_publication_does_not_open_transaction_or_replace_last_result(status):
    value = prepared()
    owner = Owner(value.fence)
    adapter = owner.adapter()
    assert adapter.publish_if_current(value)
    previous, releases = dict(owner.rows), owner.releases
    assert not adapter.publish_if_current(PreparedAnalysis(value.fence, status, reason="synthetic"))
    assert owner.rows == previous and owner.releases == releases


def test_mismatched_result_context_and_wrong_owner_scope_fail_without_writes():
    value = prepared()
    owner = Owner(value.fence)
    adapter = owner.adapter()
    mismatched = replace(value, ranking=replace(value.ranking, source_revision="other-source"))
    with pytest.raises(AnalysisPublicationError, match="analysis_publication_context_mismatch"):
        adapter.publish_if_current(mismatched)
    assert owner.releases == 0
    owner.fence = value.fence.model_copy(update={"repertoire_id": "other-repertoire"})
    with pytest.raises(AnalysisPublicationError, match="analysis_current_fence_scope_mismatch"):
        adapter.publish_if_current(value)
    assert owner.rows == {} and owner.active is owner.reserved is None


def test_read_decoding_occurs_after_release_and_corruption_is_explicit(monkeypatch):
    value = prepared()
    owner = Owner(value.fence)
    adapter = owner.adapter()
    assert adapter.publish_if_current(value)
    real_decode = storage.decode_publication

    def closed_decode(payload, digest):
        assert owner.active is owner.reserved is None
        return real_decode(payload, digest)

    monkeypatch.setattr(storage, "decode_publication", closed_decode)
    assert adapter.current_result("rep") == value
    assert adapter.latest_result("rep") == value
    version, payload, digest = owner.rows["rep"]
    owner.rows["rep"] = (version, payload, "0" * 64)
    with pytest.raises(AnalysisPublicationError, match="analysis_publication_integrity_mismatch"):
        adapter.current_result("rep")
    owner.rows["rep"] = (version + 1, payload, digest)
    with pytest.raises(AnalysisPublicationError, match="analysis_publication_format_unsupported"):
        adapter.latest_result("rep")
    assert owner.active is owner.reserved is None


def test_commit_failure_is_not_success_and_releases_reservations():
    value = prepared()
    owner = Owner(value.fence)
    adapter = owner.adapter()
    assert adapter.publish_if_current(value)
    previous = dict(owner.rows)
    owner.fence = value.fence.model_copy(update={"evidence_revision": 2})
    owner.fail_commit = True
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        adapter.publish_if_current(replace(value, fence=owner.fence))
    assert owner.rows == previous and owner.writes == 1
    assert owner.active is owner.reserved is None


def test_missing_publication_is_explicit_without_inventing_current_cache():
    owner = Owner(prepared().fence)
    adapter = owner.adapter()
    assert adapter.current_result("rep") is None
    reads = owner.fence_reads
    assert adapter.latest_result("rep") is None
    assert owner.fence_reads == reads
