"""Bridge safety tests without Docker or a database; not native acceptance."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).with_name('position-native-pytest.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class SQL:
    SQL = staticmethod(lambda value: value)
    Identifier = staticmethod(lambda value: value)
    Literal = staticmethod(lambda value: value)


class Admin:
    def __init__(self, marker=('tempo', 'tempo-disposable-postgres-test'), fail_drop=False):
        self.calls, self.marker, self.fail_drop = [], marker, fail_drop
    def execute(self, command):
        self.calls.append(command)
        if command.startswith('DROP') and self.fail_drop:
            raise RuntimeError('drop failed')
        return SimpleNamespace(fetchone=lambda: self.marker)


class BridgeTests(unittest.TestCase):
    def test_exact_case_rejects_empty_duplicate_skipped_failed_and_incomplete(self):
        record = bridge.ExactCase()
        self.assertFalse(record.passed(0))
        record.collected = [bridge.NODEID]
        for phase in ('setup', 'call', 'teardown'):
            record.pytest_runtest_logreport(SimpleNamespace(nodeid=bridge.NODEID, when=phase, outcome='passed'))
        self.assertTrue(record.passed(0))
        self.assertFalse(record.passed(1))
        for outcome in ('skipped', 'failed'):
            record.reports[1]['status'] = outcome
            self.assertFalse(record.passed(0))
        record.reports[1]['status'] = 'passed'
        record.collected *= 2
        self.assertFalse(record.passed(0))
        record.collected = [bridge.NODEID]
        record.reports.pop()
        self.assertFalse(record.passed(0))

    def test_refuse_foreign_environment(self):
        env = {'TEMPO_TEST_INSTANCE': 'disposable', 'TEMPO_TEST_COMPOSE_PROJECT': 'tempo-pg-regressions-123-abcdef12', 'TEMPO_NATIVE_CANDIDATE_REVISION': 'a' * 40}
        self.assertEqual(bridge.identity(env)[0], env['TEMPO_TEST_COMPOSE_PROJECT'])
        for key in env:
            with self.assertRaises(RuntimeError):
                bridge.identity({**env, key: 'foreign'})

    def test_database_cleanup_on_case_failure(self):
        admin, evidence = Admin(), {}
        def fail():
            raise RuntimeError('case failed')
        with self.assertRaisesRegex(RuntimeError, 'case failed'):
            bridge.with_owned_database(admin, SQL, 'tempo_position_test_' + 'a' * 32, 'project', fail, evidence)
        self.assertTrue(admin.calls[-1].startswith('DROP DATABASE tempo_position_test_'))
        self.assertEqual(evidence['cleanup'], 'dropped')

    def test_cleanup_failure_is_not_success(self):
        evidence = {}
        with self.assertRaisesRegex(RuntimeError, 'drop failed'):
            bridge.with_owned_database(Admin(fail_drop=True), SQL, 'tempo_position_test_' + 'a' * 32, 'project', lambda: None, evidence)
        self.assertEqual(evidence['cleanup'], 'failed')

    def test_foreign_database_and_marker_refused_without_mutation(self):
        admin = Admin()
        with self.assertRaises(RuntimeError):
            bridge.with_owned_database(admin, SQL, 'tempo', 'project', lambda: None, {})
        self.assertEqual(admin.calls, [])
        admin = Admin(('tempo', None))
        with self.assertRaises(RuntimeError):
            bridge.with_owned_database(admin, SQL, 'tempo_position_test_' + 'a' * 32, 'project', lambda: None, {})
        self.assertEqual(len(admin.calls), 1)

    def test_requirements_are_hash_bound_to_current_lock(self):
        lock = bridge.tomllib.loads((bridge.ROOT / 'backend/uv.lock').read_text())
        text = bridge.locked_requirements(lock)
        self.assertEqual(len(text.splitlines()), 5)
        self.assertIn('pytest==9.1.1 --hash=sha256:', text)
        with self.assertRaises(RuntimeError):
            bridge.locked_requirements({'package': []})


if __name__ == '__main__':
    unittest.main()
