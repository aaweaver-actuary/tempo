"""Named visibility regressions: clearing never deletes work or hides new work."""
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from app import database
from app.main import app
from app.services.durable_tasks import enqueue_task
from app.services.database_executor import database_writer


@pytest.fixture
def activity_client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'activity.db')
    database.initialize()
    database_writer.start()
    try: yield TestClient(app)
    finally: database_writer.stop()


def finish(task, *, paused=False):
    now=datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        db.execute("UPDATE background_tasks SET state='complete',completed_at=?,updated_at=? WHERE id=?", (now,now,task['id']))
        if paused:
            db.execute("INSERT INTO background_activity(source,work_id,paused,updated_at) VALUES('durable',?,1,?)", (task['id'],now))


def test_clear_finished_cross_device_cutoff_retains_concurrent_completion_failures_and_pauses(activity_client):
    first=enqueue_task('activity_fixture','finished',{});finish(first)
    paused=enqueue_task('activity_fixture','paused',{});finish(paused,paused=True)
    failed=enqueue_task('activity_fixture','failed',{})
    with database.connection() as db: db.execute("UPDATE background_tasks SET state='failed',last_error='Repair required' WHERE id=?", (failed['id'],))
    snapshot=activity_client.get('/api/system/activity').json()
    assert snapshot['clearable_finished']==1
    later=enqueue_task('activity_fixture','later',{});finish(later)
    response=activity_client.post('/api/system/activity/clear-finished',json={key:snapshot[key] for key in ('completion_cutoff','completion_snapshot')},headers={'Idempotency-Key':'clear-snapshot'})
    assert response.status_code==200, response.text
    other=TestClient(app).get('/api/system/activity').json()
    classifications={item['id']:item['classification'] for item in other['items']}
    assert classifications[first['id']]=='history'
    assert classifications[later['id']]=='finished'
    assert classifications[paused['id']]=='paused'
    assert classifications[failed['id']]=='needs_attention'
    with database.read_connection() as db: assert db.execute('SELECT COUNT(*) FROM background_tasks').fetchone()[0]==4


def test_clear_finished_rejects_fabricated_or_future_snapshot_and_new_generation_remains_visible(activity_client):
    task=enqueue_task('activity_fixture','generation',{});finish(task)
    snapshot=activity_client.get('/api/system/activity').json()
    invalid=activity_client.post('/api/system/activity/clear-finished',json={'completion_cutoff':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'completion_snapshot':'invented'})
    assert invalid.status_code==409
    assert activity_client.post('/api/system/activity/clear-finished',json={key:snapshot[key] for key in ('completion_cutoff','completion_snapshot')}).status_code==200
    replacement=enqueue_task('activity_fixture','generation',{'new':True})
    assert replacement['generation']>task['generation']
    assert next(item for item in activity_client.get('/api/system/activity').json()['items'] if item['id']==task['id'])['classification']=='waiting'
    finish(replacement)
    assert activity_client.get('/api/system/activity?group=finished').json()['clearable_finished']==1


def test_activity_groups_game_stages_and_settings_disabled_is_separate_from_manual_pause(activity_client):
    now=datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('history-game','lichess','test',?,'rapid',1,'white','win','start','[]')", (now,))
        db.execute("INSERT INTO game_derivation_jobs(game_id,status,updated_at) VALUES('history-game','queued',?)", (now,))
        db.execute("INSERT INTO game_analysis_jobs(game_id,status,updated_at) VALUES('history-game','queued',?)", (now,))
        db.execute('UPDATE settings SET defensive_analysis_enabled=0 WHERE id=1')
    defensive=enqueue_task('defensive_threat_scan','disabled',{})
    response=activity_client.get('/api/system/activity').json()
    game=next(item for item in response['items'] if item.get('logical_id')=='game:history-game')
    assert game['stage_count']==2 and response['counts']['queued']==1
    assert next(item for item in response['items'] if item['id']==defensive['id'])['classification']=='disabled'
    assert response['counts']['disabled']==1 and response['counts']['manual_paused']==0


def test_activity_legacy_timestamp_normalization_preserves_retry_delays_and_clear_cutoffs():
    from app.services.activity_history import project_stages
    base={'source':'durable','title':'Historical timestamp','phase':'queued','completed':None,'total':None,'error':None,'promoted':False}
    stages=[{**base,'id':'done','state':'complete','updated_at':'2020-01-01'},
            {**base,'id':'delayed','state':'queued','updated_at':'2020-01-01','next_attempt_at':'2050-01-01'}]
    response=project_stages(stages,{'cleared_through':None,'signing_key':'fixture'})
    assert response['completion_cutoff']=='2020-01-01T00:00:00+00:00'
    assert next(item for item in response['items'] if item['id']=='delayed')['waiting_reason']=='retry_delay'
