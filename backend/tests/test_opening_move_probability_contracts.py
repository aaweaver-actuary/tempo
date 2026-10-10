"""Named invariants for the opt-in source-independent contract."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.opening_move_probability_contracts import (
    CountEvidence, EvidenceFreshness, FusedMoveDistribution, FusionRequest,
    MethodDescriptor, MoveWeight, OpeningMoveEvidence, OpeningMoveEvidenceBundle,
    PositionMoveUniverse, ScoreEvidence, coverage_mass_summary, empirical_frequencies,
    fuse_move_evidence, move_universe, normalize_evidence_weights, serialize_contract,
)

EXAMPLES = json.loads((Path(__file__).resolve().parents[2] /
                      "tests/fixtures/opening-move-probability/examples.json").read_text())


@pytest.mark.parametrize("name", ["explorer", "explorer_other_cohort", "maia_dense", "maia_sparse"])
def test_opening_probability_shared_source_fixtures_round_trip(name):
    evidence = OpeningMoveEvidence.model_validate(EXAMPLES[name])
    assert json.loads(serialize_contract(evidence)) == EXAMPLES[name]
    assert OpeningMoveEvidence.model_validate_json(serialize_contract(evidence)) == evidence


@pytest.mark.parametrize("value", [-.1, float("nan"), float("inf"), float("-inf"), True, "0.4", 1.01])
def test_opening_probability_rejects_nonfinite_negative_and_non_numeric_estimates(value):
    payload = deepcopy(EXAMPLES["maia_dense"])
    payload["distribution"]["moves"][0]["probability"] = value
    with pytest.raises(ValueError):
        OpeningMoveEvidence.model_validate(payload)


def test_opening_probability_mass_tolerance_and_sparse_residual_are_explicit():
    payload = deepcopy(EXAMPLES["maia_dense"])
    payload["distribution"]["moves"][0]["probability"] += 5e-10
    OpeningMoveEvidence.model_validate(payload)
    payload["distribution"]["moves"][0]["probability"] += 2e-9
    with pytest.raises(ValueError, match="mass"):
        OpeningMoveEvidence.model_validate(payload)
    sparse = deepcopy(EXAMPLES["maia_sparse"])
    sparse["distribution"]["unknown_mass"] = 0
    with pytest.raises(ValueError):
        OpeningMoveEvidence.model_validate(sparse)
    payload = deepcopy(EXAMPLES["maia_dense"])
    payload["distribution"]["moves"][0]["probability"] = None
    payload["distribution"]["moves"][1]["probability"] += .4
    with pytest.raises(ValueError, match="Unassigned"):
        OpeningMoveEvidence.model_validate(payload)


def test_opening_probability_missing_counts_differ_from_observed_zero_and_predictions():
    evidence = OpeningMoveEvidence.model_validate(EXAMPLES["explorer"])
    frequencies = empirical_frequencies(evidence.raw_evidence)
    assert frequencies == {"e8d7": .7, "e8d8": 0}
    assert "e8e7" not in frequencies
    assert all(move.probability is None for move in evidence.distribution.moves)
    assert evidence.distribution.unknown_mass == 1
    assert OpeningMoveEvidence.model_validate(EXAMPLES["maia_dense"]).distribution.moves[-1].probability == 0
    forged = deepcopy(EXAMPLES["explorer"])
    forged["distribution"] = deepcopy(EXAMPLES["maia_dense"]["distribution"])
    with pytest.raises(ValueError, match="Raw-only"):
        OpeningMoveEvidence.model_validate(forged)


def test_opening_probability_empty_samples_and_zero_weights_do_not_fabricate_distribution():
    sample = CountEvidence(kind="counts", total_count=0, moves=[{"move_uci": "e8d7", "count": 0}])
    assert empirical_frequencies(sample) == {"e8d7": None}
    assert empirical_frequencies(CountEvidence(kind="counts", total_count=0, moves=[])) == {}
    evidence = OpeningMoveEvidence.model_validate(EXAMPLES["explorer"])
    method = MethodDescriptor(id="explicit-test-weights", version="1", parameters={})
    for weights in [(), (MoveWeight(move_uci="e8d7", value=0),)]:
        with pytest.raises(ValueError, match="empty or zero"):
            normalize_evidence_weights(evidence, weights, evidence_id="derived-fixture", unknown_mass=.2, method=method)


def test_opening_probability_raw_helpers_reject_duplicate_observations():
    for model, payload in [
        (CountEvidence, {"kind": "counts", "total_count": 10,
                         "moves": [{"move_uci": "e8d7", "count": 0}] * 2}),
        (ScoreEvidence, {"kind": "scores", "basis": "probability",
                         "moves": [{"move_uci": "e8d7", "value": 0}] * 2}),
    ]:
        with pytest.raises(ValueError, match="duplicate"):
            model.model_validate(payload)


def test_opening_probability_normalization_preserves_raw_evidence_and_provenance():
    evidence = OpeningMoveEvidence.model_validate(EXAMPLES["explorer"])
    original = serialize_contract(evidence)
    method = MethodDescriptor(id="explicit-test-weights", version="1", parameters={"reserve": .2})
    weights = (MoveWeight(move_uci="e8d7", value=7), MoveWeight(move_uci="e8d8", value=0))
    normalized = normalize_evidence_weights(evidence, weights, evidence_id="derived-fixture", unknown_mass=.2, method=method)
    assert normalized.distribution.moves[0].probability == pytest.approx(.8)
    assert normalized.distribution.moves[1].probability == 0
    assert normalized.distribution.moves[2].probability is None
    assert normalized.distribution.unknown_mass == .2
    assert normalized.raw_evidence == evidence.raw_evidence
    assert normalized.provenance == evidence.provenance
    assert normalized.cohort == evidence.cohort
    assert normalized.normalization == method
    assert normalized.evidence_id == "derived-fixture"
    assert normalized.derived_from == (evidence.evidence_id,)
    OpeningMoveEvidenceBundle(position=evidence.distribution.position, target_context={}, sources=(evidence, normalized))
    with pytest.raises(ValueError, match="distinct derived"):
        normalize_evidence_weights(evidence, weights, evidence_id=evidence.evidence_id, unknown_mass=.2, method=method)
    assert serialize_contract(evidence) == original
    with pytest.raises(ValueError):
        normalize_evidence_weights(evidence, weights, evidence_id="derived-fixture", unknown_mass=0, method=method)
    with pytest.raises(ValueError, match="duplicate"):
        normalize_evidence_weights(evidence, weights + weights, evidence_id="derived-fixture", unknown_mass=.2, method=method)
    with pytest.raises(ValueError, match="illegal"):
        normalize_evidence_weights(evidence, (MoveWeight(move_uci="e8e6", value=1),), evidence_id="derived-fixture", unknown_mass=.2, method=method)


@pytest.mark.parametrize("case", EXAMPLES["identity_cases"], ids=lambda case: case["name"])
def test_opening_probability_reuses_canonical_position_and_legal_uci_inventory(case):
    assert move_universe(case["fen"]).model_dump(mode="json") == case["position"]
    incomplete = deepcopy(case["position"])
    incomplete["legal_moves"].pop()
    with pytest.raises(ValueError, match="every legal move"):
        PositionMoveUniverse.model_validate(incomplete)
    duplicate = deepcopy(case["position"])
    duplicate["legal_moves"].append(duplicate["legal_moves"][0])
    with pytest.raises(ValueError):
        PositionMoveUniverse.model_validate(duplicate)


@pytest.mark.parametrize("move", ["0000", "garbage", "e8e6", "E8D7"])
def test_opening_probability_rejects_illegal_unknown_and_noncanonical_moves(move):
    payload = deepcopy(EXAMPLES["explorer"])
    payload["raw_evidence"]["moves"][0]["move_uci"] = move
    with pytest.raises(ValueError):
        OpeningMoveEvidence.model_validate(payload)


def test_opening_probability_rejects_terminal_positions_and_noncanonical_keys():
    with pytest.raises(ValueError, match="no legal"):
        move_universe("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")
    position = deepcopy(EXAMPLES["identity_cases"][1]["position"])
    position["fen_key"] = position["fen_key"].rsplit(" ", 1)[0] + " e3"
    with pytest.raises(ValueError, match="canonical"):
        PositionMoveUniverse.model_validate(position)


def test_opening_probability_sources_and_cohorts_coexist_without_overwriting():
    bundle = OpeningMoveEvidenceBundle.model_validate(EXAMPLES["bundle"])
    assert [source.evidence_id for source in bundle.sources] == ["explorer-1600", "explorer-1800", "maia-dense"]
    assert [source.source_id for source in bundle.sources].count("explorer-like") == 2
    duplicate = deepcopy(EXAMPLES["bundle"])
    duplicate["sources"].append(duplicate["sources"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        OpeningMoveEvidenceBundle.model_validate(duplicate)
    different_position = deepcopy(EXAMPLES["bundle"])
    different_position["position"] = move_universe("4k3/8/8/8/8/8/8/4K3 w - -").model_dump()
    with pytest.raises(ValueError, match="positions"):
        OpeningMoveEvidenceBundle.model_validate(different_position)


def test_opening_probability_serialization_orders_sets_but_preserves_context_sequences():
    payload = deepcopy(EXAMPLES["bundle"])
    payload["sources"].reverse()
    payload["position"]["legal_moves"].reverse()
    for source in payload["sources"]:
        source["raw_evidence"]["moves"].reverse()
        source["distribution"]["moves"].reverse()
        source["distribution"]["position"]["legal_moves"].reverse()
    assert serialize_contract(OpeningMoveEvidenceBundle.model_validate(payload)) == serialize_contract(
        OpeningMoveEvidenceBundle.model_validate(EXAMPLES["bundle"]))
    payload["target_context"]["profile"]["effective_speed_mixture"].reverse()
    assert serialize_contract(OpeningMoveEvidenceBundle.model_validate(payload)) != serialize_contract(
        OpeningMoveEvidenceBundle.model_validate(EXAMPLES["bundle"]))


def test_opening_probability_serialization_keeps_opaque_context_and_orders_provenance():
    payload = deepcopy(EXAMPLES["bundle"])
    payload["target_context"]["provenance"] = [{"sequence": 2}, {"sequence": 1}]
    record = deepcopy(payload["sources"][0]["provenance"][0])
    record["record_id"] = "zzz-input"
    payload["sources"][0]["provenance"].insert(0, record)
    bundle = OpeningMoveEvidenceBundle.model_validate(payload)
    output = json.loads(serialize_contract(bundle))
    assert output["target_context"]["provenance"] == [{"sequence": 2}, {"sequence": 1}]
    assert bundle.sources[0].provenance[0].record_id != "zzz-input"


def test_opening_probability_coverage_retains_uncovered_and_unknown_mass():
    distribution = OpeningMoveEvidence.model_validate(EXAMPLES["maia_sparse"]).distribution
    summary = coverage_mass_summary(distribution, {"e8d7"})
    assert summary.model_dump() == {"known_covered_mass": .45, "known_uncovered_mass": .35, "unknown_mass": .2}
    with pytest.raises(ValueError, match="illegal"):
        coverage_mass_summary(distribution, {"e8e6"})


def test_opening_probability_fusion_fences_inputs_and_preserves_contributing_lineage():
    request = FusionRequest.model_validate(EXAMPLES["fusion_request"])
    original = serialize_contract(request)
    output = deepcopy(EXAMPLES["fused"])
    output["provenance"] = []  # The seam carries contributing source lineage.
    fused = fuse_move_evidence(request, lambda _: FusedMoveDistribution.model_validate(output))
    assert fused.provenance == request.evidence.sources[-1].provenance
    assert fused.distribution == request.evidence.sources[-1].distribution
    assert serialize_contract(request) == original
    def inspecting_policy(policy_input):
        policy_input.evidence.sources[0].cohort["policy_mutation"] = True
        return FusedMoveDistribution.model_validate(output)
    fuse_move_evidence(request, inspecting_policy)
    assert serialize_contract(request) == original
    unknown_source = deepcopy(output)
    unknown_source["contributions"][0]["evidence_id"] = "not-in-request"
    with pytest.raises(ValueError, match="Unknown source"):
        fuse_move_evidence(request, lambda _: FusedMoveDistribution.model_validate(unknown_source))
    changed_context = deepcopy(output)
    changed_context["target_context"] = {}
    with pytest.raises(ValueError, match="match its request"):
        fuse_move_evidence(request, lambda _: FusedMoveDistribution.model_validate(changed_context))
    unavailable = fuse_move_evidence(request, lambda _: FusedMoveDistribution.model_validate(EXAMPLES["unavailable"]))
    assert unavailable.distribution is None and unavailable.status == "unavailable"


def test_opening_probability_freshness_is_explicit_and_resolves_microsecond_deadlines():
    EvidenceFreshness(as_of="2026-10-10T12:00:00.000001Z", valid_until="2026-10-10T12:00:00.000002Z", state="fresh")
    EvidenceFreshness(as_of="2026-10-10T12:00:00.000002Z", valid_until="2026-10-10T12:00:00.000002Z", state="stale")
    EvidenceFreshness(as_of="2026-10-10T12:00:00Z", valid_until=None, state="unknown")
    with pytest.raises(ValueError):
        EvidenceFreshness(as_of="2026-10-10T12:00:00Z", valid_until=None, state="fresh")


def test_opening_probability_rejects_extra_fields_versions_and_inexact_counts():
    for field, value in [("contract_version", 2), ("contract_version", True), ("unexpected", 1)]:
        payload = deepcopy(EXAMPLES["explorer"])
        payload[field] = value
        with pytest.raises(ValueError):
            OpeningMoveEvidence.model_validate(payload)
    for count in [True, 1.5, "7", 2**53]:
        payload = deepcopy(EXAMPLES["explorer"])
        payload["raw_evidence"]["moves"][0]["count"] = count
        with pytest.raises(ValueError):
            OpeningMoveEvidence.model_validate(payload)
