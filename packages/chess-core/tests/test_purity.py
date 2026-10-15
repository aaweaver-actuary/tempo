import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROBE = '''
import sys, json
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] not in sys.stdlib_module_names | {'tempo_chess_core'}:
            raise AssertionError('unexpected runtime dependency: ' + fullname)
sys.meta_path.insert(0, Guard())
def reject_initialization(event, arguments):
    if event in {'socket.connect','socket.bind','subprocess.Popen','sqlite3.connect','os.system','os.fork'}:
        raise AssertionError('environmental initialization: ' + event)
sys.addaudithook(reject_initialization)
import tempo_chess_core
assert 'site-packages' in tempo_chess_core.__file__
print(json.dumps([{'fenKey':tempo_chess_core.canonical_fen_key(case['fen']),
                  'cardId':tempo_chess_core.card_id(case['fen'],case['moves'])}
                 for case in json.load(sys.stdin)]))
'''


@pytest.mark.parametrize('host_timezone', ['UTC', 'America/New_York', 'Asia/Tokyo'])
def test_installed_identity_runs_in_fresh_process_without_service_or_engine_dependencies(host_timezone):
    baseline = (Path(__file__).parent / 'baseline.json').read_text()
    outputs = []
    for _ in range(2):
        result = subprocess.run([sys.executable, '-I', '-B', '-c', PROBE], input=baseline,
            env={**os.environ, 'TZ': host_timezone, 'PYTHONPATH': ''},
            text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1] == [{'fenKey':case['fenKey'], 'cardId':case['cardId']}
                                      for case in json.loads(baseline)]
