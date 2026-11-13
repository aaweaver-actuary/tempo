"""The disposable browser fixture can recover displacement after lost seed output."""
import ast
import json
from pathlib import Path

import pytest


def receipt_validator():
    path = Path(__file__).resolve().parents[2] / 'tests/fixtures/postgres-frozen-study-browser.py'
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'validate_displacement_receipt')
    namespace = {'json': json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['validate_displacement_receipt']


def test_lost_seed_ack_recovers_exact_rows_and_rejects_foreign_or_changed_receipt():
    validate = receipt_validator()
    rows = [[17, 4, 24], [21, 7, 27]]
    receipt = {'kind': 'p12-frozen-displacement-v1', 'prefix': 'owned', 'day': '2026-11-13', 'rows': rows}
    assert validate(json.dumps(receipt), 'owned', '2026-11-13', []) == rows
    assert validate(json.dumps(receipt), 'owned', '2026-11-13', rows) == rows
    for bad in ({**receipt, 'prefix': 'foreign'}, {**receipt, 'day': '2026-11-14'},
                {**receipt, 'rows': [rows[0], rows[0]]}, {**receipt, 'rows': [[True, 4, 24]]}):
        with pytest.raises(RuntimeError, match='preserve fixture'):
            validate(json.dumps(bad), 'owned', '2026-11-13', [])
    with pytest.raises(RuntimeError, match='preserve fixture'):
        validate(json.dumps(receipt), 'owned', '2026-11-13', [[17, 4, 25]])
