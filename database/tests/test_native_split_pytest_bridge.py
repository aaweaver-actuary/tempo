"""Finite split fixture ownership checks; no database or Docker activity."""
import ast
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('split_bridge', Path(__file__).with_name('native-split-pytest.py'))
split = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split)


class SplitBridgeTests(unittest.TestCase):
    def test_exact_selection_and_phase_evidence(self):
        self.assertEqual(split.selected_nodeid({'TEMPO_NATIVE_POSITION_CHECK': 'prefix-split-placement'}), split.NODEID)
        for value in ('position-grade-effects', split.NODEID, '', 'arbitrary'):
            with self.assertRaises(RuntimeError):
                split.selected_nodeid({'TEMPO_NATIVE_POSITION_CHECK': value})
        record = split.bridge.ExactCase(split.NODEID)
        record.collected = [split.NODEID]
        for phase in ('setup', 'call', 'teardown'):
            record.pytest_runtest_logreport(SimpleNamespace(nodeid=split.NODEID, when=phase, outcome='passed'))
        self.assertTrue(record.passed(0))
        for index in range(3):
            for value in ('skipped', 'failed'):
                record.reports[index]['status'] = value
                self.assertFalse(record.passed(0))
            record.reports[index]['status'] = 'passed'
        for collection in ([], [split.NODEID]*2, [split.bridge.NODEID]):
            record.collected = collection
            self.assertFalse(record.passed(0))

    def test_application_pools_and_environment_restored_on_failure(self):
        calls = []
        postgres = SimpleNamespace(close_pools=lambda: calls.append('close'))
        project = 'tempo-pg-regressions-123-abcdef12'
        dsn = 'postgresql://postgres@postgres:5432/tempo_position_test_' + 'a'*32
        keys = ('TEMPO_DATABASE_WRITE_URL','TEMPO_DATABASE_READ_URL','TEMPO224_ADMIN','TEMPO224_FIXTURE')
        with patch.dict(os.environ, {'TEMPO_DATABASE_WRITE_URL': 'original'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'case failed'):
                with split.application_database(dsn, project, postgres):
                    self.assertEqual(os.environ['TEMPO_DATABASE_WRITE_URL'], dsn)
                    self.assertEqual(os.environ['TEMPO_DATABASE_READ_URL'], dsn)
                    self.assertEqual(os.environ['TEMPO224_ADMIN'], dsn)
                    self.assertEqual(os.environ['TEMPO224_FIXTURE'], 'tempo224-' + project)
                    raise RuntimeError('case failed')
            self.assertEqual(os.environ['TEMPO_DATABASE_WRITE_URL'], 'original')
            self.assertTrue(all(key not in os.environ for key in keys[1:]))
        self.assertEqual(calls, ['close', 'close'])

    def test_foreign_dsn_or_project_rejected_before_pool_or_environment_changes(self):
        calls = []
        postgres = SimpleNamespace(close_pools=lambda: calls.append('close'))
        good = 'postgresql://postgres@postgres:5432/tempo_position_test_'+'a'*32
        for dsn, project in [(good, 'foreign'), (good.replace('@postgres:', '@localhost:'), 'tempo-pg-regressions-123-abcdef12'),
                             (good.replace('tempo_position_test_'+'a'*32, 'tempo'), 'tempo-pg-regressions-123-abcdef12')]:
            with self.assertRaises(RuntimeError):
                with split.application_database(dsn, project, postgres):
                    self.fail('accepted foreign fixture')
        self.assertEqual(calls, [])

    def test_selected_fixture_uses_native_markers_and_keeps_original_assertions(self):
        path = Path(__file__).parent / 'native_review/test_postgres.py'
        tree = ast.parse(path.read_text())
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
            ('split_case','test_native_split_then_refresh_retains_verified_after_four_placement')]
        self.assertEqual(len(selected), 2)
        for function in selected:
            for node in ast.walk(function):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('execute','execute_native'):
                    if node.args and isinstance(node.args[0],ast.Constant):
                        self.assertNotIn('?',node.args[0].value)
        case = next(n for n in selected if n.name != 'split_case')
        self.assertEqual(case.args.args[0].arg, 'split_case')
        self.assertEqual(sum(isinstance(n,ast.Assert) for n in ast.walk(case)), 4)


if __name__ == '__main__':
    unittest.main()
