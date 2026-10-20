"""Synthetic complete source ports; no live capture or queue mutation."""
from copy import deepcopy
import json
import pytest
from tempo_learning_review import historical_exposure as policy
from tempo_learning_remediation import RemediationError
from app.historical_exposure_contracts import ExposureSelection
from app.services.historical_exposure_source import project_encounter,chronology
from app.services import legacy_game_authority as owner
from app.services.legacy_game_capture import CaptureRequest,capture_source_scope
from test_legacy_scoped_capture import source_fixture


def fixture(competitor=None):
    value,source=source_fixture(competitor=competitor)
    source.captured.game['played_at']='2026-10-09T12:00:00Z'
    source.rows['cards'][0]['introduced_at']='2026-10-15'
    captured=capture_source_scope(CaptureRequest('ruy',value.game_id,(5,)),source.read,source.metadata)
    card=captured.tables['cards'][0];token=owner.current_token(captured,'ruy')
    selection=ExposureSelection(target_id=token.target_id,workspace_id='legacy',repertoire_id='ruy',
        game_id=value.game_id,source_digest=captured.game['content_hash'],authority_id=token.storage_identity()[0],
        ply=5,card_id=card['id'],card_revision=1,
        executable_digest=owner.fingerprint(dict(starting_fen=card['start_fen'],
            moves=json.loads(card['moves_json']),trained_color=card['trained_color'])),
        route_ids=sorted(row['id'] for row in captured.tables['repertoire_lines']),expected_moves=['a7a6'],
        confirmation='practice_this_encountered_line')
    return selection,captured


def test_exact_historical_departure_projects_practice_facts_without_fabricating_recall():
    selection,captured=fixture();before=deepcopy(captured)
    projected=project_encounter(selection,captured)
    assert projected.request.user_requested and projected.request.encounter.card_id=='physical-black'
    assert projected.request.encounter.observed_move=='g8f6' and projected.request.encounter.expected_moves==('a7a6',)
    assert chronology(captured.tables['cards'][0]['introduced_at'],projected.request.encounter.occurred_at)=='introduced_after_game'
    assert not captured.tables['cards'][0]['introduced_before_game']
    assert captured==before


@pytest.mark.parametrize('changes',[{'target_id':'other'},{'source_digest':'c'*64},{'card_revision':2},
    {'route_ids':['missing-route']},{'expected_moves':['g8f6']},{'executable_digest':'c'*64},{'ply':3}])
def test_changed_selection_or_unscoped_decision_cannot_create_practice_facts(changes):
    selection,captured=fixture()
    with pytest.raises(policy.ExposureRejected):
        project_encounter(selection.model_copy(update=changes),captured)


@pytest.mark.parametrize('competitor',['foreign','unbound','unsupported'])
def test_complete_competing_route_or_unbound_alternative_cannot_be_dropped(competitor):
    selection,captured=fixture(competitor)
    with pytest.raises((policy.ExposureRejected,RemediationError)):
        project_encounter(selection,captured)


def test_projection_requires_the_same_physical_owner_in_every_applicable_route():
    selection,captured=fixture()
    captured.tables['cards'].append({**captured.tables['cards'][0],'id':'other-card'})
    captured.tables['repertoire_cards'].append(dict(repertoire_id='ruy',card_id='other-card'))
    captured.graphs.append({**captured.graphs[0],'card_id':'other-card'})
    with pytest.raises(policy.ExposureRejected,match='current_exact|unambiguous'):
        project_encounter(selection,captured)


@pytest.mark.parametrize('value,result',[('2026-10-15','introduced_after_game'),('2026-10-08','introduced_before_game'),
    ('2026-10-09','unknown'),(None,'unknown'),('invalid','unknown')])
def test_chronology_is_explicit_and_never_backdated(value,result):
    selection,captured=fixture()
    assert chronology(value,owner.completed_game_time(captured.game))==result


@pytest.mark.parametrize('occurred_at,expected',[
    ('2026-10-08T22:00:00-04:00','unknown'),
    ('2026-10-09T00:30:00+09:00','introduced_after_game'),
])
def test_date_only_introduction_uses_existing_utc_day_bounds_not_game_local_date(occurred_at,expected):
    from datetime import datetime
    assert chronology('2026-10-09',datetime.fromisoformat(occurred_at))==expected
