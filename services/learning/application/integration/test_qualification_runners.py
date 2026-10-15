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
    for runner in (APPLICATION/'qualify.py', APPLICATION/'integration/run_postgres.py'):
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
