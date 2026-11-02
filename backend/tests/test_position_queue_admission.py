from types import SimpleNamespace as NS

from tempo_evidence_math.position_learning import Hold
from app.services.position_queue_admission import select_carrier


def binding(card, *, repertoire='r', response='e2e4', available=True):
    return NS(repertoire_id=repertoire, position_key='position', selected_response_uci=response,
              presentation_id=card, presentation_revision=1, decision_id=card,
              available=available, introduction='introduced', introduction_matches_current_content=True)


def source(bindings):
    key = NS(repertoire_id='r', position='position')
    target = NS(key=key, expected_uci='e2e4')
    return NS(authored_token=NS(repertoire_id='r'),
              snapshot=NS(decision_presentations=bindings),
              routes=[NS(decisions=[NS(definition=target)])]), target


def test_same_repertoire_aliases_choose_one_deterministic_carrier():
    prepared, target = source([binding('b'), binding('a'), binding('x', repertoire='other')])
    assert select_carrier(prepared, target).presentation_id == 'a'


def test_conflicting_response_or_unavailable_carrier_holds():
    prepared, target = source([binding('a', response='d2d4')])
    assert isinstance(select_carrier(prepared, target), Hold)
    prepared, target = source([binding('a', available=False)])
    assert isinstance(select_carrier(prepared, target), Hold)
