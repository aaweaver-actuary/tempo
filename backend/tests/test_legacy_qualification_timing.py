"""Standard-library supervisor regressions; no processes, installs or Docker.

Collected by the regular backend pytest owner and runnable alone with unittest.
"""
import importlib.util
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('timing_bridge', 'qualify_legacy_bridge.py')
backend = load('timing_backend', 'qualify_legacy_backend.py')


class Child:
    pid = 41234
    returncode = None

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.waits = []

    def wait(self, timeout):
        self.waits.append(timeout)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException): raise outcome
        self.returncode = outcome
        return outcome


class TimingReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.receipt = self.root / 'inside.json'
        self.report = dict(complete=False, commands=[])
        self.timer = runner.Deadline(40, self.report, receipt=self.receipt)

    def saved(self):
        return json.loads(self.receipt.read_text())

    def test_start_and_running_receipts_precede_spawn_and_wait(self):
        child = Child([0])
        def spawn(*_args, **options):
            self.assertTrue(options['start_new_session'])
            receipt = self.saved()
            self.assertFalse(receipt['complete'])
            self.assertEqual(receipt['pending_stage'], 'game-evidence-execution')
            command = receipt['commands'][0]
            self.assertEqual(command['state'], 'pending')
            self.assertTrue(command['started_at'].endswith('+00:00'))
            self.assertNotIn('process_group', command)
            return child
        original_wait = child.wait
        def wait(timeout):
            command = self.saved()['commands'][0]
            self.assertEqual((command['state'], command['process_group']), ('running', child.pid))
            return original_wait(timeout)
        child.wait = wait
        with patch.object(runner.subprocess, 'Popen', spawn):
            self.assertEqual(self.timer.run(['synthetic'], log=self.root / 'game-evidence-execution.log'), 0)
        receipt = self.saved()
        self.assertNotIn('pending_stage', receipt)
        command = receipt['commands'][0]
        self.assertEqual((command['state'], command['exit_code']), ('completed', 0))
        self.assertGreaterEqual(command['seconds'], 0)
        self.assertTrue(command['completed_at'].endswith('+00:00'))
        self.assertFalse(receipt['complete'])  # Command success cannot qualify the gate.

    def test_timeout_retains_running_stage_before_term_and_failed_result_after_reap(self):
        child = Child([subprocess.TimeoutExpired('synthetic', 30), -15])
        signals = []
        def kill(pid, signum):
            self.assertEqual(self.saved()['commands'][0]['state'], 'running')
            signals.append((pid, signum))
        with patch.object(runner.subprocess, 'Popen', return_value=child), patch.object(runner.os, 'killpg', kill):
            with self.assertRaises(subprocess.TimeoutExpired): self.timer.run(['synthetic'])
        command = self.saved()['commands'][0]
        self.assertEqual((command['state'], command['error'], command['exit_code']), ('failed', 'TimeoutExpired', -15))
        self.assertEqual(signals, [(child.pid, signal.SIGTERM)])
        self.assertEqual(child.waits[-1], 5)

    def test_interrupt_retains_error_and_unchanged_term_kill_reap_sequence(self):
        child = Child([KeyboardInterrupt(), subprocess.TimeoutExpired('synthetic', 5), -9])
        with patch.object(runner.subprocess, 'Popen', return_value=child), patch.object(runner.os, 'killpg') as kill:
            with self.assertRaises(KeyboardInterrupt): self.timer.run(['synthetic'])
        self.assertEqual(kill.call_args_list[0].args, (child.pid, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (child.pid, signal.SIGKILL))
        self.assertEqual(child.waits[-2:], [5, 5])
        self.assertEqual(self.saved()['commands'][0]['error'], 'KeyboardInterrupt')

    def test_failed_wrapper_is_reaped_before_failed_completion_is_saved(self):
        child = Child([1])
        def kill(*_args): self.assertEqual(self.saved()['commands'][0]['state'], 'running')
        with patch.object(runner.subprocess, 'Popen', return_value=child), patch.object(runner.os, 'killpg', kill):
            with self.assertRaises(RuntimeError): self.timer.run(['synthetic'])
        self.assertEqual(self.saved()['commands'][0]['state'], 'failed')

    def test_spawn_failure_is_durable_without_inventing_a_process(self):
        with patch.object(runner.subprocess, 'Popen', side_effect=OSError('synthetic')), patch.object(runner.os, 'killpg') as kill:
            with self.assertRaises(OSError): self.timer.run(['synthetic'])
        command = self.saved()['commands'][0]
        self.assertEqual(command['error'], 'OSError')
        self.assertNotIn('process_group', command)
        kill.assert_not_called()

    def test_expired_budget_persists_failure_without_spawning(self):
        self.timer.end = 0
        with patch.object(runner.subprocess, 'Popen') as spawn:
            with self.assertRaises(TimeoutError): self.timer.run(['synthetic'])
        spawn.assert_not_called()
        self.assertEqual(self.saved()['commands'][0]['error'], 'TimeoutError')

    def test_receipt_io_consumes_the_original_work_budget(self):
        now = [0.0]
        original_write = runner.write
        def write(*args):
            original_write(*args)
            now[0] += 2
        child = Child([0])
        with patch.object(runner.time, 'monotonic', side_effect=lambda: now[0]), patch.object(runner, 'write', write), \
                patch.object(runner.subprocess, 'Popen', return_value=child):
            runner.Deadline(40, self.report, receipt=self.receipt).run(['synthetic'])
        self.assertEqual(child.waits, [26.0])  # Two receipt writes, same ten-second reap reserve.

    def test_post_spawn_receipt_failure_still_reaps_the_child(self):
        original_write = runner.write
        calls = [0]
        def write(*args):
            calls[0] += 1
            if calls[0] == 2: raise OSError('synthetic receipt failure')
            original_write(*args)
        child = Child([-15])
        with patch.object(runner, 'write', write), patch.object(runner.subprocess, 'Popen', return_value=child), \
                patch.object(runner.os, 'killpg') as kill:
            with self.assertRaises(OSError): self.timer.run(['synthetic'])
        kill.assert_called_once_with(child.pid, signal.SIGTERM)
        self.assertEqual(child.waits, [5])
        self.assertEqual(self.saved()['commands'][0]['error'], 'OSError')

    def test_cleanup_timer_remains_independent_of_receipt_io(self):
        child = Child([0])
        with patch.object(runner, 'write', side_effect=AssertionError('cleanup attempted receipt I/O')), \
                patch.object(runner.subprocess, 'Popen', return_value=child):
            self.assertEqual(runner.Deadline(300, {}).run(['synthetic-cleanup']), 0)

    def test_control_capture_does_not_add_secret_arguments_to_receipts(self):
        child = type('Capture', (), dict(pid=41234, returncode=0, communicate=lambda self, timeout: ('', '')))()
        with patch.object(runner.subprocess, 'Popen', return_value=child):
            self.timer.capture(['synthetic-control', '--password', 'synthetic-private-value'])
        self.assertEqual(self.report['commands'], [])
        self.assertFalse(self.receipt.exists())

    def test_both_helper_entrypoints_install_interrupt_handlers_before_dispatch(self):
        for module, arguments in ((runner, ['--inside', 'artifacts']), (backend, ['--inside'])):
            with self.subTest(module=module.__name__):
                handlers = {}
                def inside(*_args):
                    self.assertIn(signal.SIGTERM, handlers)
                    self.assertIn(signal.SIGINT, handlers)
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                with patch.object(module.signal, 'signal', side_effect=lambda sig, handler: handlers.update({sig: handler})), \
                        patch.object(module, 'inside', inside), patch.object(sys, 'argv', ['qualifier', *arguments]):
                    with self.assertRaises(KeyboardInterrupt): module.main()


class NativePrerequisiteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def backend_wheels(self):
        staging = runner.staging
        directory = self.root / 'work/source/backend-wheels'
        site = self.root / 'work/environment/lib/python3.12/site-packages'
        directory.mkdir(parents=True)
        site.mkdir(parents=True)
        for package, version in staging.BACKEND_WHEELS.items():
            files = {package + '/__init__.py': b'CURRENT_SOURCE=True\n',
                     package + '-' + version + '.dist-info/METADATA': ('Version: ' + version + '\n').encode()}
            with zipfile.ZipFile(directory / (package + '-' + version + '-py3-none-any.whl'), 'w') as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
                    path = site / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return directory, site

    def test_exact_network_not_found_is_absence_but_other_identity_or_errors_are_not(self):
        for code, diagnostic, expected in [
            (1, 'Error response from daemon: network owned-network not found\n', True),
            (1, 'network owned-network not found', True),
            (0, 'network owned-network not found', False),
            (1, 'Error response from daemon: network another-owned-network not found', False),
            (1, 'Error response from daemon: network owned-network-extra not found', False),
            (1, 'network owned-network permission denied', False),
            (1, 'daemon unavailable for owned-network', False),
        ]:
            with self.subTest(diagnostic=diagnostic, code=code):
                result = subprocess.CompletedProcess([], code, '', diagnostic)
                self.assertEqual(runner.verified_absence(result, 'owned-network'), expected)

    def test_three_backend_dependencies_survive_tracked_staging_and_artifact_copy(self):
        source = self.root / 'repository'; source.mkdir()
        files = set(runner.staging.SOURCE_FILES)
        files.update(package + '/src/owned/__init__.py' for package in runner.BACKEND_DEPENDENCIES)
        files.add('unrelated/src/private.py')
        for name in files:
            path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('tracked ' + name)
        stage = runner.staging.OwnedStaging('owned-dependency-test', 'current-head', base=self.root)
        stage.stage_source(source, sorted(files), runner.SOURCE_PACKAGES)
        restricted = self.root / 'restricted'
        runner.copy_artifact_source(stage.source, restricted)
        for package in runner.BACKEND_DEPENDENCIES:
            name = package + '/src/owned/__init__.py'
            self.assertEqual((restricted / name).read_bytes(), (source / name).read_bytes())
        self.assertFalse((restricted / 'unrelated').exists())

    def test_current_dependency_wheels_verify_every_installed_file(self):
        self.backend_wheels()
        result = runner.staging.verify_backend_environment(self.root)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(value['verified_files'] == 2 for value in result.values()))

    def test_same_version_older_installed_dependency_is_rejected(self):
        _, site = self.backend_wheels()
        (site / 'tempo_learning_runtime/__init__.py').write_text('OLDER_IMAGE=True\n')
        with self.assertRaisesRegex(ValueError, 'differs from verified wheel'):
            runner.staging.verify_backend_environment(self.root)

    def test_missing_or_extra_dependency_wheels_are_rejected(self):
        directory, _ = self.backend_wheels()
        extra = directory / 'unreviewed-1.0-py3-none-any.whl'; extra.write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'Exactly three'):
            runner.staging.verify_backend_environment(self.root)
        extra.unlink()
        next(directory.glob('tempo_learning_runtime-*.whl')).unlink()
        with self.assertRaisesRegex(ValueError, 'Exactly three'):
            runner.staging.verify_backend_environment(self.root)

    def test_native_refuses_receipts_without_current_dependency_proof(self):
        wheels = self.root / 'wheels'; wheels.mkdir()
        artifacts = {}
        for index in range(12):
            name = 'synthetic-' + str(index) + '.whl'
            (wheels / name).write_bytes(b'synthetic owned artifact')
            artifacts[name] = hashlib.sha256((wheels / name).read_bytes()).hexdigest()
        host = dict(complete=True, cleanup_verified=True, source_head='current-head', image='pinned',
                    plan=dict(artifact_test_total=373))
        (self.root / 'host.json').write_text(json.dumps(host))
        (self.root / 'inside.json').write_text(json.dumps(dict(complete=True, artifacts=artifacts)))
        with patch.object(backend.shared.staging, 'qualified_inputs'), \
                patch.object(backend.shared.staging, 'verify_backend_environment', return_value={'current': 'proof'}):
            with self.assertRaisesRegex(ValueError, 'dependency wheel/install proof missing'):
                backend.qualified_artifacts(self.root, 'current-head', 'pinned')


if __name__ == '__main__':
    unittest.main()
