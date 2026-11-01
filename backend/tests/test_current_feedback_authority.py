"""Current qualified feedback proves exact authority without legacy capture."""
from copy import deepcopy

import pytest

from app.product_composition import provider_priority
from tempo_legacy_database.adapters.study.current_feedback_authority import CurrentQualifiedOwnerDecisions
from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentDecisionUnavailable
from test_product_delivery_qualification import native_handoff, owned_postgres, setup_case, qualify, command
from test_game_bound_repertoire_preparation import authored, candidate
from test_product_snapshot import repertoire_facts
from app.repertoire import PreparedFirstBoundaryRepertoire


def prepared_case():
    case = setup_case()
    facts = repertoire_facts()
    publication = PreparedFirstBoundaryRepertoire(authored(facts),game=case.game).with_bindings(candidate(facts))
    case.snapshot = publication.snapshot
    case.command = command(case.game,case.snapshot,3)
    case.source = case.source.model_copy(update={'effects':(case.command,)})
    delivery = qualify(case)
    actual = next(row for row in facts['cards'] if row['id'] == delivery.qualification.selection.decision.presentation_id)
    return case,publication,delivery,dict(card=actual,header=facts['repertoire'])


def test_current_qualified_constructor_requires_no_legacy_export_and_reuses_complete_scope():
    from app.games.feedback.first_boundary import select_first_boundary
    case,publication,delivery,target = prepared_case()
    decisions = CurrentQualifiedOwnerDecisions(game=case.game,target_facts=target)
    def selector(game,**arguments):
        return select_first_boundary(game,**arguments,prepared_repertoires=(publication,))
    decisions.prepare_qualified(case.command,delivery.qualification,qualified_publications=(publication,),
        qualification_fence=lambda *args:None,game=case.game,select_first_boundary=selector)
    assert not hasattr(decisions,'authority') and not hasattr(decisions,'proof')
    assert decisions.introduction_revision(case.command.decision.repertoire_id) == target['header']['scope_source_revision']+1
    assert decisions._qualified_captures == (publication.authored_facts()['repertoire'],)
    assert decisions._prepared_encounter is not None


@pytest.mark.parametrize('fault',['source','occurrence','interior_definition'])
def test_current_qualified_authority_rejects_changed_source_or_exact_executable(fault):
    case,publication,delivery,target = prepared_case()
    decisions = CurrentQualifiedOwnerDecisions(game=case.game,target_facts=target)
    if fault == 'source':
        changed = case.game.model_copy(update={'source_digest':'b'*64})
        with pytest.raises(CurrentDecisionUnavailable,match='canonical_source_unproved'):
            decisions._validate_qualified_game(changed,case.command)
    elif fault == 'occurrence':
        changed = case.command.model_copy(update={'occurred_at':'2020-01-01T00:00:00Z'})
        with pytest.raises(CurrentDecisionUnavailable,match='canonical_source_unproved'):
            decisions._validate_qualified_game(case.game,changed)
    else:
        target = deepcopy(target)
        target['card']['moves_json']='["d2d4"]'
        decisions = CurrentQualifiedOwnerDecisions(game=case.game,target_facts=target)
        with pytest.raises(CurrentDecisionUnavailable,match='decision_not_in_executable'):
            decisions.prepare_encounter(case.command)


def test_native_current_feedback_accepts_with_broad_legacy_capture_unavailable(native_handoff,monkeypatch):
    from app import legacy_game_observation
    from app.services import legacy_game_capture
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case = native_handoff
    def forbidden(*args,**kwargs):
        pytest.fail('current qualified feedback invoked broad legacy capture/projection')
    monkeypatch.setattr(legacy_game_observation,'capture_game',forbidden)
    monkeypatch.setattr(legacy_game_capture,'capture_source_scope',forbidden)
    monkeypatch.setattr(provider_priority,'project_capture',forbidden)
    monkeypatch.setattr(provider_priority,'_owner_capture',forbidden)
    prepared = provider_priority.prepare_delivery_feedback(case.original,connections=case.connections,
        clock=case.clock,admitted=lambda:True,capture=forbidden)
    assert prepared.provider_feedback is not None,prepared.delivery.qualification_reason
    assert prepared.provider_feedback.target_capture is None and prepared.provider_feedback.target_snapshot is None
    with case.connections.writer() as database:
        result = provider_priority.apply_prepared_determined_feedback(database,prepared,clock=case.clock,
            current_runtime_id=prepared.mode_runtime_id)
        assert result.progressed,result.held_reason
        effect = PostgresGameFeedbackStore(database).get(case.original.command.key)
        assert effect is not None and effect.key == case.original.command.key
    assert case.store.delivery(case.original.operation_id).state == 'acknowledged'


@pytest.mark.parametrize('fault', ['ambiguous', 'missing_graph_route', 'wrong_ply', 'revision'])
def test_exact_target_requires_complete_equivalent_route_and_revision(fault):
    case, publication, delivery, target = prepared_case()
    decisions = CurrentQualifiedOwnerDecisions(game=case.game, target_facts=target)
    decisions._qualified_snapshots = (publication.snapshot,)
    selected = delivery.qualification.selection.decision
    current = case.command.model_copy(update={'decision': selected})
    mappings = [item for item in publication.snapshot.decision_presentations
                if item.decision_id == current.key.decision_id]
    assert mappings
    actual = dict(target['card'])
    graphs = [{'line_id': item.route_id} for item in mappings]
    assert decisions._mapping_for_target(current, actual, graphs) == selected
    if fault == 'ambiguous':
        altered = mappings[0].model_copy(update={'presentation_id': 'other-card'})
        decisions._qualified_snapshots = (publication.snapshot.model_copy(update={
            'decision_presentations': (*publication.snapshot.decision_presentations, altered)}),)
        expected = 'conflicting_decision_bindings'
    elif fault == 'missing_graph_route':
        graphs.append({'line_id': 'unrepresented-route'})
        expected = 'incomplete_decision_bindings'
    elif fault == 'wrong_ply':
        current = current.model_copy(update={'decision': selected.model_copy(update={'route_ply': selected.route_ply + 1})})
        expected = 'supplied_route_unproved'
    else:
        actual['revision'] += 1
        expected = 'card_revision_changed'
    with pytest.raises(CurrentDecisionUnavailable, match=expected):
        decisions._mapping_for_target(current, actual, graphs)
