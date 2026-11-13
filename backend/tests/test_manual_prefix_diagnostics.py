"""Guard the bounded disposable split diagnostic against foreign identities."""
import ast
from pathlib import Path
import re
import uuid
import pytest


def test_split_diagnostic_binds_exact_owned_ids_and_rejects_foreign_values():
    path = Path(__file__).resolve().parents[2] / 'tests/fixtures/postgres-manual-prefix-diagnostics.py'
    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'validate_ids')
    namespace = {'re': re, 'uuid': uuid}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    validate = namespace['validate_ids']; intent = str(uuid.uuid4()); op = str(uuid.uuid4()); attempt = str(uuid.uuid4())
    assert validate('a' * 64, intent, op, 'review-attempt:' + attempt) == 'prefix-split-intent:' + intent
    for args in [('foreign', intent, op, 'review-attempt:' + attempt), ('a'*64, '../foreign', op, 'review-attempt:' + attempt),
                 ('a'*64, intent, op, 'review-attempt:foreign')]:
        with pytest.raises(ValueError): validate(*args)
    source = path.read_text()
    assert 'SET TRANSACTION READ ONLY' in source and 'tempo-disposable-postgres-test' in source
    assert 'attempt_token IS NOT NULL' in source and 'left(last_error_json,1000)' in source


def test_split_browser_preserves_original_and_cleanup_failures():
    path = Path(__file__).resolve().parents[2] / 'tests/browser/manual-prefix-split-http.spec.ts'
    source = path.read_text()
    assert 'Original split failure:' in source and 'Fixture cleanup failure:' in source
    assert 'throw original;' in source and "timeout:90_000" in source
    assert "split-diagnostic-failure" in source and '.catch(() => undefined)' in source
