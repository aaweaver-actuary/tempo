"""Producer compatibility fixtures must track the executable producer assembly."""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def run_check(fixture):
    paths = ['backend', 'database', 'packages/scheduling/src']
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(str(ROOT / path) for path in paths)}
    return subprocess.run([sys.executable, str(HERE / 'generate.py'), '--check', '--fixture', str(fixture)],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def test_producer_fixture_is_fresh_and_detects_drift(tmp_path):
    fixture = HERE / 'producer.json'
    assert run_check(fixture).returncode == 0
    stale = tmp_path / 'stale.json'
    stale.write_text(fixture.read_text().replace('opening-1', 'stale', 1))
    result = run_check(stale)
    assert result.returncode != 0
    assert 'fixture is stale' in result.stderr
