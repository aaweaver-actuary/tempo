"""Selected native evidence must describe actual, successful unittest execution."""
import importlib.util
from pathlib import Path
import unittest

import pytest

specification = importlib.util.spec_from_file_location(
    'ci_native_unittest', Path(__file__).resolve().parents[2] / 'scripts/ci-native-unittest.py')
native_runner = importlib.util.module_from_spec(specification)
specification.loader.exec_module(native_runner)


def test_issue239_native_report_collects_and_executes_real_cases():
    class PassingCase(unittest.TestCase):
        def test_native_assertion(self):
            self.assertEqual(2 + 2, 4)
    evidence, success = native_runner.execute_suite(unittest.defaultTestLoader.loadTestsFromTestCase(PassingCase))
    assert success
    assert evidence['expected'] == [test['id'] for test in evidence['tests']]
    assert evidence['tests'][0]['status'] == 'passed'


@pytest.mark.parametrize('outcome', ['failure', 'error', 'skip', 'subtest', 'expected_failure'])
def test_issue239_native_report_rejects_unsuccessful_or_skipped_cases(outcome):
    class NonPassingCase(unittest.TestCase):
        def test_native_assertion(self):
            if outcome == 'skip':
                self.skipTest('intentional harness regression')
            if outcome == 'error':
                raise RuntimeError('intentional harness regression')
            if outcome == 'subtest':
                with self.subTest('failed subtest'):
                    self.assertEqual(1, 2)
            else:
                self.assertEqual(1, 2)
    if outcome == 'expected_failure':
        NonPassingCase.test_native_assertion = unittest.expectedFailure(NonPassingCase.test_native_assertion)
    evidence, success = native_runner.execute_suite(unittest.defaultTestLoader.loadTestsFromTestCase(NonPassingCase))
    assert not success
    assert evidence['tests'][0]['status'] != 'passed'


def test_issue239_native_report_rejects_empty_and_duplicate_collection():
    with pytest.raises(RuntimeError, match='Empty or duplicate'):
        native_runner.execute_suite(unittest.TestSuite())
    case = unittest.FunctionTestCase(lambda: None)
    with pytest.raises(RuntimeError, match='Empty or duplicate'):
        native_runner.execute_suite(unittest.TestSuite([case, case]))
