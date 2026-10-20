"""Connection-facing consumers bind to composition, never policy with a cursor."""
import ast
import importlib.util
import os
from contextlib import contextmanager
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))


def imports(path):
    result = {}
    for node in ast.walk(ast.parse((ROOT / path).read_text())):
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                result[name.asname or name.name] = (node.module, name.name)
    return result


def test_native_recovery_consumer_binds_database_operations_to_feedback_adapter():
    bound = imports('scripts/check_postgres_queue_attempt_recovery.py')
    assert bound['MISS_REASON'] == ('app.services.real_game_feedback', 'MISS_REASON')
    for name in ('promote_real_game_card', 'has_outstanding_real_game_miss'):
        assert bound[name] == ('app.services.real_game_feedback_adapter', name)


def test_native_review_consumer_uses_canonical_writer_and_feedback_composition():
    bound = imports('services/learning/application/integration/test_postgres.py')
    assert bound['ensure_card_queued_after'] == ('app.services.review_service', 'ensure_card_queued_after')
    assert bound['prioritize_queued_misses'] == ('app.services.real_game_feedback_adapter', 'prioritize_queued_misses')
    assert bound['MISS_REASON'] == ('app.services.real_game_feedback', 'MISS_REASON')
    assert 'real_game_feedback' not in bound


def test_issuance_regression_imports_existing_real_game_fixture_helpers():
    path = 'backend/tests/test_queue_attempt_issuance.py'
    bound = imports(path)
    for name in ('_seed_game', '_seed_repertoire'):
        module, imported = bound[name]
        assert imported == name and (ROOT / 'backend/tests' / (module + '.py')).is_file()


@pytest.mark.parametrize('name,args', [
    ('has_outstanding_real_game_miss', ('card',)),
    ('promote_real_game_card', ('card', 'day')),
    ('clear_satisfied_miss_priority', ('card',)),
])
def test_connection_entry_wraps_borrowed_database_before_policy(monkeypatch, name, args):
    from app.services import real_game_feedback_adapter as composition
    database, store = object(), object()
    made, calls = [], []
    def factory(value):
        made.append(value)
        return store
    def operation(value, *arguments):
        calls.append((value, arguments))
        return True
    monkeypatch.setattr(composition, 'BorrowedFeedbackStore', factory)
    monkeypatch.setattr(composition.policy, name, operation)
    result = getattr(composition, name)(database, *args)
    assert made == [database] and calls == [(store, args)]
    if name != 'clear_satisfied_miss_priority':
        assert result is True


def test_event_entry_supplies_admission_day_and_semantic_store(monkeypatch):
    from app.services import real_game_feedback_adapter as composition
    database, store, calls = object(), object(), []
    monkeypatch.setattr(composition, 'BorrowedFeedbackStore', lambda value: store if value is database else None)
    monkeypatch.setattr(composition, 'date', SimpleNamespace(today=lambda: SimpleNamespace(isoformat=lambda: 'original-day')))
    monkeypatch.setattr(composition.policy, 'prioritize_real_game_miss', lambda *args: calls.append(args) or True)
    assert composition.prioritize_real_game_miss(database, 'event') is True
    assert calls == [(store, 'event', 'original-day')]


def test_batch_composition_closes_snapshot_before_short_individual_transactions(monkeypatch):
    from app.services import real_game_feedback_adapter as composition
    calls, databases = [], []
    @contextmanager
    def connection(*, background):
        value = object(); databases.append(value); calls.append(('open', value, background))
        try:
            yield value
        finally:
            calls.append(('close', value))
    class Store:
        def __init__(self, database): self.database = database
        def missed_event_batches(self, game_id):
            calls.append(('snapshot', self.database, game_id))
            yield ('a',)
            yield ('b',)
    monkeypatch.setattr(composition, 'connection', connection)
    monkeypatch.setattr(composition, 'BorrowedFeedbackStore', Store)
    monkeypatch.setattr(composition.activity_gate, 'wait_for_foreground', lambda: calls.append(('yield',)))
    monkeypatch.setattr(composition, 'prioritize_real_game_miss', lambda database, event: calls.append(('apply', database, event)))
    composition.apply_real_game_misses('game', background=True)
    read, first, second = databases
    assert calls == [('open', read, True), ('snapshot', read, 'game'), ('close', read),
                     ('yield',), ('open', first, True), ('apply', first, 'a'), ('close', first),
                     ('yield',), ('open', second, True), ('apply', second, 'b'), ('close', second)]


def backend_policy_import_errors(relative, source):
    parts = Path(relative).parts
    if parts[0] == 'backend':
        package = '.'.join(parts[1:-1])
    elif 'src' in parts:
        package = '.'.join(parts[parts.index('src') + 1:-1])
    else:
        package = '.'.join(parts[:-1])
    errors = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ''
        if node.level:
            module = importlib.util.resolve_name('.' * node.level + module, package)
        if module == 'app.services.real_game_feedback' and {name.name for name in node.names} - {'MISS_REASON'}:
            errors.append((node.lineno, module))
        if module == 'app.services' and any(name.name == 'real_game_feedback' for name in node.names):
            if relative not in {'backend/tests/test_real_game_feedback_policy.py',
                                'backend/app/services/real_game_feedback_adapter.py'}:
                errors.append((node.lineno, module))
    return errors


@pytest.mark.parametrize('path,source,bad', [
    ('backend/app/services/consumer.py', 'from .real_game_feedback import promote_real_game_card', True),
    ('backend/app/services/consumer.py', 'from .real_game_feedback import MISS_REASON', False),
    ('scripts/native.py', 'from app.services.real_game_feedback import has_outstanding_real_game_miss', True),
    ('services/learning/runtime/src/tempo_learning_runtime/admission.py',
     'from .real_game_feedback import has_outstanding_real_game_miss', False),
])
def test_consumer_guard_resolves_backend_relative_imports_without_conflating_runtime(path, source, bad):
    assert bool(backend_policy_import_errors(path, source)) is bad


def test_repository_connection_consumers_do_not_import_semantic_policy_operations():
    # Includes scripts/integration packages and resolves backend-relative imports;
    # the independent runtime's same-named module remains a separate owner.
    for directory in ('backend', 'scripts', 'services', 'tools', 'packages', 'contracts'):
        for folder, children, filenames in os.walk(ROOT / directory):
            children[:] = [name for name in children if not name.startswith('.')
                           and name not in {'node_modules', '__pycache__', 'build', 'dist'}]
            for filename in filenames:
                if not filename.endswith('.py'):
                    continue
                path = Path(folder) / filename
                source = path.read_text()
                if 'real_game_feedback' not in source:
                    continue
                relative = path.relative_to(ROOT).as_posix()
                assert not backend_policy_import_errors(relative, source), relative
