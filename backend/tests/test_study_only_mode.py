"""Emergency isolation preserves foreground study and resumable durable work."""

from datetime import date
import pytest
from fastapi.testclient import TestClient
from tempo_legacy_database import connections as database
from app import main, tasks
from app.services import durable_tasks, study_mode
from tempo_legacy_database.adapters.database_executor import database_writer
from test_queue_attempt_issuance import queued_attempts


@pytest.fixture(autouse=True)
def active_writer(queued_attempts):
    database_writer.start()
    yield
    database_writer.stop()


def enable_mode():
    with database.connection() as settings_database:
        settings_database.execute('UPDATE settings SET study_only_mode=1 WHERE id=1')


def test_study_only_toggle_persists_and_omitted_field_preserves_value(queued_attempts):
    client = TestClient(main.app)
    assert client.get('/api/settings').json()['study_only_mode'] is False
    assert client.put('/api/settings', json={'study_only_mode': True}).status_code == 200
    database.initialize()
    assert client.get('/api/settings').json()['study_only_mode'] is True
    assert client.put('/api/settings', json={'initial_depth': 8}).status_code == 200
    assert client.get('/api/settings').json()['study_only_mode'] is True
    assert client.put('/api/settings', json={'study_only_mode': False}).status_code == 200
    assert client.get('/api/settings').json()['study_only_mode'] is False


def test_study_only_mode_preserves_foreground_review_and_queue_progression(queued_attempts):
    enable_mode()
    client = TestClient(main.app)
    first = client.get('/api/queue/window?limit=2')
    assert first.status_code == 200, first.text
    assert first.json()['cards'][0]['id'] == 'active-a'
    review = {'outcome': 'correct', 'queue_entry_id': queued_attempts['active-a'],
              'expected_revision': 1, 'attempt_id': 'study-only-foreground-review'}
    saved = client.post('/api/cards/active-a/review', json=review)
    assert saved.status_code == 200, saved.text
    assert saved.json()['persisted'] is True
    next_queue = client.get('/api/queue/window?limit=1')
    assert next_queue.status_code == 200, next_queue.text
    assert next_queue.json()['cards'][0]['id'] == 'later-b'


def test_study_only_mode_pauses_optional_work_and_resumes_after_restart(queued_attempts):
    optional = durable_tasks.enqueue_task('opening_graph_rebuild', 'mode-optional', {}, priority=-100)
    enable_mode()
    assert durable_tasks.claim_task(kind='opening_graph_rebuild') is None
    database.initialize()
    assert study_mode.study_only_enabled()
    with database.connection() as settings_database:
        settings_database.execute('UPDATE settings SET study_only_mode=0 WHERE id=1')
    resumed = durable_tasks.claim_task(kind='opening_graph_rebuild')
    assert resumed['id'] == optional['id']
    assert resumed['generation'] == optional['generation']


def test_study_only_mode_preserves_receipts_and_idempotent_replay(queued_attempts):
    enable_mode()
    client = TestClient(main.app)
    assert client.get('/api/queue/window').status_code == 200
    review = {'outcome': 'correct', 'queue_entry_id': queued_attempts['active-a'],
              'expected_revision': 1, 'attempt_id': 'study-only-idempotent-review'}
    first = client.post('/api/cards/active-a/review', json=review)
    assert first.status_code == 200, first.text
    database.initialize()
    second = client.post('/api/cards/active-a/review', json=review)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    with database.read_connection() as settings_database:
        assert settings_database.execute("SELECT COUNT(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1


def test_study_only_mode_releases_raced_optional_delivery_without_running_handler(queued_attempts, monkeypatch):
    optional = durable_tasks.enqueue_task('opening_graph_rebuild', 'raced-mode-optional', {})
    claimed = durable_tasks.claim_task(kind='opening_graph_rebuild')
    enable_mode()
    monkeypatch.setattr(tasks, 'execute_postgres_opening_graph_slice',
                        lambda task: pytest.fail('Paused optional handler ran'))
    assert tasks._execute_claimed_background_slice(claimed, None) is False
    with database.read_connection() as settings_database:
        preserved = settings_database.execute('SELECT generation,attempt_count,payload_json,state FROM background_tasks WHERE id=?',
                                              (optional['id'],)).fetchone()
    assert preserved['generation'] == optional['generation']
    assert preserved['attempt_count'] == 0
    assert preserved['payload_json'] == '{}'
    assert preserved['state'] == 'retrying'


def test_study_only_mode_allows_daily_queue_claim_and_blocks_engine_preparation(queued_attempts, monkeypatch):
    current_queue = durable_tasks.enqueue_task('daily_queue', 'mode-daily', {'queue_date': date.today().isoformat()})
    enable_mode()
    assert durable_tasks.claim_task(kind='daily_queue')['id'] == current_queue['id']
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(main, 'read_operation', lambda *args, **kwargs: {'state': 'unknown'})
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda database=None: True)
    from app import game_analysis_commands
    monkeypatch.setattr(game_analysis_commands, 'prepare_position_claim',
                        lambda *args: pytest.fail('Paused expensive position preparation ran'))
    assert main.claim_game_analysis_position(None, 'docker', 'mode-claim') == {
        'job': None, 'paused_reason': 'study_only_mode'}
