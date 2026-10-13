"""Collect and report the existing native fixture suite without changing its assertions."""
import json
import sys
import unittest


def case_ids(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from case_ids(case)
        else:
            yield case.id()


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *arguments):
        super().__init__(*arguments)
        self.observed = {}

    def startTest(self, case):
        super().startTest(case)
        if case.id() in self.observed:
            raise RuntimeError('Duplicate native test execution')
        self.observed[case.id()] = 'incomplete'

    def addSuccess(self, case):
        super().addSuccess(case)
        self.observed[case.id()] = 'passed'

    def addFailure(self, case, error):
        super().addFailure(case, error)
        self.observed[case.id()] = 'failed'

    def addError(self, case, error):
        super().addError(case, error)
        self.observed[case.id()] = 'failed'

    def addSkip(self, case, reason):
        super().addSkip(case, reason)
        self.observed[case.id()] = 'skipped'

    def addSubTest(self, case, subtest, error):
        super().addSubTest(case, subtest, error)
        if error is not None:
            self.observed[case.id()] = 'failed'


def execute_suite(suite):
    expected = list(case_ids(suite))
    if not expected or len(expected) != len(set(expected)):
        raise RuntimeError('Empty or duplicate native collection')
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    tests = [dict(id=identity, status=status, retries=0) for identity, status in result.observed.items()]
    evidence = dict(expected=expected, tests=tests)
    success = result.wasSuccessful() and sorted(expected) == sorted(result.observed) and all(
        test['status'] == 'passed' for test in tests)
    return evidence, success


if __name__ == '__main__':
    if sys.argv[1:] != ['redis-fixture-isolation']:
        raise RuntimeError('Unknown native suite')
    import check_postgres_redis_fixture_isolation as native_fixture
    native_fixture.fixture.configure()
    collected = unittest.defaultTestLoader.loadTestsFromTestCase(native_fixture.RedisFixtureIsolation)
    evidence, success = execute_suite(collected)
    print(json.dumps(evidence))
    sys.exit(0 if success else 1)
