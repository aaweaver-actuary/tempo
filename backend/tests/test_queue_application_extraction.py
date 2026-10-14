"""#220: pinned queue parity and application dependency ownership."""

import ast
from contextlib import contextmanager
from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app import database
from app.services import postgres_queue_refresh, queue_application, queue_ordering, study_mode
from test_queue_attempt_issuance import queued_attempts

APP_ROOT = Path(__file__).resolve().parents[1] / 'app'
FIXTURES = Path(__file__).parent / 'fixtures'
FORBIDDEN = {'app.main', 'app.tasks', 'app.background_worker', 'app.celery_app', 'app.queue_commands'}


def application_imports(module_name, *, include_deferred=True):
    module_path = APP_ROOT.parent / (module_name.replace('.', '/') + '.py')
    if not module_path.exists():
        return set()
    module_tree = ast.parse(module_path.read_text())
    dependencies = set()
    if not include_deferred:
        # Unrelated database initialization/maintenance functions are not queue calls.
        module_tree.body = [node for node in module_tree.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for node in ast.walk(module_tree):
        if isinstance(node, ast.Import):
            dependencies.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_module = importlib.util.resolve_name('.' * node.level + (node.module or ''), module_name.rpartition('.')[0]) if node.level else node.module
            dependencies.add(imported_module)
            dependencies.update(imported_module + '.' + alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            loader_name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            if loader_name in {'import_module', '__import__'} and node.args and isinstance(node.args[0], ast.Constant):
                target = node.args[0].value
                if target.startswith('.'):
                    package = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else module_name.rpartition('.')[0]
                    target = importlib.util.resolve_name(target, package)
                dependencies.add(target)
    return {dependency for dependency in dependencies if dependency.startswith('app.')}


def test_queue_application_import_does_not_load_http_or_worker_root():
    pending = ['app.services.queue_application', 'app.services.queue_ordering',
               'app.services.postgres_queue_refresh', 'app.services.study_attempts',
               'app.services.opening_ranking_snapshot']
    owned_modules = set(pending)
    visited = set()
    while pending:
        module_name = pending.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        assert module_name not in FORBIDDEN, module_name
        # Documented broker publication adapter, not queue application orchestration.
        # Its lazy Celery import is required for post-commit wake and durable recovery.
        if module_name == 'app.services.queue_refresh_wakeup':
            continue
        pending.extend(application_imports(module_name, include_deferred=module_name in owned_modules) - visited)
    import_probe = "from app.services import queue_application, queue_ordering, postgres_queue_refresh; import sys; assert not (set(sys.modules) & " + repr(FORBIDDEN) + ')'
    subprocess.run([sys.executable, '-c', import_probe], check=True,
                   env={**os.environ, 'PYTHONPATH': str(APP_ROOT.parent)})


@pytest.mark.parametrize('fixture', json.loads((FIXTURES / 'queue-extraction-220.json').read_text())['ordering'])
def test_queue_ordering_matches_pinned_pre_extraction_fixture(fixture):
    seed, membership_hash, ordered = queue_ordering._plan_daily_queue_order(
        fixture['rows'], fixture['day'], fixture['saved'],
        preserve_through_entry_id=fixture['preserve_through_entry_id'],
    )
    assert (seed, membership_hash, [row['id'] for row in ordered]) == (
        fixture['seed'], fixture['membership_hash'], fixture['ordered_ids'],
    )


def test_queue_application_sql_matches_pinned_pre_extraction_literals():
    frozen_sql = json.loads((FIXTURES / 'queue-extraction-220-sql.json').read_text())
    found = {}
    for node in ast.parse((APP_ROOT / 'services/queue_application.py').read_text()).body:
        name = node.name if isinstance(node, ast.FunctionDef) else node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else ''
        if name in frozen_sql:
            found[name] = [literal.value for literal in ast.walk(node)
                           if isinstance(literal, ast.Constant) and isinstance(literal.value, str)
                           and any(keyword in literal.value.upper() for keyword in ('SELECT ', 'INSERT ', 'UPDATE ', 'DELETE '))]
    assert found == frozen_sql


@pytest.mark.parametrize('after,expected_cards', [(None, ['later-b', 'active-a', 'active-a']),
                                                 (0, ['active-a', 'later-b', 'active-a']),
                                                 (99, ['later-b', 'active-a', 'active-a'])])
def test_queue_application_requeue_preserves_origin_cycle_and_issuance(queued_attempts, after, expected_cards):
    day = date.today().isoformat()
    with database.connection() as owned_database:
        owned_database.execute('DELETE FROM daily_queue WHERE id=?', (queued_attempts['active-a'],))
        # Historical origin remains the cycle authority after projection deletion.
        first_repeat = queue_application.requeue(owned_database, day, 'active-a', None, 'failed')
        repeated = queue_application.requeue(owned_database, day, 'active-a', after, 'fresh')
        row = owned_database.execute('SELECT cycle,admission_kind,attempt_state FROM daily_queue WHERE id=?', (repeated,)).fetchone()
        assert tuple(row) == (2, 'review', 'fresh')
        assert owned_database.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=?', (repeated,)).fetchone()[0] == 0
        # Connection remains owned by the caller and changes roll back together.
        owned_database.execute('SAVEPOINT rollback_repeat')
        rolled_back = queue_application.requeue(owned_database, day, 'active-a', None, 'fresh')
        owned_database.execute('ROLLBACK TO SAVEPOINT rollback_repeat')
        assert not owned_database.execute('SELECT 1 FROM daily_queue WHERE id=?', (rolled_back,)).fetchone()
        assert first_repeat != repeated
        assert [row[0] for row in owned_database.execute('SELECT card_id FROM daily_queue ORDER BY position,id')] == expected_cards


@pytest.mark.parametrize('legacy_phase,next_phase', [('prioritized_openings', 'admit_study'),
                                                    ('prioritized_opening_item', 'admit_study'),
                                                    ('real_game_misses', 'publish_projection'),
                                                    ('randomize_queue', 'publish_projection')])
def test_essential_queue_study_only_resumes_every_legacy_phase(monkeypatch, legacy_phase, next_phase):
    captured = {}
    opening_page_reads = []
    def bounded_opening_read(sql, parameters=(), *, native=False):
        assert native
        assert parameters == ('2026-10-12', '2026-10-12')
        assert 'SELECT EXISTS' in sql
        opening_page_reads.append(sql)
        return [(False,)]  # Exhausted allowance: the bounded page resumes admit_study.
    monkeypatch.setattr(postgres_queue_refresh, '_bounded_read', bounded_opening_read)
    monkeypatch.setattr(postgres_queue_refresh.postgres_store, 'configured', lambda: True)
    @contextmanager
    def owned_connection(**kwargs):
        assert kwargs == {'background': True}
        yield object()
    monkeypatch.setattr(postgres_queue_refresh, 'connection', owned_connection)
    monkeypatch.setattr(postgres_queue_refresh, 'lock_current_slice', lambda database, task: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, 'wait_for_foreground', lambda: None)
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(postgres_queue_refresh, '_prepare_study_admission', lambda day: None)
    monkeypatch.setattr(postgres_queue_refresh, '_prepare_opening_quarantine', lambda *args: {'invalid': None, 'has_more': False, 'after_entry_id': 17})
    for optional in ('_prepare_prioritized_openings', '_admit_one_prioritized_opening', '_prepare_real_game_obligation', '_prepare_queue_randomization'):
        monkeypatch.setattr(postgres_queue_refresh, optional, lambda *args, **kwargs: pytest.fail('Optional phase executed'))
    def advance(database, task, **kwargs):
        captured.update(kwargs)
        return True
    monkeypatch.setattr(postgres_queue_refresh, 'advance_task_slice_in_transaction', advance)
    payload = {'queue_date': '2026-10-12', '_queue_phase': legacy_phase,
               'preserve_through_entry_id': 42, 'quarantine_started': True,
               'opening_plan': [{'card_id': 'old-card', 'repertoire_id': 'rep', 'reason': None,
                                 'capacity_route': {'parent_card_id': 'parent'}}], 'opening_index': 0}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice({'payload': payload}) is True
    assert captured['next_phase'] == next_phase
    assert captured['next_payload']['preserve_through_entry_id'] == 42
    assert captured['next_payload']['queue_date'] == payload['queue_date']
    assert len(opening_page_reads) == int(legacy_phase.startswith('prioritized_opening'))
    if opening_page_reads:
        assert captured['next_payload']['study_opening_counts']['stop_reason'] == 'daily_allowances_exhausted'
        assert captured['next_payload']['study_opening_cursor'] == []
        assert captured['next_payload']['study_opening_upper'] == []
        assert 'opening_plan' not in captured['next_payload']
    else:
        assert captured['next_payload']['quarantine_started'] is True
        assert captured['next_payload']['after_entry_id'] == 17


def test_queue_extraction_preserves_frozen_capacity_proposal_checkpoint(monkeypatch):
    proposal = {'repertoire_id': 'rep', 'card_id': 'child', 'reason': None,
                'capacity_route': {'repertoire_id': 'rep', 'parent_card_id': 'parent',
                                   'parent_introduced': True, 'generation': 11,
                                   'line_id': 'line', 'decision_index': 1,
                                   'earlier_unreviewed_card_ids': [],
                                   'current_ready_publication': True,
                                   'structurally_valid': True, 'single_decision': True}}
    payload = {'queue_date': '2026-10-12', '_queue_phase': 'prioritized_opening_item',
               'opening_plan': [proposal], 'opening_index': 0, 'preserve_through_entry_id': 42}
    captured = {}
    admitted = []
    @contextmanager
    def owned_connection(**kwargs):
        yield object()
    monkeypatch.setattr(postgres_queue_refresh.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(postgres_queue_refresh, 'connection', owned_connection)
    monkeypatch.setattr(postgres_queue_refresh, 'lock_current_slice', lambda database, task: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, 'wait_for_foreground', lambda: None)
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
    monkeypatch.setattr(postgres_queue_refresh, '_admit_one_prioritized_opening',
                        lambda database, day, planned: admitted.append(planned))
    def advance(database, task, **kwargs):
        captured.update(kwargs)
        return True
    monkeypatch.setattr(postgres_queue_refresh, 'advance_task_slice_in_transaction', advance)
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice({'payload': payload})
    assert admitted == [proposal]
    assert captured['next_payload'] == {**payload, '_queue_phase': 'admit_study', 'opening_index': 1}
    assert json.loads(json.dumps(captured['next_payload']))['opening_plan'] == [proposal]


@pytest.fixture(autouse=True)
def no_frozen_publication_in_legacy_extraction_fixtures(monkeypatch, request):
    # This file owns legacy orchestration fixtures; actual order adapters have
    # separate real-connection regressions in test_frozen_opening_order.py.
    from app.services import frozen_opening_order
    monkeypatch.setattr(frozen_opening_order, 'activation', lambda database, **kwargs: (None, 0))
    if 'test_essential_queue_study_only_resumes_every_legacy_phase' in request.node.name or 'test_queue_extraction_preserves_frozen_capacity_proposal_checkpoint' in request.node.name:
        @contextmanager
        def frozen_metadata_reader():
            yield object()
        monkeypatch.setattr(postgres_queue_refresh, 'background_read_connection', frozen_metadata_reader)
