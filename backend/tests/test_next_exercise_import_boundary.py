"""Check actual public imports in a fresh interpreter, with a negative control."""
import os
import subprocess
import sys


def guarded_import(target, inject=False):
    code = '''
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi', 'celery', 'psycopg', 'redis', 'tempo_storage'} or fullname.startswith(('app.main', 'app.product_composition', 'app.services', 'app.providers')):
            raise RuntimeError('forbidden import: ' + fullname)
        return None
sys.meta_path.insert(0, Guard())
''' + ("import fastapi\n" if inject else f"import {target}\n")
    return subprocess.run([sys.executable, '-c', code], env=os.environ.copy(), capture_output=True, text=True)


def test_actual_contract_and_operation_are_inert():
    for module in ('app.contracts.queue_issuance', 'app.study.next_exercise'):
        result = guarded_import(module)
        assert result.returncode == 0, result.stderr


def test_guard_rejects_forbidden_dependency():
    result = guarded_import('app.study.next_exercise', inject=True)
    assert result.returncode != 0 and 'forbidden import: fastapi' in result.stderr
