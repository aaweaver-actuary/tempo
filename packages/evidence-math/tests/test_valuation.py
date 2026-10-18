from dataclasses import replace
from datetime import timedelta
import json
import pytest
from tempo_evidence_math import *
from .support import fixture, AS_OF, ITALIAN, RUY


def values(result):
    return {candidate.card_id: candidate.metadata()['valuation']['value'] for candidate in result.candidates}


def test_valuation_real_reach_readiness_kernel_multi_decision_hand_calculation():
    result = evaluate_opening_card_valuation(fixture())
    assert values(result) == pytest.approx({'multi': .168, 'single': .189})
    assert result.baseline.value == 0
    assert result.context.policy_id == 'italian'
    configuration = json.loads(result.context.scorer_configuration_json)
    assert configuration['complete_preparedness']
    assert configuration['baseline_bundle'] == 'marginal-expected-correct-decisions@'+VALUATION_VERSION
    assert len(configuration['precomputed_score_bundles']) == 1
    assert configuration['precomputed_score_bundles'][0]['name'] != 'production-order'
    document = valuation_snapshot_document(result)
    assert load_snapshot(document)[0] == result.candidates


@pytest.mark.parametrize('policy,moves,other,other_moves', [('italian',ITALIAN,'ruy',RUY),('ruy',RUY,'italian',ITALIAN)])
def test_valuation_italian_and_ruy_add_remove_duplicate_isolation(policy,moves,other,other_moves):
    selected = fixture(policy, moves)
    baseline = valuation_snapshot_document(evaluate_opening_card_valuation(selected))
    catalog = {policy: selected}
    for count in (1,2,4):
        catalog[other] = [fixture(other,other_moves)]*count
        assert valuation_snapshot_document(evaluate_opening_card_valuation(catalog[policy])) == baseline
    del catalog[other]
    assert valuation_snapshot_document(evaluate_opening_card_valuation(catalog[policy])) == baseline


def test_valuation_policy_version_bridge_retains_both_verified_decision_ids():
    result = evaluate_opening_card_valuation(fixture(policy_version=2))
    provenance = result.candidates[0].metadata()['valuation']['readiness']
    assert all(identity != entry['estimate']['decision_id'] for identity,entry in provenance.items())
    assert values(result) == pytest.approx({'multi': .168, 'single': .189})


def test_valuation_foreign_identity_fails_before_scoring():
    current = fixture()
    changed = replace(current.readiness[0], policy_id='foreign')
    with pytest.raises(ValueError, match='Foreign'):
        evaluate_opening_card_valuation(replace(current,readiness=(changed,*current.readiness[1:])))


def test_valuation_snapshot_detaches_mutable_readiness_collections():
    current=fixture(); mutable=list(current.readiness)
    detached=replace(current,readiness=mutable)
    mutable.clear()
    assert values(evaluate_opening_card_valuation(detached)) == pytest.approx({'multi':.168,'single':.189})


def test_valuation_effects_are_independent_non_regressive_and_missing_remains_unknown():
    current=fixture()
    candidate=replace(current.candidates[0],effect=None,effect_reference=None)
    result=evaluate_opening_card_valuation(replace(current,candidates=(candidate,current.candidates[1])))
    assert values(result)['multi'] is None
    assert values(result)['single'] == pytest.approx(.189)


def test_valuation_input_permutation_is_byte_identical():
    current=fixture()
    permuted=replace(current,routes=tuple(reversed(current.routes)),readiness=tuple(reversed(current.readiness)),candidates=tuple(reversed(current.candidates)))
    assert valuation_snapshot_document(evaluate_opening_card_valuation(current)) == valuation_snapshot_document(evaluate_opening_card_valuation(permuted))


def test_graph_snapshot_rejects_route_beyond_horizon():
    graph=fixture().graph
    with pytest.raises(ValueError,match='horizon'):
        replace(graph,horizon_plies=1)
    with pytest.raises(ValueError,match='publication'):
        replace(graph,publications=())


def test_projection_time_and_presentation_are_fenced():
    binding=fixture().readiness[0]; current=binding.snapshot
    with pytest.raises(ValueError,match='as_of'):
        decision_readiness(current,AS_OF+timedelta(days=1))
    with pytest.raises(ValueError,match='presentation'):
        decision_readiness(replace(current,memory=replace(current.memory,presentation=replace(current.presentation,card_id='foreign'))),AS_OF)


def test_mathematical_input_roundtrip_preserves_result_and_explicit_versions():
    original=fixture()
    document=valuation_input_document(original)
    decoded=load_valuation_snapshot(document)
    assert valuation_snapshot_document(evaluate_opening_card_valuation(decoded)) == valuation_snapshot_document(evaluate_opening_card_valuation(original))
    document['graph']['publications'][0]['graph_digest']='foreign'
    with pytest.raises(ValueError):load_valuation_snapshot(document)
