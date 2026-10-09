"""Large atomic integrity publication, foreground contention, restart and replay."""
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
import psycopg
from psycopg.errors import LockNotAvailable

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import postgres_store, review_commands, activity_commands
from app.command_gateway import execute_command
from app.services import postgres_integrity as integrity
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
from app.services.durable_tasks import claim_task, enqueue_task_in_transaction


def proof_integrity_publications(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Integrity publication proof requires disposable PostgreSQL')
    import check_postgres_graph_retention as fixtures
    started=time.monotonic()
    with patch.object(fixtures,'DATABASE_URL',parent_database_url), fixtures.owned_fixture_database() as identity:
        repertoire_id=identity+'-rep'
        now=datetime.now(timezone.utc).isoformat()
        card_prefix=identity+'-card-'
        study_id=identity+'-study'
        with postgres_store.connection() as database:
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'Integrity','synthetic',?)",(repertoire_id,now))
            graph=enqueue_task_in_transaction(database,'opening_graph_rebuild',repertoire_id,{'repertoire_id':repertoire_id})
            database.execute("UPDATE background_tasks SET state='complete' WHERE id=?",(graph['id'],))
            database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(?,1,?)",(repertoire_id,now))
            database.execute_native("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,pending_validation) SELECT %s||lpad(number::text,4,'0'),%s,'prefix',%s,'[]',%s,'opening',1 FROM generate_series(0,2142) number",(card_prefix,repertoire_id,chess.STARTING_FEN,date.today().isoformat()))
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,state,introduced_at) VALUES(?,?,'prefix',?,'[\"e2e4\"]',?,'tactics','learning',?)",(study_id,repertoire_id,chess.STARTING_FEN,date.today().isoformat(),date.today().isoformat()))
            queue_id=database.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,0) RETURNING id",(date.today().isoformat(),study_id)).fetchone()[0]
            database.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,created_at,updated_at) VALUES(?,?,'invalid_source','legacy',?,?)",(identity+'-legacy',repertoire_id,now,now))
            database.execute("INSERT INTO repertoire_integrity_card_blocks VALUES(?,?,?,'legacy',?)",(repertoire_id,card_prefix+'0000',identity+'-legacy',now))
            queued=integrity.request_integrity_scan_in_transaction(database,repertoire_id,1,date.today().isoformat())
            run_id=f"{queued['id']}:{queued['generation']}"
            for ordinal in range(129):
                sources=([{'type':'card','id':card_prefix+f'{number:04}'} for number in range(2143)]
                         if ordinal==128 else [{'type':'card','id':card_prefix+f'{ordinal:04}'}])
                database.execute("INSERT INTO repertoire_integrity_issue_candidates(run_id,id,repertoire_id,kind,signature,moves_json,sources_json) VALUES(?,?,?,'invalid_source',?,'[]',?)",(run_id,f'issue-{ordinal:04}',repertoire_id,str(ordinal),json.dumps(sources)))
            database.execute("UPDATE background_tasks SET phase='publish' WHERE id=?",(queued['id'],))
            database.execute("UPDATE repertoire_integrity_state SET scan_status='running' WHERE repertoire_id=?",(repertoire_id,))
        durations=[]
        def next_slice():
            task=claim_task('integrity_scan')
            assert task and task['id']==queued['id']
            return task
        def execute(task):
            before=time.monotonic()
            result=integrity.execute_postgres_integrity_slice(task)
            durations.append(time.monotonic()-before)
            return result
        def visible():
            with postgres_store.connection(read_only=True) as database:
                return tuple(database.execute('SELECT COUNT(*) FROM current_repertoire_integrity_issues WHERE repertoire_id=?',(repertoire_id,)).fetchone()), tuple(database.execute('SELECT COUNT(*) FROM current_repertoire_integrity_card_blocks WHERE repertoire_id=?',(repertoire_id,)).fetchone())
        first=next_slice()
        with activity_gate.foreground():
            try: execute(first)
            except BackgroundAdmissionDeferred: pass
            else: raise AssertionError('Foreground admission allowed a publication slice')
        assert visible()==((1,),(1,))
        entered,release=Event(),Event()
        original_prepare=integrity.prepare_integrity_publication_page
        def pause_after_closed_read(task):
            prepared=original_prepare(task)
            assert activity_gate.active_background_sections==0
            entered.set()
            assert release.wait(10)
            return prepared
        with ThreadPoolExecutor(max_workers=1) as executor, patch.object(integrity,'prepare_integrity_publication_page',pause_after_closed_read):
            future=executor.submit(execute,first)
            try:
                assert entered.wait(5)
                assert visible()==((1,),(1,))
                before=time.monotonic()
                with activity_gate.foreground():
                    review=execute_command(identity+'-review','cards.review',{'card_id':study_id,'review':{'outcome':'correct','queue_entry_id':queue_id,'attempt_id':identity+'-attempt','recorded_at':now}})
                foreground_ms=(time.monotonic()-before)*1000
                assert review['persisted'] and not future.done()
            finally:
                release.set()
            assert future.result(timeout=10)
        assert not execute(first),'Stale replay advanced a publication page'
        postgres_store.close_pools()
        # Preserve a failed compatible page and its cursor through the real
        # idempotent foreground control rather than restarting source traversal.
        with postgres_store.connection() as database:
            saved=database.execute('SELECT phase,payload_json FROM background_tasks WHERE id=?',(queued['id'],)).fetchone()
            database.execute("UPDATE background_tasks SET state='failed',last_error='interrupted' WHERE id=?",(queued['id'],))
        retry=execute_command(identity+'-retry','activity.task.retry',{'task_id':queued['id']})
        assert retry['phase']==saved[0]
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT payload_json FROM background_tasks WHERE id=?',(queued['id'],)).fetchone()[0]==saved[1]
            assert database.execute('SELECT COUNT(*) FROM integrity_issue_generations WHERE run_id=?',(run_id,)).fetchone()[0]==1
            assert database.execute('SELECT 1 FROM integrity_training_blocks WHERE repertoire_id=? AND card_id=?',(repertoire_id,card_prefix+'2142')).fetchone()
        # Real row contention rolls back the page. Release and replay the exact
        # claimed lease under the unchanged short database budget.
        contended=next_slice()
        with psycopg.connect(fixtures.DATABASE_URL) as blocker:
            blocker.execute('SELECT id FROM background_tasks WHERE id=%s FOR UPDATE',(queued['id'],))
            try:
                execute(contended)
            except LockNotAvailable:
                pass
            else:
                raise AssertionError('Publication ignored a real foreground row lock')
        assert execute(contended)
        for ordinal in range(1400):
            task=next_slice()
            if task['phase']=='complete':
                break
            if task['phase']=='publish':
                assert visible()==((1,),(1,)),'Staging exposed a partial generation'
            assert execute(task)
            if ordinal%17==0:
                postgres_store.close_pools()
        else:
            raise AssertionError('Large integrity generation did not reach validation completion')
        assert visible()==((1,),(1,))
        class Interrupted(Exception): pass
        def interrupt(*_args): raise Interrupted()
        with patch('app.services.postgres_opening_segmentation.request_segmentation_in_transaction',interrupt):
            try: execute(task)
            except Interrupted: pass
            else: raise AssertionError('Publication interruption was not exercised')
        postgres_store.close_pools()
        assert visible()==((1,),(1,)),'Interrupted pointer switch leaked rows'
        assert execute(task)
        assert visible()==((129,),(2271,))
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT COUNT(*) FROM cards WHERE repertoire_id=? AND content_type=\'opening\' AND pending_validation=1',(repertoire_id,)).fetchone()[0]==2143
            assert database.execute('SELECT scan_status FROM repertoire_integrity_state WHERE repertoire_id=?',(repertoire_id,)).fetchone()[0]=='idle'
            assert database.execute('SELECT COUNT(*) FROM repertoire_integrity_issues WHERE repertoire_id=?',(repertoire_id,)).fetchone()[0]==1,'Legacy diagnostic history was deleted'
        assert execute(next_slice())
        # An obsolete failed scan starts at current graph inputs, leaving the
        # old complete publication visible until the new empty scan validates.
        with postgres_store.connection() as database:
            database.execute("UPDATE background_tasks SET state='failed' WHERE id=?",(queued['id'],))
            database.execute('UPDATE background_tasks SET generation=2 WHERE id=?',(graph['id'],))
            database.execute('UPDATE opening_graph_publications SET generation=2 WHERE repertoire_id=?',(repertoire_id,))
        fresh=execute_command(identity+'-fresh','activity.task.retry',{'task_id':queued['id']})
        assert fresh['generation']>queued['generation']
        with postgres_store.connection() as database:
            assert json.loads(database.execute('SELECT payload_json FROM background_tasks WHERE id=?',(queued['id'],)).fetchone()[0])['graph_generation']==2
            database.execute("UPDATE background_tasks SET phase='publish' WHERE id=?",(queued['id'],))
            database.execute("UPDATE repertoire_integrity_state SET scan_status='running' WHERE repertoire_id=?",(repertoire_id,))
        assert visible()==((129,),(2271,))
        for _ in range(1400):
            task=next_slice()
            execute(task)
            with postgres_store.connection(read_only=True) as database:
                state=database.execute('SELECT state FROM background_tasks WHERE id=?',(queued['id'],)).fetchone()[0]
            if state=='complete': break
        else: raise AssertionError('Empty replacement/cleanup did not finish')
        assert visible()==((0,),(0,))
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT COUNT(*) FROM integrity_issue_generations WHERE repertoire_id=?',(repertoire_id,)).fetchone()[0]==0
            assert database.execute('SELECT COUNT(*) FROM repertoire_integrity_issue_candidates WHERE run_id=?',(run_id,)).fetchone()[0]==0
            assert database.execute('SELECT COUNT(*) FROM cards WHERE repertoire_id=? AND pending_validation=1',(repertoire_id,)).fetchone()[0]==0
            assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?',(study_id,)).fetchone()[0]==1
        with activity_gate.foreground():
            try:
                integrity.prepare_next_integrity_cards(repertoire_id,'')
            except BackgroundAdmissionDeferred: pass
            else: raise AssertionError('Foreground contention admitted a background scan')
        print('PASS test_postgres_large_integrity_publication_pages_foreground_restart_retry_and_atomic_replay',f'129 issues/2271 blocks/2143 cards; maximum slice {max(durations)*1000:.3f} ms; foreground review {foreground_ms:.3f} ms')
    print('Native proof duration:',round(time.monotonic()-started,2),'seconds')


if __name__=='__main__':
    proof_integrity_publications(os.environ['TEMPO_INTEGRITY_PROOF_URL'])
