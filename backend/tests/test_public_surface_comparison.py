"""Pure comparison/classification exports; unavailable action stays unavailable."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
PATHS=[str(ROOT/'backend')]+[str(p/'src') for p in (ROOT/'packages').iterdir() if (p/'src').is_dir()]
sys.path[:0]=PATHS


def test_comparison_export_identity_and_route_progression():
    from tempo_repertoire_comparison import interfaces as public
    import tempo_repertoire_comparison as existing
    for name in public.__all__:assert getattr(public,name) is getattr(existing,name)
    route=public.TraversalRoute(('start','after'),('e2e4',))
    frontier=public.RouteFrontier((route,),'start')
    candidates=frontier.candidates('start');assert candidates=={(0,0)}
    frontier.advance(candidates,'e2e4');assert frontier.candidates('after')=={(0,1)}


def test_classification_identity_and_conflict_policy_unchanged():
    from tempo_game_classification import interfaces as public
    import tempo_game_classification as existing
    for name in public.__all__:assert getattr(public,name) is getattr(existing,name)
    kind,reason=public.classify_response(played_uci='e2e4',expected_uci=('e2e4','d2d4'),learner_turn=True)
    assert kind=='conflict'
    assert reason=='multiple_own_responses_in_one_repertoire'


def test_action_placeholder_does_not_claim_effects():
    from tempo_game_actions.interfaces import UnimplementedActions
    from tempo_game_actions import UnimplementedActions as existing
    assert UnimplementedActions is existing
    request=object()
    result=UnimplementedActions().execute(request)
    assert result.component=='actions'
    assert 'not implemented' in result.reason


def test_comparison_cold_import_starts_no_connection_process_or_app():
    code='''
import sys,socket,subprocess
sys.path[:0]=PATHS
from unittest.mock import patch
def deny(*a,**kw):raise AssertionError('startup forbidden')
with patch.object(socket.socket,'connect',deny),patch.object(subprocess,'Popen',deny):
 import tempo_repertoire_comparison.interfaces,tempo_game_classification.interfaces,tempo_game_actions.interfaces
assert 'app.main' not in sys.modules
'''.replace('PATHS',repr(PATHS))
    subprocess.run([sys.executable,'-c',code],check=True,timeout=5)
