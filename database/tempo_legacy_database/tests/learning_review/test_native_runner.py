"""Existing mocked native lifecycle proofs; no real cluster is created."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
APPLICATION = ROOT / "services/learning/application"
RUNNER = ROOT / "database/tempo_legacy_database/tooling/learning_review/run_postgres.py"
specification = importlib.util.spec_from_file_location(
    "learning_runner_policy_proofs", APPLICATION / "integration/test_qualification_runners.py")
policy = importlib.util.module_from_spec(specification)
specification.loader.exec_module(policy)
load_runner, qualification = policy.load_runner, policy.qualification


class NativeHarness:
    """Docker and pytest fault injection; never creates a real cluster."""
    def __init__(self, directory):
        self.directory = directory
        self.mode = 'success'
        self.created = False
        self.identity = None
        self.calls = []
        self.test_calls = []
        self.markers_during_cleanup = []

    def docker(self, *arguments, check=True):
        from types import SimpleNamespace
        self.calls.append(arguments)
        if arguments[:2] == ('image','inspect'):
            if self.mode == 'image_failure': raise RuntimeError('image inspection failed')
            return SimpleNamespace(stdout='fixture-image\n', returncode=0)
        if arguments[:2] == ('container','ls'):
            if self.created:
                self.markers_during_cleanup.append(json.loads((self.directory/'native.json').read_text())['complete'])
                if self.mode == 'inspection_failure': raise RuntimeError('daemon inspection failed')
            return SimpleNamespace(stdout='fixture-container\n' if self.created or self.mode == 'collision' else '', returncode=0)
        if arguments[0] == 'run':
            self.identity = arguments[arguments.index('--name')+1]
            self.created = self.mode != 'start_without_creation'
            if self.mode in {'start_failure','start_without_creation','start_and_remove_failure'}:
                raise RuntimeError('startup failed')
            return SimpleNamespace(stdout='fixture-container\n', returncode=0)
        if arguments[0] == 'inspect':
            if self.mode == 'label_inspection_failure': raise RuntimeError('label inspection failed')
            labels = {'tempo.owner':self.identity, 'tempo.disposable':'true'}
            if self.mode == 'ownership_mismatch': labels['tempo.owner'] = 'another-task'
            if self.mode == 'disposable_mismatch': labels['tempo.disposable'] = 'false'
            return SimpleNamespace(stdout=json.dumps(labels), returncode=0)
        if arguments[0] == 'rm':
            if self.mode in {'remove_failure','start_and_remove_failure'}: raise RuntimeError('removal failed')
            if self.mode != 'verification_failure': self.created = False
            return SimpleNamespace(stdout='removed', returncode=0)
        raise AssertionError(arguments)

    def pytest(self, arguments, **options):
        from types import SimpleNamespace
        self.test_calls.append(arguments)
        collection = {'collected':['database/tempo_legacy_database/tests/learning_review/test_postgres.py::test_required'],
                      'deselected':[], 'collection_errors':[], 'reports':[], 'exit_code':0}
        collecting = '--collect-only' in arguments
        if collecting and self.mode == 'collection_failure':
            collection.update(exit_code=2, collection_errors=['collection failed'])
        if not collecting:
            collection['reports'] = [{'nodeid':collection['collected'][0],'phase':phase,
                'outcome':'skipped' if self.mode == 'skip' and phase == 'call' else 'passed'}
                for phase in ('setup','call','teardown')]
            junit = Path(next(arg.split('=',1)[1] for arg in arguments if arg.startswith('--junitxml=')))
            junit.write_text('<testsuite><testcase classname="database.tempo_legacy_database.tests.learning_review.test_postgres" name="test_required">'
                             + ('<skipped/>' if self.mode == 'skip' else '')+'</testcase></testsuite>')
        Path(arguments[2]).write_text(json.dumps(collection))
        return SimpleNamespace(stdout='fixture pytest\n', stderr='', returncode=collection['exit_code'])



def native_harness(monkeypatch, tmp_path):
    from types import SimpleNamespace
    runner = load_runner(monkeypatch, RUNNER)
    harness = NativeHarness(tmp_path/'evidence')
    monkeypatch.setattr(runner, 'docker', harness.docker)
    monkeypatch.setattr(runner, 'provision_owned_cluster', lambda identity,password:{})
    monkeypatch.setattr(runner, 'subprocess', SimpleNamespace(run=harness.pytest,
        check_output=lambda *args,**options:'fixture-revision\n'))
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    return runner, harness



def test_native_success_is_published_only_after_verified_cleanup(monkeypatch, tmp_path):
    runner, harness = native_harness(monkeypatch, tmp_path)
    external = tmp_path/'external results.xml'
    monkeypatch.setenv('PYTEST_ADDOPTS', '--junit-xml='+repr(str(external)))
    assert runner.main([str(harness.directory)]) == 0
    report = json.loads((harness.directory/'native.json').read_text())
    assert report['complete'] and report['pytest_exit_code'] == report['exit_code'] == 0
    assert report['resource']['container_removed'] and report['resource']['container_absent']
    assert harness.markers_during_cleanup and not any(harness.markers_during_cleanup)
    assert external.read_bytes() == (harness.directory/'native.xml').read_bytes()
    assert external.read_bytes() == (Path(report['run_directory'])/'native.xml').read_bytes()



@pytest.mark.parametrize('mode,expected_removal_attempt,expected_absence', [
    ('start_failure',True,True), ('start_without_creation',False,True),
    ('image_failure',False,None), ('collision',False,None),
    ('ownership_mismatch',False,None), ('disposable_mismatch',False,None),
    ('inspection_failure',False,None), ('label_inspection_failure',False,None),
    ('remove_failure',True,None), ('verification_failure',True,None)])
def test_native_failure_paths_cleanup_only_verified_owned_resources(monkeypatch, tmp_path, mode, expected_removal_attempt, expected_absence):
    runner, harness = native_harness(monkeypatch, tmp_path)
    harness.mode = mode
    assert runner.main([str(harness.directory)]) != 0
    report = json.loads((harness.directory/'native.json').read_text())
    resource = json.loads((harness.directory/'native-resource.json').read_text())
    assert not report['complete'] and resource['exit_code'] != 0
    assert any(call[0]=='rm' for call in harness.calls) is expected_removal_attempt
    assert resource['container_absent'] is expected_absence
    if mode in {'ownership_mismatch','disposable_mismatch','inspection_failure','label_inspection_failure','remove_failure','verification_failure'}:
        assert not resource['cleanup_verified'] and report['cleanup_error']



def test_native_preserves_startup_and_cleanup_errors_together(monkeypatch, tmp_path):
    runner, harness = native_harness(monkeypatch, tmp_path)
    harness.mode = 'start_and_remove_failure'
    assert runner.main([str(harness.directory)]) == 1
    report = json.loads((harness.directory/'native.json').read_text())
    assert 'startup failed' in report['error'] and 'removal failed' in report['cleanup_error']



@pytest.mark.parametrize('mode', ['skip','collection_failure','start_failure','remove_failure','image_failure','reject_options'])
def test_native_failed_retry_invalidates_previous_success_and_preserves_history(monkeypatch, tmp_path, mode):
    runner, harness = native_harness(monkeypatch, tmp_path)
    assert runner.main([str(harness.directory)]) == 0
    previous = json.loads((harness.directory/'native.json').read_text())
    harness.mode = mode
    calls_before = len(harness.calls)
    if mode == 'reject_options': monkeypatch.setenv('PYTEST_ADDOPTS', '-k no_such_required_identity')
    assert runner.main([str(harness.directory)]) == 1
    current = json.loads((harness.directory/'native.json').read_text())
    resource = json.loads((harness.directory/'native-resource.json').read_text())
    assert not current['complete'] and current['exit_code'] == resource['exit_code'] == 1
    assert current['identity'] != previous['identity']
    assert json.loads((Path(previous['run_directory'])/'native.json').read_text()) == previous
    archived = list((harness.directory/'history').glob('*/native.json'))
    assert len(archived) == 1 and json.loads(archived[0].read_text()) == previous
    if mode in {'skip','remove_failure'}:
        assert current['pytest_exit_code'] == 0
    if mode == 'reject_options':
        assert len(harness.calls) == calls_before
    else:
        assert current['run_directory'] != previous['run_directory']
