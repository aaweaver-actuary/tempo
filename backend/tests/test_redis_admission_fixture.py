"""Focused lifecycle regressions; native SQL semantics run in the PG durability gate."""
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
import sys

import pytest


@pytest.fixture
def redis_fixture():
    specification = importlib.util.spec_from_file_location(
        'redis_fixture', Path(__file__).resolve().parents[2] / 'database/tests/check_postgres_redis_admission.py')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_redis_fixture_cleanup_explicitly_removes_non_cascading_receipts(redis_fixture, monkeypatch):
    statements = []
    snapshot = {'marker': 'tempo-redis-admission-fixture', 'version': 2,
                'identity': 'redis-admission-00000000-0000-0000-0000-000000000000',
                'settings_before': {'id': 1, 'study_only_mode': 0},
                'settings_installed': {'id': 1, 'study_only_mode': 1},
                'queue_date': '2000-01-01', 'displaced': [],
                'projection_before': None, 'projection_installed': {'queue_date': '2000-01-01'}}

    snapshot['owned_queue_entries'] = [dict(zip(redis_fixture.QUEUE_OWNERSHIP_COLUMNS,
        (index + 1, card_id, snapshot['queue_date'], 0, snapshot['identity'])))
        for index, card_id in enumerate(redis_fixture.card_ids(snapshot['identity']))]

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def transaction(self):
            return nullcontext()

        def execute(self, statement, parameters=()):
            rendered = statement.as_string() if hasattr(statement, 'as_string') else statement
            statements.append(rendered)
            self.statement = rendered
            return self

        def fetchall(self):
            if 'SELECT id,card_id,queue_date,cycle,admission_repertoire_id' in self.statement:
                return [tuple(entry[column] for column in redis_fixture.QUEUE_OWNERSHIP_COLUMNS) for entry in snapshot['owned_queue_entries']]
            if 'SELECT id,repertoire_id FROM cards' in self.statement:
                return [(card_id, snapshot['identity']) for card_id in redis_fixture.card_ids(snapshot['identity'])]
            return []

        def fetchone(self):
            if 'source_name' in self.statement:
                return ('tempo-redis-admission-fixture:' + json.dumps(snapshot),)
            if 'settings' in self.statement:
                return (snapshot['settings_installed'],)
            if 'queue_projections' in self.statement:
                return (snapshot['projection_installed'],)
            return None

    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    monkeypatch.setenv('TEMPO_TEST_COMPOSE_PROJECT', 'tempo-pg-regressions-123-abcd')
    monkeypatch.setattr(redis_fixture.psycopg, 'connect', lambda *args, **kwargs: Database())
    monkeypatch.setattr(sys, 'argv', ['fixture', 'cleanup', snapshot['identity'],
                                    json.dumps({'previous_mode': 0, 'displaced': []})])
    redis_fixture.main()
    for table in ['review_attempt_receipts', 'review_schedule_snapshots', 'queue_attempt_origins', 'operation_receipts']:
        assert any(f'DELETE FROM {table}' in statement for statement in statements), table


@pytest.mark.parametrize('conflict', ['mode', 'created_settings', 'projection', 'missing_entry', 'reordered_entry'])
def test_redis_fixture_restoration_conflicts_are_explicit(redis_fixture, conflict):
    saved = {'settings_before': {'id': 1, 'study_only_mode': 0},
             'settings_installed': {'id': 1, 'study_only_mode': 1},
             'projection_installed': {'generation': 7}, 'displaced': [[10, 0, 2]]}
    settings, projection, positions = {'id': 1, 'study_only_mode': 1}, {'generation': 7}, {10: 2}
    if conflict == 'mode':
        settings['study_only_mode'] = 0
    elif conflict == 'created_settings':
        saved['settings_before'] = None
        settings['new_cards_per_day'] = 12
    elif conflict == 'projection':
        projection['generation'] = 8
    elif conflict == 'missing_entry':
        positions.clear()
    else:
        positions[10] = 3
    with pytest.raises(RuntimeError, match='restoration conflict'):
        redis_fixture.validate_restoration(saved, settings, projection, positions)


def test_redis_fixture_unchanged_preexisting_settings_fields_are_preserved(redis_fixture):
    saved = {'settings_before': {'id': 1, 'study_only_mode': 0},
             'settings_installed': {'id': 1, 'study_only_mode': 1},
             'projection_installed': None, 'displaced': []}
    redis_fixture.validate_restoration(saved, {'id': 1, 'study_only_mode': 1, 'new_cards_per_day': 17}, None, {})


def test_redis_fixture_never_reports_success_before_commit(redis_fixture, monkeypatch, capsys):
    class CommitFailure:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            raise RuntimeError('commit failed')

    monkeypatch.setattr(redis_fixture, 'configure', lambda: None)
    monkeypatch.setattr(redis_fixture, 'seed_fixture', lambda *_: {'identity': 'owned'})
    monkeypatch.setattr(redis_fixture.psycopg, 'connect', lambda *args, **kwargs: CommitFailure())
    monkeypatch.setattr(sys, 'argv', ['fixture', 'seed', 'owned'])
    with pytest.raises(RuntimeError, match='commit failed'):
        redis_fixture.main()
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('environment', [
    {'TEMPO_TEST_INSTANCE': 'production'},
    {'TEMPO_TEST_INSTANCE': 'disposable', 'TEMPO_TEST_COMPOSE_PROJECT': 'tempo'},
    {'TEMPO_TEST_INSTANCE': 'disposable', 'TEMPO_TEST_COMPOSE_PROJECT': 'tempo-issue225-abcd'},
    {'TEMPO_TEST_INSTANCE': 'disposable', 'TEMPO_TEST_COMPOSE_PROJECT': 'tempo-pg-regressions-1-abcd',
     'TEMPO_REDIS_ADMISSION_TEST_DSN': 'postgresql://other/tempo'},
])
def test_redis_fixture_rejects_unowned_or_custom_database_before_connect(redis_fixture, monkeypatch, environment):
    for key in ['TEMPO_TEST_INSTANCE', 'TEMPO_TEST_COMPOSE_PROJECT', 'TEMPO_REDIS_ADMISSION_TEST_DSN']:
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(redis_fixture.psycopg, 'connect', lambda *_: pytest.fail('must reject before connecting'))
    with pytest.raises(RuntimeError):
        redis_fixture.main()
