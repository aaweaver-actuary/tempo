"""Pure defense codecs are shared without importing the threat producer."""
import ast
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import chess
import pytest
from tempo_learning_runtime import defense_decode as codec
from tempo_learning_runtime.threat_validation import AnalysisLine, AnalysisReport, AnalysisRequest, EngineScore


def test_public_codecs_preserve_compatibility_and_consumer_imports():
    for public, old in (('request_from_json', '_request_from_json'), ('seed_from_json', '_seed_from_json'), ('anchor_from_json', '_anchor_from_json')):
        assert getattr(codec, public) is getattr(codec, old)
    from app.services import threat_pipeline
    for name in ('_request_from_json', '_seed_from_json', '_anchor_from_json', 'report_from_json', 'validate_analysis_report', 'ENGINE_VERSION', 'NETWORK_VERSION'):
        assert getattr(threat_pipeline, name) is getattr(codec, name)
    assert codec.ENGINE_VERSION == 'Stockfish 19 WASM'
    assert codec.NETWORK_VERSION == 'nn-61e7af4bb97d.nnue'
    root = Path(__file__).resolve().parents[1] / 'app'
    for name in ('threat_analysis_commands.py', 'services/threat_training.py', 'services/discovery_admission.py', 'game_analysis_commands.py', 'services/game_analysis_worker.py'):
        imports = [n.module or '' for n in ast.walk(ast.parse((root / name).read_text())) if isinstance(n, ast.ImportFrom)]
        assert not any(name.endswith('threat_pipeline') for name in imports)
        assert 'tempo_learning_runtime.defense_decode' in imports


def test_owner_import_is_cold():
    code = '''
import importlib.abc, socket, ssl, subprocess, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.services.threat_pipeline" or fullname.startswith("app.games.providers"):
            raise AssertionError(fullname)
def forbidden(*args, **kwargs): raise AssertionError("startup")
sys.meta_path.insert(0, Guard())
socket.socket = forbidden
subprocess.Popen = forbidden
from tempo_learning_runtime import defense_decode
assert defense_decode.request_from_json is defense_decode._request_from_json
import app.threat_analysis_commands
import app.services.threat_training
import app.services.discovery_admission
import app.game_analysis_commands
import app.services.game_analysis_worker
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_parser_and_report_validation_preserve_rejections():
    raw = dict(position_start_fen=chess.STARTING_FEN, position_prefix_uci=[], engine_version='e', network_version='n', depth=10, multipv=1, root_move_uci=None)
    request = codec.request_from_json(raw)
    assert request.position_prefix_uci == ()
    with pytest.raises(KeyError): codec.request_from_json({})
    with pytest.raises(KeyError): codec.seed_from_json({})
    with pytest.raises(KeyError): codec.anchor_from_json({})
    with pytest.raises(ValueError): EngineScore()
    line = AnalysisLine('e2e4', ('e2e4',), EngineScore(cp=10), 10)
    report = AnalysisReport(request, (line,), True)
    codec.validate_analysis_report(request, report)
    for bad_request, bad_report in [
        (request, replace(report, complete=False)),
        (replace(request, depth=11), report),
        (replace(request, position_prefix_uci=('e2e5',)), replace(report, request=replace(request, position_prefix_uci=('e2e5',)))),
        (request, replace(report, lines=(replace(line, depth=9),))),
        (request, replace(report, lines=(replace(line, root_move_uci='d2d4'),))),
        (request, replace(report, lines=(replace(line, pv_uci=('e2e4', 'e7e4')),))),
    ]:
        with pytest.raises(ValueError): codec.validate_analysis_report(bad_request, bad_report)
    decoded = codec.report_from_json({'request': raw, 'lines': [{'root_move_uci': 'e2e4', 'pv_uci': ['e2e4'], 'score': {'cp': 10}, 'depth': 10}], 'complete': True})
    assert decoded == report
