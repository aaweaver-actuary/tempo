"""Retained Study qualification consumes pure facts, never a Games producer."""
import os
import subprocess
import sys

from app.contracts.product_v02.learning import GameEffectControl, GameEncounter, GameLapse
from test_product_game_lapse import fixture


def test_retained_qualification_leaf_preserves_frozen_delivery_identities():
    from app.games.feedback import coordinator, ports, qualification
    lapse = fixture('lapse', GameLapse)
    encounter = fixture('encounters/unintroduced-missed', GameEncounter)
    control = GameEffectControl(key=encounter.key, source_revision=encounter.source.source_revision,
        action='hold', reason='reanalysis')
    # Captured from the unchanged b79ebafc coordinator, not recomputed expected IDs.
    expected = (
        (lapse, 'c350c412e152ae83a0901f3332175077ff98bab339bde2abcb05a70efb0fe01c'),
        (encounter, '18f7c69f6bbe92e3e24eb0cad5ccf345ddcabb65f9a83967110323fb1940b00d'),
        (control, '0efd33a3a4613710b9c1412aff2138d2f28ce0f33db4b78470e43719d0165bc7'),
    )
    assert coordinator.delivery_for is qualification.delivery_for is ports.delivery_for
    assert coordinator.stable_id is ports.stable_id and coordinator.source_revision is ports.source_revision
    for command, identity in expected:
        delivery = qualification.delivery_for('synthetic-source', command)
        assert delivery.operation_id == identity and delivery.command == command
        assert delivery == coordinator.delivery_for('synthetic-source', command)


def test_retained_qualification_leaf_imports_without_games_producer_or_infrastructure():
    script = '''
import importlib, importlib.abc, sys
import chess, pydantic
blocked = ('app.games.feedback.coordinator', 'app.games.feedback.jobs', 'app.games.feedback.review',
    'app.games.feedback.continuous', 'app.games.providers', 'app.services', 'app.main', 'app.tasks',
    'app.product_composition', 'tempo_legacy_database', 'psycopg', 'sqlite3', 'httpx', 'requests')
class NoProducer(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(blocked):
            raise AssertionError('optional producer or infrastructure: ' + fullname)
sys.meta_path.insert(0, NoProducer())
from app.games.feedback.qualification import delivery_for, select_first_boundary
assert callable(delivery_for) and callable(select_first_boundary)
assert not any(name.startswith(blocked) for name in sys.modules)
for module in ('app.games.feedback.coordinator', 'app.games.providers.lichess_acquisition'):
    try:
        importlib.import_module(module)
    except AssertionError as error:
        assert 'optional producer or infrastructure' in str(error)
    else:
        raise AssertionError('producer negative control was not blocked')
'''
    result = subprocess.run([sys.executable, '-B', '-c', script], env=os.environ.copy(),
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
