"""Independent controls preserve study and durable optional work."""
import pytest
from fastapi.testclient import TestClient
from app import database, main, tasks
from app.services import durable_tasks, study_mode
from app.services.database_executor import database_writer
from test_queue_attempt_issuance import queued_attempts

@pytest.fixture(autouse=True)
def active_writer(queued_attempts):
    database_writer.start()
    yield
    database_writer.stop()


def test_restoration_controls_persist_and_old_settings_writes_preserve_flags(queued_attempts):
    client = TestClient(main.app)
    saved = client.put('/api/settings', json={'repertoire_preparation_enabled': False,
        'optional_analysis_and_imports_enabled': False})
    assert saved.status_code == 200, saved.text
    assert saved.json()['repertoire_preparation_enabled'] is False
    assert saved.json()['optional_analysis_and_imports_enabled'] is False
    database.initialize()
    assert client.put('/api/settings', json={'initial_depth': 8}).status_code == 200
    result = client.get('/api/settings').json()
    assert result['repertoire_preparation_enabled'] is False
    assert result['optional_analysis_and_imports_enabled'] is False


def test_restoration_controls_pause_raced_work_preserve_essential_and_resume_original_generation(queued_attempts, monkeypatch):
    optional = durable_tasks.enqueue_task('opening_graph_rebuild', 'restoration-preserved', {'cursor': 7}, priority=-1000)
    claimed = durable_tasks.claim_task(kind='opening_graph_rebuild')
    client = TestClient(main.app)
    assert client.put('/api/settings', json={'optional_analysis_and_imports_enabled': False}).status_code == 200
    monkeypatch.setattr(tasks, 'execute_postgres_opening_graph_slice', lambda task: pytest.fail('Paused optional work ran'))
    assert tasks._execute_claimed_background_slice(claimed, None) is False
    assert durable_tasks.claim_task(kind='opening_graph_rebuild') is None
    essential = durable_tasks.enqueue_task('daily_queue', 'restoration-essential', {}, priority=-200)
    assert durable_tasks.claim_task(kind='daily_queue')['id'] == essential['id']
    database.initialize()
    assert client.put('/api/settings', json={'optional_analysis_and_imports_enabled': True, 'study_only_mode': True}).status_code == 200
    assert durable_tasks.claim_task(kind='opening_graph_rebuild') is None
    assert client.put('/api/settings', json={'study_only_mode': False}).status_code == 200
    with database.connection() as settings_database:
        settings_database.execute("UPDATE background_tasks SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE id=?", (optional['id'],))
    resumed = durable_tasks.claim_task(kind='opening_graph_rebuild')
    assert resumed['id'] == optional['id']
    assert resumed['generation'] == optional['generation']
    assert resumed['payload']['cursor'] == 7


@pytest.mark.parametrize("owned_connection", [False, True])
def test_restoration_pause_reads_one_snapshot_during_atomic_control_switch(queued_attempts, monkeypatch, owned_connection):
    """Both committed rows pause work; a mixed pair must never authorize it."""
    from contextlib import contextmanager
    with database.connection() as writer:
        writer.execute('UPDATE settings SET study_only_mode=0, optional_analysis_and_imports_enabled=0 WHERE id=1')
    queries = []
    acquisitions = []
    original_reader = database.read_connection

    class InterleavedSettingsReader:
        def __init__(self, reader):
            self.reader = reader

        def execute(self, sql):
            queries.append(sql)
            # Materialize the actual first read before an independent writer commits.
            row = self.reader.execute(sql).fetchone()
            if "optional_analysis_and_imports_enabled" in sql or len(queries) == (1 if owned_connection else 2):
                with database.connection() as writer:
                    writer.execute('UPDATE settings SET study_only_mode=1, optional_analysis_and_imports_enabled=1 WHERE id=1')
            class Result:
                def fetchone(self):
                    return row
            return Result()

    @contextmanager
    def interleaved_reader():
        acquisitions.append(True)
        with original_reader() as reader:
            yield InterleavedSettingsReader(reader)

    monkeypatch.setattr(study_mode, 'read_connection', interleaved_reader)
    if owned_connection:
        with interleaved_reader() as reader:
            paused = study_mode.optional_work_paused(reader)
    else:
        paused = study_mode.optional_work_paused()
    assert paused is True
    assert len(acquisitions) == 1
    assert queries == ['SELECT study_only_mode, optional_analysis_and_imports_enabled FROM settings WHERE id=1']
    with original_reader() as reader:
        assert tuple(reader.execute('SELECT study_only_mode, optional_analysis_and_imports_enabled FROM settings WHERE id=1').fetchone()) == (1, 1)
