"""Logical scale, authoritative history and durable snapshot/receipt replay proof."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import postgres_store, activity_commands
from app.command_gateway import execute_command
from app.services.background_activity import list_activity
from app.services.durable_tasks import enqueue_task


def proof_activity_history(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE')!='disposable': raise RuntimeError('Disposable database required')
    import check_postgres_graph_retention as fixtures
    started=time.monotonic()
    with patch.object(fixtures,'DATABASE_URL',parent_database_url),fixtures.owned_fixture_database() as identity:
        now=datetime.now(timezone.utc).isoformat();rep=identity+'-rep'
        with postgres_store.connection() as db:
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'London System','fixture',?)",(rep,now))
            db.execute("INSERT INTO repertoire_integrity_jobs(repertoire_id,run_id,status,source_offset,total_sources,updated_at) VALUES(?,'september','running',113,2612,'2026-09-28T09:46:00+00:00')",(rep,))
            db.execute_native("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) SELECT %s||'-game-'||number,'lichess','fixture',%s,'rapid',1,'white','win','start','[]' FROM generate_series(1,2143) number",(identity,now))
            db.execute_native("INSERT INTO game_derivation_jobs(game_id,status,updated_at) SELECT id,'queued',%s FROM imported_games",(now,))
            db.execute_native("INSERT INTO game_analysis_jobs(game_id,status,updated_at) SELECT id,'queued',%s FROM imported_games",(now,))
            db.execute('UPDATE settings SET defensive_analysis_enabled=0 WHERE id=1')
        defensive=enqueue_task('defensive_threat_scan',identity+'-disabled',{})
        finished=enqueue_task('activity_fixture',identity+'-finished',{})
        failed=enqueue_task('activity_fixture',identity+'-failed',{})
        paused=enqueue_task('activity_fixture',identity+'-paused',{})
        with postgres_store.connection() as db:
            db.execute("UPDATE background_tasks SET state='complete',completed_at=?,updated_at=? WHERE id=?",(now,now,finished['id']))
            db.execute("UPDATE background_tasks SET state='failed',last_error='Repair required' WHERE id=?",(failed['id'],))
            db.execute("INSERT INTO background_activity(source,work_id,paused,updated_at) VALUES('durable',?,1,?)",(paused['id'],now))
        read_started=time.monotonic();snapshot=list_activity(limit=100);read_seconds=time.monotonic()-read_started
        assert snapshot['counts']['queued']==2143 and snapshot['counts']['disabled']==1 and snapshot['counts']['manual_paused']==1
        assert snapshot['counts']['failed']==1 and snapshot['counts']['history']==1
        assert list_activity(group='history')['items'][0]['updated_at'].startswith('2026-09-28')
        assert all(item['stage_count']==2 for item in list_activity(group='waiting')['items'])
        later=enqueue_task('activity_fixture',identity+'-later',{})
        later_at=datetime.now(timezone.utc).isoformat()
        with postgres_store.connection() as db: db.execute("UPDATE background_tasks SET state='complete',completed_at=?,updated_at=? WHERE id=?",(later_at,later_at,later['id']))
        payload={key:snapshot[key] for key in ('completion_cutoff','completion_snapshot')}
        accepted=execute_command(identity+'-clear','activity.clear_finished',payload)
        postgres_store.close_pools()
        assert execute_command(identity+'-clear','activity.clear_finished',payload)==accepted
        other=list_activity(limit=100)
        assert other['clearable_finished']==1 and other['counts']['failed']==1 and other['counts']['manual_paused']==1 and other['counts']['disabled']==1
        assert next(item for item in list_activity(group='history')['items'] if item['id']==finished['id'])['archived']
        replacement=enqueue_task('activity_fixture',identity+'-finished',{'new':True})
        assert replacement['generation']>finished['generation'] and list_activity()['counts']['queued']==2144
        with postgres_store.connection(read_only=True) as db:
            assert db.execute('SELECT COUNT(*) FROM imported_games').fetchone()[0]==2143
            assert db.execute('SELECT COUNT(*) FROM background_tasks').fetchone()[0]==5
            assert db.execute('SELECT COUNT(*) FROM operation_receipts WHERE operation_id=?',(identity+'-clear',)).fetchone()[0]==1
        print(json.dumps({'test':'test_postgres_activity_logical_scale_legacy_history_cross_device_clear_receipt_restart_new_generation_and_retained_pauses','logical_games':2143,'activity_read_ms':round(read_seconds*1000,3),'duration_seconds':round(time.monotonic()-started,2)}))


if __name__=='__main__': proof_activity_history(os.environ['TEMPO_ACTIVITY_PROOF_URL'])
