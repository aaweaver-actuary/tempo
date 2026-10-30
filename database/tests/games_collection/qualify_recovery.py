"""Source-bound entry for the explicit managed Study recovery suite.

The component runner supplies Docker admission, resource ownership and reports.
This entry binds host-side fixture imports to the selected checkout and delegates
all execution to the existing pytest assembly; it starts no service itself.
"""
from pathlib import Path
import importlib.util
import os
import sys

ROOT = Path(__file__).resolve().parents[3]
SELECTION = ROOT / 'database/tests/games_collection/test_study_recovery_packaged.py'


def main(arguments=()):
    paths = [ROOT / 'backend', ROOT / 'database', ROOT / 'backend/tests']
    for pattern in ('packages/*/src', 'contracts/*/src', 'services/learning/*/src'):
        paths.extend(sorted(ROOT.glob(pattern)))
    assert all(path.is_dir() for path in paths)
    sys.path[:0] = list(map(str, paths))
    os.environ['PYTHONPATH'] = os.pathsep.join(map(str, paths))
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    for name in ('app', 'tempo_legacy_database', 'tempo_game_review_contracts', 'tempo_learning_remediation'):
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None or not Path(spec.origin).resolve().is_relative_to(ROOT):
            raise RuntimeError('Recovery fixture source is not bound to the selected checkout: ' + name)
    if not SELECTION.is_file():
        raise RuntimeError('Missing explicit Study recovery assembly selection')
    import pytest
    return pytest.main(['-q', str(SELECTION), *arguments])


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
