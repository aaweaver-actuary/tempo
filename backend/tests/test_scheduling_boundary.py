"""Enforce the A1 scheduling seam using Import Linter and its Grimp graph."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import grimp
import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
METADATA = json.loads((REPOSITORY / 'tests/fixtures/scheduling-boundary/ownership.json').read_text())
APPLICATION = METADATA['implementation_module']
CONTRACTS = METADATA['shared_contract_module']
SCHEDULER = 'app.services.scheduler'
CERTIFIED_MODULES = (APPLICATION, CONTRACTS, SCHEDULER)
PUBLIC_TYPES = ('CardState', 'ReviewRecord', 'OpeningSeedFacts', 'MaturityFacts',
                'UpdatedCardState', 'SchedulingReviewResult', 'SchedulingReviewStore')


@pytest.fixture(scope='module')
def dependency_graph():
    return grimp.build_graph('app', include_external_packages=True,
                             exclude_type_checking_imports=False, cache_dir=None)


def lint_imports(backend_directory, configuration=REPOSITORY / '.importlinter'):
    environment = {**os.environ, 'PYTHONPATH': str(backend_directory),
                   'PYTHONDONTWRITEBYTECODE': '1'}
    executable = Path(sys.executable).parent / 'lint-imports'
    return subprocess.run([str(executable), '--config', str(configuration), '--no-cache'],
                          cwd=backend_directory.parent, env=environment,
                          capture_output=True, text=True, timeout=20)


def synthetic_package(tmp_path, additions):
    """Copy only source, never mutate the development or live checkout."""
    backend_directory = tmp_path / 'backend'
    sources = {'app': '', 'app.services': '', 'app.main': '',
               'app.database': '', 'app.services.review_service': '',
               'app.services.scheduling_review_store': ''}
    for module in CERTIFIED_MODULES:
        sources[module] = (REPOSITORY / 'backend' / Path(*module.split('.')).with_suffix('.py')).read_text()
    for module, extra_source in additions.items():
        sources[module] = sources.get(module, '') + '\n' + extra_source + '\n'
    for module, source in sources.items():
        destination = backend_directory.joinpath(*module.split('.'))
        destination = destination / '__init__.py' if module in ('app', 'app.services') else destination.with_suffix('.py')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source)
    return backend_directory


@pytest.mark.parametrize('forbidden_import', [
    'import app.main',
    'import psycopg',
    'def hidden():\n    from app import database',
    'if False:\n    from . import unrelated_analysis',
])
def test_scheduling_boundary_rejects_forbidden_direct_and_deferred_imports(tmp_path, forbidden_import):
    backend_directory = synthetic_package(tmp_path, {
        APPLICATION: forbidden_import, 'app.services.unrelated_analysis': ''})
    completed = lint_imports(backend_directory)
    assert completed.returncode != 0, completed.stdout
    expected_contract = ('Scheduling requires no service infrastructure' if forbidden_import == 'import psycopg'
                         else 'Scheduling imports only its contracts and scheduler')
    assert expected_contract + ' BROKEN' in completed.stdout


def test_scheduling_boundary_rejects_transitive_infrastructure_imports(tmp_path):
    backend_directory = synthetic_package(tmp_path, {
        SCHEDULER: 'from .bridge import value',
        'app.services.bridge': 'from app import database\nvalue = 1'})
    completed = lint_imports(backend_directory)
    assert completed.returncode != 0, completed.stdout
    assert 'app.services.scheduler' in completed.stdout and 'app.database' in completed.stdout


@pytest.mark.parametrize('protected_module', [APPLICATION, CONTRACTS])
def test_scheduling_boundary_rejects_unowned_consumers(tmp_path, protected_module):
    backend_directory = synthetic_package(tmp_path, {
        'app.services.unrelated_analysis': f'import {protected_module}'})
    completed = lint_imports(backend_directory)
    assert completed.returncode != 0, completed.stdout
    assert 'BROKEN' in completed.stdout and 'unrelated_analysis' in completed.stdout


def test_scheduling_boundary_preserves_allowed_edges_and_public_type_identity():
    from app.services import scheduling_review_application, scheduling_review_contracts
    completed = lint_imports(REPOSITORY / 'backend')
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for name in PUBLIC_TYPES:
        assert getattr(scheduling_review_application, name) is getattr(scheduling_review_contracts, name)
    assert {name for name in scheduling_review_contracts.SchedulingReviewStore.__dict__
            if not name.startswith('_')} == {
                'source_review_exists', 'load_card_state', 'append_review',
                'load_maturity_facts', 'save_card_state'}


def test_scheduling_boundary_has_no_unrelated_transitive_dependencies(dependency_graph):
    for module, expected_imports in METADATA['allowed_direct_dependencies'].items():
        assert sorted(dependency_graph.find_modules_directly_imported_by(module)) == sorted(expected_imports), module
    reachable = dependency_graph.find_upstream_modules(APPLICATION)
    assert {module for module in reachable if module.startswith('app.')} == {CONTRACTS, SCHEDULER}
    assert set(reachable) <= set(CERTIFIED_MODULES) | {'__future__', 'dataclasses', 'datetime', 'typing', 'json', 'fsrs', 'tempo_scheduling', 'tempo_learning_runtime'}


@pytest.mark.parametrize('module', CERTIFIED_MODULES)
def test_scheduling_boundary_disallows_dynamic_import_loaders(module):
    source_path = REPOSITORY / 'backend' / Path(*module.split('.')).with_suffix('.py')
    for node in ast.walk(ast.parse(source_path.read_text())):
        if isinstance(node, ast.Name):
            assert node.id not in {'__import__', 'eval', 'exec'}, (module, node.lineno)
        if isinstance(node, ast.Attribute):
            assert node.attr not in {'__import__', 'import_module', 'exec_module', 'load_module'}, (module, node.lineno)


def test_scheduling_boundary_runs_without_application_startup_or_services():
    source = '''
import sys
from importlib.abc import MetaPathFinder
sys.path.insert(0, sys.argv[1] + '/backend')
class RejectInfrastructure(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('app.') and fullname not in {
            'app.services', 'app.services.scheduler',
            'app.services.scheduling_review_application', 'app.services.scheduling_review_contracts'}:
            raise AssertionError('Unexpected application dependency: ' + fullname)
        if fullname.split('.')[0] in {'psycopg','psycopg_pool','redis','celery','fastapi','httpx','sqlite3'}:
            raise AssertionError('Infrastructure dependency: ' + fullname)
sys.meta_path.insert(0, RejectInfrastructure())
def reject_services(event, arguments):
    if event in {'socket.connect', 'socket.bind', 'sqlite3.connect'}:
        raise AssertionError('Service initialization: ' + event)
sys.addaudithook(reject_services)
import pytest
raise SystemExit(pytest.main([sys.argv[1] + '/backend/tests/test_scheduling_review_application.py',
    '-q', '-p', 'no:cacheprovider', '--rootdir=' + sys.argv[1]]))
'''
    completed = subprocess.run([sys.executable, '-I', '-B', '-c', source, str(REPOSITORY)],
                              env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                                   'PYTHONDONTWRITEBYTECODE': '1'},
                              capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '13 passed' in completed.stdout


def ownership_selection(changed_module, dependency_graph):
    """Module-local demonstration only; does not authorize or modify CI."""
    if changed_module == METADATA['unrelated']['module']:
        assert APPLICATION not in dependency_graph.find_downstream_modules(changed_module)
        return set(METADATA['unrelated']['tests'])
    selected = set(METADATA['owning_tests'])
    if changed_module == CONTRACTS:
        consumers = dependency_graph.find_downstream_modules(changed_module)
        for consumer, tests in METADATA['consumers'].items():
            assert consumer in consumers, consumer
            selected.update(tests)
    else:
        assert changed_module == APPLICATION
    return selected


def test_scheduling_module_change_selects_owning_tests(dependency_graph):
    assert ownership_selection(APPLICATION, dependency_graph) == set(METADATA['owning_tests'])


def test_unrelated_module_change_excludes_scheduling_tests(dependency_graph):
    selected = ownership_selection(METADATA['unrelated']['module'], dependency_graph)
    assert selected == set(METADATA['unrelated']['tests'])
    assert selected.isdisjoint(METADATA['owning_tests'])


def test_scheduling_contract_change_expands_consumer_qualification(dependency_graph):
    selected = ownership_selection(CONTRACTS, dependency_graph)
    assert selected == set(METADATA['owning_tests']) | {
        test for tests in METADATA['consumers'].values() for test in tests}
    assert set(METADATA['owning_tests']) < selected
    for file in selected | set(METADATA['native_boundaries']['browser_specs']):
        assert (REPOSITORY / file).is_file(), file


def test_scheduling_boundary_tools_are_declared_for_canonical_uv_setup():
    """The mandated uv sync must install direct imports used by regular tests."""
    project = tomllib.loads((REPOSITORY / 'backend/pyproject.toml').read_text())
    lock = tomllib.loads((REPOSITORY / 'backend/uv.lock').read_text())
    required_tools = {'import-linter': '2.15', 'grimp': '3.17'}
    development_requirements = set(project['dependency-groups']['dev'])
    ci_requirements = set((REPOSITORY / 'backend/requirements-test.txt').read_text().splitlines())
    backend_package = next(package for package in lock['package'] if package['name'] == 'backend')
    locked_development_names = {dependency['name'] for dependency in backend_package['dev-dependencies']['dev']}
    for name, version in required_tools.items():
        assert f'{name}=={version}' in development_requirements, name
        assert f'{name}=={version}' in ci_requirements, name
        assert name in locked_development_names, name
        assert any(package['name'] == name and package['version'] == version for package in lock['package']), name


@pytest.mark.parametrize('package_name,exports,dependencies', [
    ('scheduling', ['Schedule', 'schedule_review', 'unlock_ready'], ['fsrs']),
    ('chess-core', ['canonical_fen_key', 'card_id'], []),
])
@pytest.mark.parametrize('forbidden_source', [
    'import fastapi',
    'def hidden():\n    import app.main',
    'if False:\n    import celery',
    'from . import bridge',
])
def test_pure_package_checker_rejects_direct_lazy_conditional_and_transitive_imports(
        tmp_path, package_name, exports, dependencies, forbidden_source):
    """Use D's existing checker; mutate only a disposable copy of owned source."""
    import shutil
    package_root = REPOSITORY / 'packages' / package_name
    isolated = tmp_path / package_name
    shutil.copytree(package_root / 'src', isolated / 'src')
    shutil.copyfile(package_root / 'pyproject.toml', isolated / 'pyproject.toml')
    import_root = next((isolated / 'src').iterdir())
    entry = import_root / '__init__.py'
    entry.write_text(entry.read_text() + '\n' + forbidden_source + '\n')
    if forbidden_source == 'from . import bridge':
        (import_root / 'bridge.py').write_text('import app.database\n')
    completed = subprocess.run([sys.executable, str(REPOSITORY / 'tools/component-build/check-python.py'),
        str(isolated), json.dumps(dependencies), json.dumps(exports)],
        capture_output=True, text=True, timeout=20)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert 'undeclared dependency' in completed.stderr
