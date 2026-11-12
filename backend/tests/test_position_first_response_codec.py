"""Receipt bytes, adjudication and issued-plan identity stay stable."""
from types import SimpleNamespace as NS
import os
import subprocess
import sys

import pytest
from app.study.position_learning.first_response_codec import (
    _position_canonical, _position_digest, decode_position_first_response,
    position_first_response_envelope,
)


def plan():
    key = NS(repertoire_id='r', variant='standard', position='p')
    definition = NS(key=key, expected_uci='e2e4', policy_version=1)
    slot = NS(definition=definition, fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', original_ply=0)
    return NS(decision_slots=[slot], attempt_id='a', queue_entry_id=3, original_card_id='c', original_card_revision=2, owner_epoch=4)


def envelope(move='e2e4', disposition='wrong', manual=False):
    return position_first_response_envelope(plan(), event_id='e', decision_index=0, actual_uci=move,
        manual_failure=manual, assistance_before=False, observed_at='2026-01-01T00:00:00Z',
        classify_alternative=lambda *_: disposition)


def test_canonical_bytes_and_digest():
    assert _position_canonical({'b': 'é', 'a': 1}) == '{"a":1,"b":"\\u00e9"}'
    assert _position_digest({'a': 1}) == '015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862'


@pytest.mark.parametrize(('move', 'source', 'manual', 'expected'), [
    ('e2e4', 'wrong', False, 'expected'), ('d2d4', 'wrong', False, 'wrong'),
    ('d2d4', 'alternate', False, 'alternate'), ('d2d4', 'unverified', False, 'unverified'),
    ('e2e5', 'wrong', False, 'illegal'), (None, 'wrong', True, 'manual_failure'),
])
def test_dispositions_round_trip(move, source, manual, expected):
    value = envelope(move, source, manual)
    assert value['disposition'] == expected
    assert decode_position_first_response(value, _position_digest(value), plan()) == value


def test_forged_digest_and_identity_are_rejected():
    value = envelope()
    with pytest.raises(ValueError, match='first_response_digest_mismatch'):
        decode_position_first_response(value, 'forged', plan())
    value['attempt_id'] = 'other'
    with pytest.raises(ValueError, match='first_response_issued_plan_mismatch'):
        decode_position_first_response(value, _position_digest(value), plan())


def test_codec_import_does_not_load_transport_or_storage():
    script = '''\nimport importlib.abc, sys\nclass Block(importlib.abc.MetaPathFinder):\n def find_spec(self, fullname, path=None, target=None):\n  if fullname == "fastapi" or fullname.startswith("fastapi.") or fullname == "tempo_storage" or fullname.startswith("tempo_storage.") or fullname == "app.study.position_checkpoint": raise AssertionError(fullname)\nsys.meta_path.insert(0, Block())\nfrom app.study.position_learning.first_response_codec import decode_position_first_response\n'''
    subprocess.run([sys.executable, '-c', script], env=os.environ.copy(), check=True)
