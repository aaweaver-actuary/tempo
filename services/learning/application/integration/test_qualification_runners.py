"""Named runner regressions; no PostgreSQL cluster is needed for these proofs."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as xml

import pytest

APPLICATION = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location('learning_qualification', APPLICATION/'qualification.py')
qualification = importlib.util.module_from_spec(specification)
specification.loader.exec_module(qualification)


@pytest.mark.parametrize('options', ['-k ordinary_review_sequence', '-k no_such_required_identity',
    '-m selected', '--deselect=tests/test_example.py::test_one', '--ignore=tests',
    '--ignore-glob=*', '--maxfail=1', '-x', '-p injected_plugin'])
@pytest.mark.parametrize('native', [False, True])
def test_full_qualification_rejects_inherited_partial_selection(options, native):
    with pytest.raises(ValueError, match='rejects inherited'):
        qualification.junit_destination(options, allow_junit=native)


@pytest.mark.parametrize('option', ['--junitxml', '--junit-xml'])
@pytest.mark.parametrize('separator', ['=', ' '])
def test_native_preserves_external_junit_destination_with_spaces(tmp_path, option, separator):
    destination = tmp_path/'external results.xml'
    assert qualification.junit_destination(option+separator+repr(str(destination)), allow_junit=True) == destination


@pytest.mark.parametrize('options', ['--junitxml', '--junitxml=', '--junitxml --junitxml=a',
    '--junitxml=a --junit-xml=b', '--junitxml=a -k one', '--junitxml=a --deselect=case', '"'])
def test_native_rejects_invalid_reporting_or_reporting_with_selection(options):
    with pytest.raises(ValueError):
        qualification.junit_destination(options, allow_junit=True)


def test_qualifier_rejects_selectors_before_build_and_native_before_docker(tmp_path):
    for runner in (APPLICATION/'qualify.py', APPLICATION.parents[2]/'database/tempo_legacy_database/tooling/learning_review/run_postgres.py'):
        environment = dict(os.environ, PYTEST_ADDOPTS='-k no_such_required_identity', PATH='')
        result = subprocess.run([sys.executable, str(runner), str(tmp_path/'evidence')],
                                env=environment, capture_output=True, text=True)
        assert result.returncode != 0
        assert 'rejects inherited PYTEST_ADDOPTS' in result.stderr
        assert not (tmp_path/'evidence').exists()


def test_full_qualification_removes_external_plugin_and_environment_injection():
    environment = qualification.controlled_environment({'PYTEST_ADDOPTS':'-k one',
        'PYTEST_PLUGINS':'injected_plugin', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'0',
        'PYTHONHOME':'/foreign', 'KEEP':'yes'})
    assert 'PYTEST_ADDOPTS' not in environment
    assert 'PYTEST_PLUGINS' not in environment
    assert 'PYTHONHOME' not in environment
    assert environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
    assert environment['KEEP'] == 'yes'


def receipts(tmp_path):
    identities = ['test_example.py::test_one', 'test_example.py::test_two']
    collection = dict(collected=identities, deselected=[], collection_errors=[], reports=[], exit_code=0)
    execution = copy.deepcopy(collection)
    execution['reports'] = [dict(nodeid=identity, phase=phase, outcome='passed')
        for identity in identities for phase in ('setup','call','teardown')]
    junit = tmp_path/'cases.xml'
    junit.write_text('<testsuite><testcase classname="test_example" name="test_one"/>'
                     '<testcase classname="test_example" name="test_two"/></testsuite>')
    return collection, execution, junit


def test_full_qualification_requires_exact_complete_normal_inventory(tmp_path):
    collection, execution, junit = receipts(tmp_path)
    assert qualification.validate_inventory(collection, execution, junit) == collection['collected']


@pytest.mark.parametrize('defect', ['missing','unexpected','duplicate_collection','duplicate_execution',
    'deselected','empty','collection_error','nonzero','missing_phase','duplicate_phase',
    'skipped','failed','setup_error','teardown_error','junit_missing','junit_duplicate','junit_skipped','junit_failed','junit_error'])
def test_full_qualification_rejects_missing_skipped_failed_or_duplicate_required_cases(tmp_path, defect):
    collection, execution, junit = receipts(tmp_path)
    if defect == 'missing': execution['collected'].pop()
    elif defect == 'unexpected': execution['collected'].append('test_example.py::test_three')
    elif defect == 'duplicate_collection': collection['collected'].append(collection['collected'][0])
    elif defect == 'duplicate_execution': execution['collected'].append(execution['collected'][0])
    elif defect == 'deselected': execution['deselected'].append('test_example.py::test_two')
    elif defect == 'empty': collection['collected'] = []
    elif defect == 'collection_error': collection['collection_errors'].append('collection failed')
    elif defect == 'nonzero': execution['exit_code'] = 1
    elif defect == 'missing_phase': execution['reports'].pop()
    elif defect == 'duplicate_phase': execution['reports'].append(execution['reports'][0])
    elif defect in {'skipped','failed','setup_error','teardown_error'}:
        index = {'skipped':1,'failed':1,'setup_error':0,'teardown_error':2}[defect]
        execution['reports'][index]['outcome'] = 'skipped' if defect == 'skipped' else 'failed'
    else:
        tree = xml.parse(junit); root=tree.getroot()
        if defect == 'junit_missing': root.remove(root[-1])
        elif defect == 'junit_duplicate': root[-1].attrib.update(root[0].attrib)
        else: xml.SubElement(root[0], {'junit_skipped':'skipped','junit_failed':'failure','junit_error':'error'}[defect])
        tree.write(junit)
    with pytest.raises(RuntimeError): qualification.validate_inventory(collection, execution, junit)


def test_actual_controlled_collection_execution_and_external_junit_ignore_plugin_injection(tmp_path):
    (tmp_path/'test_example.py').write_text('def test_one(): pass\ndef test_two(): pass\n')
    (tmp_path/'pytest.ini').write_text('[pytest]\n')
    environment = qualification.controlled_environment(dict(os.environ, PYTEST_PLUGINS='no_such_plugin'))
    collection = tmp_path/'collected.json'; execution = tmp_path/'executed.json'; junit=tmp_path/'external.xml'
    arguments = [sys.executable, str(APPLICATION/'qualification.py')]
    flags = ['-c',str(tmp_path/'pytest.ini'),'-o','addopts=','-p','no:cacheprovider',str(tmp_path/'test_example.py')]
    for receipt, options in ((collection,['--collect-only','-q']), (execution,['--junitxml='+str(junit)])):
        result=subprocess.run([*arguments,str(receipt),*flags,*options],cwd=tmp_path,env=environment,capture_output=True,text=True)
        assert result.returncode == 0, result.stdout+result.stderr
    identities=qualification.validate_inventory(json.loads(collection.read_text()),json.loads(execution.read_text()),junit)
    assert identities == ['test_example.py::test_one','test_example.py::test_two']


# These exercise the registered commands, rather than only the option parser.
@pytest.mark.parametrize('selector', [
    "-k 'ordinary_review_sequence or full_qualification_requires_exact_complete_normal_inventory'",
    '-k no_such_required_identity', '-m no_such_marker', '--deselect=required',
    '--ignore=tests', '--ignore-glob=*', '--maxfail=1', '-x'])
@pytest.mark.parametrize('suite_index', [0, 1])
def test_registered_component_python_suites_cannot_accept_partial_selection(tmp_path, selector, suite_index):
    import shlex
    root = APPLICATION.parents[2]
    component = next(component for component in json.loads((root/'components.json').read_text())['components']
                     if component['id'] == 'learning-review-application')
    suite = component['tests'][suite_index]
    command = shlex.split(suite['command'])
    command[0] = sys.executable
    junit = tmp_path/'external.xml'
    environment = dict(os.environ, PYTEST_ADDOPTS=selector, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result = subprocess.run([*command, '--junitxml='+str(junit)],
        cwd=root/suite.get('cwd', component['root']), env=environment, capture_output=True, text=True)
    assert result.returncode != 0, result.stdout+result.stderr
    assert 'rejects inherited PYTEST_ADDOPTS' in result.stderr
    assert not junit.exists()


def load_runner(monkeypatch, filename):
    monkeypatch.setitem(sys.modules, 'qualification', qualification)
    path = APPLICATION/filename
    specification = importlib.util.spec_from_file_location('learning_runner_'+path.stem, path)
    runner = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(runner)
    return runner


@pytest.mark.parametrize('option', ['--junitxml', '--junit-xml'])
@pytest.mark.parametrize('separator', ['=', ' '])
def test_component_wrapper_runs_complete_fixture_and_preserves_external_junit(monkeypatch, tmp_path, option, separator):
    runner = load_runner(monkeypatch, 'run_suite.py')
    fixture = tmp_path/'test_small.py'
    fixture.write_text('def test_one(): pass\ndef test_two(): pass\n')
    monkeypatch.setattr(runner, 'suite_inputs', lambda suite:(tmp_path, [fixture]))
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    monkeypatch.setenv('PYTEST_PLUGINS', 'plugin_that_must_not_load')
    monkeypatch.setenv('TEMPO_LEARNING_EVIDENCE_DIR', str(tmp_path/'evidence'))
    junit = tmp_path/'external results.xml'
    arguments = [option+'='+str(junit)] if separator == '=' else [option,str(junit)]
    assert runner.main(['application', *arguments]) == 0
    assert len(list(xml.parse(junit).iter('testcase'))) == 2
    collections = list((tmp_path/'evidence').rglob('collected.json'))
    executions = list((tmp_path/'evidence').rglob('executed.json'))
    assert len(collections) == len(executions) == 1
    identities = qualification.validate_inventory(json.loads(collections[0].read_text()),
        json.loads(executions[0].read_text()), junit)
    assert identities == ['test_small.py::test_one', 'test_small.py::test_two']


@pytest.mark.parametrize('arguments', [
    ['application','--junitxml=a','--junit-xml=b'], ['application','-k','one'],
    ['application','--junitxml='], ['application','--unknown'], ['not-a-suite']])
def test_component_wrapper_rejects_unknown_arguments_and_duplicate_junit(monkeypatch, arguments):
    runner = load_runner(monkeypatch, 'run_suite.py')
    with pytest.raises(SystemExit) as caught:
        runner.main(arguments)
    assert caught.value.code != 0


@pytest.mark.parametrize('outcome', ['skip','failure','setup_error','teardown_error'])
def test_component_wrapper_fails_on_nonpassing_required_fixture(monkeypatch, tmp_path, outcome):
    runner = load_runner(monkeypatch, 'run_suite.py')
    fixture = tmp_path/'test_small.py'
    contents = 'import pytest\ndef test_one(): pass\n'
    if outcome == 'skip': contents += 'def test_two(): pytest.skip("required")\n'
    elif outcome == 'failure': contents += 'def test_two(): assert False\n'
    elif outcome == 'setup_error': contents += '@pytest.fixture\ndef broken(): raise RuntimeError("setup")\ndef test_two(broken): pass\n'
    else: contents += '@pytest.fixture\ndef broken():\n    yield\n    raise RuntimeError("teardown")\ndef test_two(broken): pass\n'
    fixture.write_text(contents)
    monkeypatch.setattr(runner, 'suite_inputs', lambda suite:(tmp_path, [fixture]))
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    monkeypatch.setenv('TEMPO_LEARNING_EVIDENCE_DIR', str(tmp_path/'evidence'))
    assert runner.main(['application','--junitxml='+str(tmp_path/'external.xml')]) != 0


def test_atomic_summary_replacement_never_exposes_partial_json(monkeypatch, tmp_path):
    destination = tmp_path/'summary.json'
    destination.write_text('{"complete":true}\n')
    original = destination.read_text()
    def fail_replace(*args): raise OSError('injected replacement failure')
    monkeypatch.setattr(qualification.os, 'replace', fail_replace)
    with pytest.raises(OSError): qualification.write_json(destination, {'complete':False})
    assert destination.read_text() == original
    assert list(tmp_path.iterdir()) == [destination]
