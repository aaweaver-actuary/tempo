"""Current-scope, retained-source, retry delay and foreground coverage proof."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from threading import Event
import time
from unittest.mock import patch
import chess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import postgres_store, review_commands
from app.command_gateway import execute_command
from app.services import explorer_sessions as sessions
from app.services import postgres_coverage_explorer as explorer, postgres_coverage_recovery as recovery
from app.services.repertoire_coverage import ExplorerRequestError
from app.services.durable_tasks import claim_task
from app.services.activity_gate import activity_gate
from app.services.redis_admission_gate import BackgroundAdmissionDeferred


def proof_coverage_recovery(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Coverage recovery proof requires disposable fixtures')
    import check_postgres_graph_retention as fixtures
    started=time.monotonic()
    with patch.object(fixtures,'DATABASE_URL',parent_database_url), fixtures.owned_fixture_database() as identity, \
            patch.object(sessions,'TOKEN_KEY',identity+':token'), patch.object(sessions,'REJECTED_KEY',identity+':rejected'), \
            patch.dict(os.environ,{'TEMPO_LICHESS_EXPLORER_TOKEN':''}):
        server=sessions.client()
        now=datetime.now(timezone.utc).isoformat()
        rep,current,old,obsolete,card=[identity+'-'+suffix for suffix in ('rep','current','old','obsolete','study')]
        settings={'maia_elo':1600,'reply_denominator':100,'cumulative_target':.95,'speed_weights':{'blitz':1},'canonical_prefix_revision':0}
        board=chess.Board();board.push_uci('e2e4'); reply_fen=board.fen()
        try:
            with postgres_store.connection() as database:
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'Coverage recovery','synthetic',?)",(rep,now))
                for run,created,revision in [(old,'2026-10-01',0),(current,'2026-10-02',0),(obsolete,'2026-10-03',99)]:
                    database.execute("INSERT INTO repertoire_coverage_runs(id,repertoire_id,status,settings_json,total_nodes,completed_nodes,created_at,updated_at) VALUES(?,?,'failed',?,2,1,?,?)",(run,rep,json.dumps({**settings,'canonical_prefix_revision':revision}),created,now))
                    database.execute("INSERT INTO repertoire_coverage_nodes(id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,covered_replies_json,explorer_status,maia_status,explorer_failure_code,explorer_error,last_error,updated_at) VALUES(?,?,?,?,?,1,'white','[]','[]','failed','complete','registration_missing','Register Explorer','Register Explorer',?)",(run+'-auth',run,rep,reply_fen,reply_fen,now))
                    database.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,maia_probability,covered,source_state) VALUES(?,'e7e5',.8,0,'maia-only')",(run+'-auth',))
                database.execute("INSERT INTO repertoire_coverage_nodes(id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,covered_replies_json,explorer_status,maia_status,explorer_games,updated_at) VALUES(?,?,?,?,?,0,'black','[]','[]','complete','complete',20,?)",(current+'-complete',current,rep,chess.STARTING_FEN,chess.STARTING_FEN,now))
                database.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,explorer_probability,maia_probability,covered,source_state) VALUES(?,'e2e4',.6,.7,0,'blended')",(current+'-complete',))
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,content_type) VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,?,'tactics')",(card,rep,chess.STARTING_FEN,date.today().isoformat(),date.today().isoformat()))
                queue=database.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,0) RETURNING id",(date.today().isoformat(),card)).fetchone()[0]
            assert not recovery.recover_one_explorer_run(),'Authentication wait retried without a session'
            sessions.register('synthetic-current-scope-session')
            with activity_gate.foreground():
                try: recovery.recover_one_explorer_run()
                except BackgroundAdmissionDeferred: pass
                else: raise AssertionError('Recovery bypassed foreground admission')
            assert recovery.recover_one_explorer_run()
            assert not recovery.recover_one_explorer_run(),'Recovery replaced already pending work'
            first=claim_task('coverage_explorer');assert first['payload']['run_id']==current
            with patch.object(explorer,'_fetch_explorer',side_effect=ExplorerRequestError('Rate limited',code='rate_limited',retry_seconds=120)):
                assert explorer.execute_coverage_explorer_slice(first)
            postgres_store.close_pools()
            with postgres_store.connection(read_only=True) as database:
                saved=database.execute('SELECT state,next_attempt_at,attempt_count,payload_json FROM background_tasks WHERE id=?',(first['id'],)).fetchone()
                node=database.execute('SELECT explorer_status,explorer_failure_code,explorer_retry_at,maia_status FROM repertoire_coverage_nodes WHERE id=?',(current+'-auth',)).fetchone()
                assert saved[0]=='retrying' and saved[2]==0 and json.loads(saved[3])==first['payload']
                assert tuple(node)[:2]==('queued','rate_limited') and node[2]==saved[1] and node[3]=='complete'
                assert (datetime.fromisoformat(node[2])-datetime.now(timezone.utc)).total_seconds()>115
            assert claim_task('coverage_explorer') is None and not recovery.recover_one_explorer_run()
            with postgres_store.connection() as database:
                database.execute("UPDATE background_tasks SET next_attempt_at='2000-01-01' WHERE id=?",(first['id'],))
                database.execute("UPDATE repertoire_coverage_nodes SET explorer_retry_at='2000-01-01' WHERE id=?",(current+'-auth',))
            task=claim_task('coverage_explorer');assert task
            entered,release=Event(),Event()
            def fetch_with_no_transaction(*_):
                assert activity_gate.active_background_sections==0
                entered.set();assert release.wait(5)
                return {'moves':[{'uci':'e7e5','white':10,'draws':5,'black':5}],'_explorer_games':20,'_probabilities':{'e7e5':1}}
            with patch.object(explorer,'_fetch_explorer',fetch_with_no_transaction), ThreadPoolExecutor(max_workers=1) as executor:
                pending=executor.submit(explorer.execute_coverage_explorer_slice,task)
                try:
                    assert entered.wait(5)
                    review_started=time.monotonic()
                    with activity_gate.foreground():
                        result=execute_command(identity+'-review','cards.review',{'card_id':card,'review':{'outcome':'correct','queue_entry_id':queue,'attempt_id':identity+'-attempt','recorded_at':now}})
                    review_seconds=time.monotonic()-review_started
                    assert result['persisted'] and review_seconds<1 and not pending.done()
                finally: release.set()
                assert pending.result(timeout=5)
            assert not explorer.execute_coverage_explorer_slice(task),'Stale replay advanced a completed node twice'
            postgres_store.close_pools()
            with postgres_store.connection(read_only=True) as database:
                assert tuple(database.execute('SELECT explorer_status,maia_status,explorer_failure_code,explorer_error FROM repertoire_coverage_nodes WHERE id=?',(current+'-auth',)).fetchone())==('complete','complete',None,None)
                assert database.execute('SELECT maia_probability FROM repertoire_coverage_candidates WHERE node_id=? AND move_uci=?',(current+'-auth','e7e5')).fetchone()[0]==.8
                assert tuple(database.execute('SELECT explorer_probability,maia_probability FROM repertoire_coverage_candidates WHERE node_id=?',(current+'-complete',)).fetchone())==(.6,.7)
                assert database.execute('SELECT completed_nodes FROM repertoire_coverage_runs WHERE id=?',(current,)).fetchone()[0]==2
                for run in (old,obsolete):
                    assert database.execute('SELECT explorer_status FROM repertoire_coverage_nodes WHERE id=?',(run+'-auth',)).fetchone()[0]=='failed'
            with postgres_store.connection() as database:
                database.execute("UPDATE repertoire_coverage_nodes SET explorer_status='failed',explorer_failure_code='invalid_response',explorer_error='Invalid response' WHERE id=?",(current+'-auth',))
                database.execute("UPDATE repertoire_coverage_runs SET status='failed' WHERE id=?",(current,))
                database.execute("UPDATE background_tasks SET state='complete' WHERE id=?",(first['id'],))
            assert not recovery.recover_one_explorer_run(),'Non-authentication failure or older attempt was silently retried'
            print(json.dumps({'test':'test_postgres_coverage_latest_scope_partial_results_restart_rate_limit_foreground_and_replay','foreground_review_ms':round(review_seconds*1000,3),'duration_seconds':round(time.monotonic()-started,2)}))
        finally: server.delete(sessions.TOKEN_KEY,sessions.REJECTED_KEY)


if __name__=='__main__':
    proof_coverage_recovery(os.environ['TEMPO_COVERAGE_PROOF_URL'])
