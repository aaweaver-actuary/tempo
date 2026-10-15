"""Controlled pytest execution and exact inventory proof for Learning runners."""
from collections import Counter
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import xml.etree.ElementTree as xml


def junit_destination(options, *, allow_junit=False):
    """Reject ambient options; native tooling may route one structured receipt."""
    arguments = shlex.split(options)
    destination = None
    index = 0
    while index < len(arguments):
        option = arguments[index]
        name, separator, value = option.partition('=')
        if not allow_junit or name not in {'--junitxml', '--junit-xml'}:
            raise ValueError('Full qualification rejects inherited PYTEST_ADDOPTS: ' + option)
        if destination is not None:
            raise ValueError('Only one inherited JUnit destination is supported')
        if not separator:
            index += 1
            value = arguments[index] if index < len(arguments) else ''
        if not value or value.startswith('-'):
            raise ValueError('JUnit destination must be a nonempty path')
        destination = Path(value).resolve()
        index += 1
    return destination


def controlled_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    for key in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONHOME'):
        result.pop(key, None)
    result['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    result['PYTHONDONTWRITEBYTECODE'] = '1'
    return result


def write_json(destination, value):
    """Replace a machine-readable summary without exposing a partial write."""
    destination = Path(destination)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=destination.parent, delete=False) as temporary:
            temporary_name = temporary.name
            json.dump(value, temporary, indent=2)
            temporary.write('\n')
        os.replace(temporary_name, destination)
    finally:
        if temporary_name and Path(temporary_name).exists():
            Path(temporary_name).unlink()


class Inventory:
    def __init__(self):
        self.collected = []
        self.deselected = []
        self.reports = []
        self.collection_errors = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_deselected(self, items):
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(str(report.longrepr))

    def pytest_runtest_logreport(self, report):
        self.reports.append({'nodeid': report.nodeid, 'phase': report.when,
                             'outcome': report.outcome})


def validate_inventory(collection, execution, junit):
    for label, receipt in (('collection', collection), ('execution', execution)):
        identities = receipt['collected']
        if receipt['exit_code'] or receipt['collection_errors'] or receipt['deselected']:
            raise RuntimeError(f'{label}: unsuccessful or deselected required inventory')
        if not identities or len(set(identities)) != len(identities):
            raise RuntimeError(f'{label}: empty or duplicate required identities')
    expected = set(collection['collected'])
    actual = set(execution['collected'])
    if actual != expected:
        raise RuntimeError(f'Required identities differ: missing={sorted(expected-actual)}, unexpected={sorted(actual-expected)}')
    phases = Counter((report['nodeid'], report['phase']) for report in execution['reports'])
    required = Counter((identity, phase) for identity in expected for phase in ('setup', 'call', 'teardown'))
    if phases != required:
        raise RuntimeError('Required execution phases missing, unexpected or duplicated')
    if any(report['outcome'] != 'passed' for report in execution['reports']):
        raise RuntimeError('Required test setup/call/teardown failed or skipped')
    cases = list(xml.parse(junit).iter('testcase'))
    if len(cases) != len(expected) or any(case.find(tag) is not None for case in cases for tag in ('failure', 'error', 'skipped')):
        raise RuntimeError('JUnit does not contain the complete passing required inventory')
    junit_identities = [(case.get('classname'), case.get('name')) for case in cases]
    if len(set(junit_identities)) != len(junit_identities):
        raise RuntimeError('JUnit contains duplicate required identities')
    return sorted(expected)


def main():
    import pytest
    destination = Path(sys.argv[1])
    inventory = Inventory()
    exit_code = int(pytest.main(sys.argv[2:], plugins=[inventory]))
    destination.write_text(json.dumps({**vars(inventory), 'exit_code': exit_code}, indent=2)+'\n')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
