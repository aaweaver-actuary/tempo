"""Explorer owner surface with unavailable-configuration fake-only semantics."""
import asyncio
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
for relative in ('backend','packages/game-review-contracts/src','packages/chess-core/src','contracts/game-evidence/src','packages/evidence-math/src','contracts/analysis-jobs/src'):
    sys.path.insert(0, str(ROOT/relative))


def test_probability_exports_preserve_existing_identity():
    from app.analysis.probability import interfaces as public, ports, explorer
    for name in public.__all__:
        assert getattr(public, name) is getattr(explorer if name == 'ExplorerClient' else ports, name)


def test_unconfigured_probability_uses_existing_safe_result_without_transport():
    import chess
    from app.analysis.probability.interfaces import ExplorerClient, ExplorerRequest
    from tempo_evidence_math import move_universe
    def deny():raise AssertionError('transport must not start')
    client=ExplorerClient(transport_factory=deny)
    request=ExplorerRequest(position=move_universe(chess.STARTING_FEN),starting_fen=chess.STARTING_FEN,
        history_uci=(),variant='standard',population=None,since='2026-01',until='2026-10',move_limit=20)
    result=asyncio.run(client.fetch(request,credential=None))
    assert result.evidence is None
    assert result.diagnostic.code=='configuration_required'


def test_probability_cold_import_is_inert():
    code='''
import sys,socket,subprocess,httpx
sys.path[:0]=PATHS
from unittest.mock import patch
def deny(*args,**kwargs):raise AssertionError('startup forbidden')
with patch.object(socket.socket,'connect',deny),patch.object(subprocess,'Popen',deny),patch.object(httpx.AsyncClient,'__init__',deny):
 import app.analysis.probability.interfaces
assert 'app.main' not in sys.modules
'''.replace('PATHS',repr([str(ROOT/p) for p in ('backend','packages/game-review-contracts/src','packages/chess-core/src','contracts/game-evidence/src','packages/evidence-math/src','contracts/analysis-jobs/src')]))
    subprocess.run([sys.executable,'-c',code],check=True,timeout=5)
