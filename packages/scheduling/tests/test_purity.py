import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

TEST_ROOT = Path(__file__).parent
BASELINE = (TEST_ROOT / 'baseline.json').read_text()


@pytest.mark.parametrize('host_timezone', ['UTC', 'America/New_York', 'Asia/Tokyo'])
def test_installed_scheduling_is_identical_across_fresh_guarded_processes(host_timezone):
    outputs = []
    for _ in range(2):
        result = subprocess.run([sys.executable, '-I', '-B', str(TEST_ROOT / 'guarded_probe.py')],
            input=BASELINE, text=True, capture_output=True, timeout=20,
            env={**os.environ, 'TZ': host_timezone, 'PYTHONPATH': '', 'PYTHONHOME': ''})
        assert result.returncode == 0, result.stdout + result.stderr
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1] == [case['expected'] for case in json.loads(BASELINE)]


@pytest.mark.parametrize('forbidden_api', ['clock', 'sleep', 'identity', 'random'])
def test_installed_guard_rejects_environmental_apis(forbidden_api):
    result = subprocess.run([sys.executable, '-I', '-B', str(TEST_ROOT / 'guarded_probe.py'), forbidden_api],
        input=BASELINE, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert 'ambient' in result.stderr
