"""Study preference works from supplied facts without a concrete database."""
from pathlib import Path
import subprocess
import sys

from app.study import frozen_order as policy


def test_frozen_policy_import_needs_no_database_or_sibling_domain():
    probe = r'''
from importlib.abc import MetaPathFinder
import sys
sys.path.insert(0, sys.argv[1])
class RejectInfrastructure(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(('tempo_', 'psycopg', 'sqlite3', 'redis', 'celery')) or
                (fullname.startswith('app.') and not
                 (fullname == 'app.study' or fullname.startswith('app.study.frozen_order')))):
            raise AssertionError('Policy imported outside its domain: ' + fullname)
sys.meta_path.insert(0, RejectInfrastructure())
from app.study import frozen_order
facts = frozen_order.FrozenAnnotationFacts((None, 2), {}, set(), set())
assert frozen_order.annotate([{'id': 'card'}], facts, fingerprint=lambda *_: 'unused') == [
    {'id': 'card', 'frozen_token': [None, 2]}]
'''
    backend_root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-I', '-c', probe, str(backend_root)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_frozen_policy_keeps_legacy_protection_and_route_blocks_independent():
    facts = policy.FrozenAnnotationFacts(
        ('run', 3), {'captured': policy.FrozenRankFacts(1, 'old', {'revision': 2}, ('r',))},
        {'legacy'}, {('fallback', 'blocked')},
    )
    candidates = [{'id': 'captured'}, {'id': 'legacy'}, {'id': 'fallback', 'repertoire_id': 'blocked'}]
    rows = policy.annotate(candidates, facts, fingerprint=lambda *_: 'changed')
    assert [(row['frozen_rank'], row['frozen_valid'], row['frozen_route_valid']) for row in rows] == [
        (1, False, True), (None, False, True), (None, True, False)]
    assert all(row['frozen_token'] == ['run', 3] for row in rows)
    assert all('frozen_token' not in row for row in candidates)


def test_stale_proposal_does_not_call_capabilities_and_miss_requires_real_evidence():
    calls = []
    capabilities = dict(miss_reason='miss',
                        has_outstanding_miss=lambda card: calls.append(('miss', card)) or False,
                        current_definition_valid=lambda card: calls.append(('definition', card)) or True)
    assert not policy.proposal_valid({'card_id': 'card', 'frozen_token': ['old', 1]}, ('run', 2), **capabilities)
    assert calls == []
    planned = {'card_id': 'card', 'frozen_token': ['run', 2], 'reason': 'miss'}
    assert not policy.proposal_valid(planned, ('run', 2), **capabilities)
    assert calls == [('miss', 'card')]
    assert policy.proposal_valid({**planned, 'reason': 'ordinary'}, ('run', 2), **capabilities)
    assert calls[-1] == ('definition', 'card')


def test_frozen_slot_policy_preserves_exposure_and_unrelated_slots():
    def row(identifier, rank, **extra):
        return {'id': identifier, 'card_id': str(identifier), 'position': identifier,
                'content_type': 'opening', 'admission_kind': 'new',
                'frozen_valid': True, 'frozen_rank': rank, **extra}
    rows = [row(1, 4), row(2, 3), row(3, 2, content_type='review'), row(4, 1)]
    assert [r['id'] for r in policy.order_queue_slots(
        rows, miss_reason='miss', card_exposed=lambda card: card == '2', preserve_through_entry_id=0,
    )] == [4, 2, 3, 1]
