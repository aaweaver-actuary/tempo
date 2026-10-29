"""Run the DB owner's fake readback cases once in Learning's regular suite."""

import importlib.util
from io import StringIO
from pathlib import Path
import unittest


def test_runtime_database_readback_contract_cases():
    root = Path(__file__).resolve().parents[4]
    source = root / "database/tempo_learning_database/tests/test_runtime_readback.py"
    specification = importlib.util.spec_from_file_location("learning_runtime_readback_cases", source)
    cases = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(cases)
    suite = unittest.TestLoader().loadTestsFromModule(cases)
    assert suite.countTestCases() == 23, "All 23 owning cases must be selected exactly once"
    output = StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    print(output.getvalue())
    assert result.testsRun == 23, output.getvalue()
    assert not result.failures and not result.errors and not result.skipped, output.getvalue()
