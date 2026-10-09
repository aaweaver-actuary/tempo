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
    test_postgres_integrity_scan_identity_reuse_and_bounded_staging_cleanup(parent_database_url)
    print('Native proof duration:',round(time.monotonic()-started,2),'seconds')


def test_postgres_integrity_scan_identity_reuse_and_bounded_staging_cleanup(parent_database_url):
    """Normal scan requests retain identity; externally deleted tasks are outside this contract."""
    import check_postgres_graph_retention as fixtures
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        repertoire_id, other_repertoire_id = identity + '-rep', identity + '-other'
        card_id, study_id = identity + '-card', identity + '-study'
        now = datetime.now(timezone.utc).isoformat()
        staging_stores = ('repertoire_integrity_issue_candidates',
                          'repertoire_integrity_position_accumulators',
                          'repertoire_integrity_source_runs')

        def staging_counts(run_id):
            with postgres_store.connection(read_only=True) as database:
                return tuple(database.execute(f'SELECT COUNT(*) FROM {table} WHERE run_id=?',
                                              (run_id,)).fetchone()[0] for table in staging_stores)

        def stage_inputs(database, run_id, owner, issue_count=1, position_count=1, source_count=1):
            for ordinal in range(issue_count):
                database.execute(
                    "INSERT INTO repertoire_integrity_issue_candidates(run_id,id,repertoire_id,kind,"
                    "signature,moves_json,sources_json) VALUES(?,?,?,'invalid_source',?,'[]',?)",
                    (run_id, f'issue-{ordinal:03}', owner, f'signature-{ordinal}',
                     json.dumps([{'type': 'card', 'id': card_id}]) if ordinal == 0 else '[]'))
            for ordinal in range(position_count):
                database.execute("INSERT INTO repertoire_integrity_position_accumulators "
                                 "VALUES(?,?,?,NULL,'[]','[]')", (run_id, f'position-{ordinal}', chess.STARTING_FEN))
            for ordinal in range(source_count):
                database.execute("INSERT INTO repertoire_integrity_source_runs VALUES(?,?,'[]','[]')", (run_id, ordinal))

        def visible():
            with postgres_store.connection(read_only=True) as database:
                return tuple(tuple(row) for row in database.execute(
                    'SELECT id,signature,moves_json,sources_json FROM current_repertoire_integrity_issues '
                    'WHERE repertoire_id=? ORDER BY id', (repertoire_id,)).fetchall()), tuple(
                    tuple(row) for row in database.execute(
                        'SELECT card_id,issue_id FROM current_repertoire_integrity_card_blocks '
                        'WHERE repertoire_id=? ORDER BY card_id,issue_id', (repertoire_id,)).fetchall())

        def claim():
            claimed_task = claim_task('integrity_scan')
            assert claimed_task and claimed_task['id'] == scan_task['id']
            return claimed_task

        def finish_until_cleanup():
            for _ in range(16):
                claimed_task = claim()
                assert integrity.execute_postgres_integrity_slice(claimed_task)
                if claimed_task['phase'] == 'complete':
                    return
            raise AssertionError('Small integrity publication did not reach cleanup')

        with postgres_store.connection() as database:
            for owner in (repertoire_id, other_repertoire_id):
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'Replay','synthetic',?)", (owner, now))
            graph = enqueue_task_in_transaction(database, 'opening_graph_rebuild', repertoire_id, {'repertoire_id': repertoire_id})
            database.execute("UPDATE background_tasks SET state='complete' WHERE id=?", (graph['id'],))
            database.execute('INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(?,1,?)', (repertoire_id, now))
            for identifier, content_type in ((card_id, 'opening'), (study_id, 'tactics')):
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,state,introduced_at) "
                                 "VALUES(?,?,'prefix',?,'[\"e2e4\"]',?,?,'learning',?)",
                                 (identifier, repertoire_id, chess.STARTING_FEN, date.today().isoformat(), content_type, now))
            queue_id = database.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,0) RETURNING id',
                                        (date.today().isoformat(), study_id)).fetchone()[0]
            database.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,created_at,updated_at) "
                             "VALUES(?,?,'invalid_source','legacy',?,?)", (identity + '-legacy', repertoire_id, now, now))
            first_request = integrity.request_integrity_scan_in_transaction(database, repertoire_id, 1, date.today().isoformat())
            scan_task = integrity.request_integrity_scan_in_transaction(database, repertoire_id, 1, date.today().isoformat())
            assert scan_task['id'] == first_request['id'] and scan_task['generation'] > first_request['generation']
            accepted_run = f"{scan_task['id']}:{scan_task['generation']}"
            stage_inputs(database, accepted_run, repertoire_id)
            database.execute("UPDATE background_tasks SET phase='publish' WHERE id=?", (scan_task['id'],))
            other_task = enqueue_task_in_transaction(database, 'integrity_scan', other_repertoire_id, {'repertoire_id': other_repertoire_id}, delay_seconds=3600)
            other_run = f"{other_task['id']}:{other_task['generation']}"
            assert other_task['id'] != scan_task['id']
            stage_inputs(database, other_run, other_repertoire_id)
        review = execute_command(identity + '-review', 'cards.review', {'card_id': study_id, 'review': {
            'outcome': 'correct', 'queue_entry_id': queue_id, 'attempt_id': identity + '-attempt', 'recorded_at': now}})
        assert review['persisted']
        finish_until_cleanup()
        assert integrity.execute_postgres_integrity_slice(claim())
        accepted_visible = visible()
        assert len(accepted_visible[0]) == len(accepted_visible[1]) == 1
        postgres_store.close_pools()
        with postgres_store.connection() as database:
            scan_task = integrity.request_integrity_scan_in_transaction(database, repertoire_id, 1, date.today().isoformat())
            assert scan_task['id'] == first_request['id'] and scan_task['generation'] > first_request['generation']
            database.execute("UPDATE background_tasks SET state='failed',phase='publish' WHERE id=?", (scan_task['id'],))
        compatible = execute_command(identity + '-compatible', 'activity.task.retry', {'task_id': scan_task['id']})
        assert compatible['id'] == scan_task['id'] and compatible['generation'] == scan_task['generation']
        assert compatible['phase'] == 'publish'
        with postgres_store.connection() as database:
            database.execute("UPDATE background_tasks SET state='failed' WHERE id=?", (scan_task['id'],))
            database.execute('UPDATE background_tasks SET generation=2 WHERE id=?', (graph['id'],))
            database.execute('UPDATE opening_graph_publications SET generation=2 WHERE repertoire_id=?', (repertoire_id,))
        incompatible = execute_command(identity + '-incompatible', 'activity.task.retry', {'task_id': scan_task['id']})
        assert incompatible['id'] == scan_task['id'] and incompatible['generation'] > scan_task['generation']
        current_run = f"{incompatible['id']}:{incompatible['generation']}"
        with postgres_store.connection() as database:
            # More than one page in every old staging store; retain the old accepted issue.
            for ordinal in range(1, 18):
                database.execute("INSERT INTO repertoire_integrity_issue_candidates(run_id,id,repertoire_id,kind,signature,moves_json,sources_json) "
                                 "VALUES(?,?,?,'invalid_source','obsolete','[]','[]')", (accepted_run, f'issue-{ordinal:03}', repertoire_id))
            for ordinal in (1, 2):
                database.execute("INSERT INTO repertoire_integrity_position_accumulators VALUES(?,?,?,NULL,'[]','[]')", (accepted_run, f'position-{ordinal}', chess.STARTING_FEN))
            database.execute("INSERT INTO repertoire_integrity_source_runs VALUES(?,1,'[]','[]')", (accepted_run,))
            stage_inputs(database, current_run, repertoire_id)
            database.execute("UPDATE background_tasks SET phase='publish' WHERE id=?", (scan_task['id'],))
            database.execute("INSERT INTO integrity_issue_generations(run_id,id,repertoire_id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json,created_at,updated_at) "
                             "SELECT run_id,id,repertoire_id,kind,fen_key,fen,trained_color,signature,'[\"d2d4\"]',sources_json,?,? "
                             "FROM repertoire_integrity_issue_candidates WHERE run_id=?", (now, now, current_run))
        replay_task = claim()
        saved_checkpoint = json.dumps(replay_task['payload'], sort_keys=True)
        for complete_flag in (0, 1):
            with postgres_store.connection() as database:
                database.execute('UPDATE integrity_issue_generations SET blocks_complete=? WHERE run_id=?', (complete_flag, current_run))
            try:
                integrity.execute_postgres_integrity_slice(replay_task)
            except RuntimeError as error:
                assert 'immutable candidate content' in str(error)
            else:
                raise AssertionError('Conflicting publication replay was silently accepted')
            postgres_store.close_pools()
            with postgres_store.connection(read_only=True) as database:
                assert database.execute('SELECT run_id FROM integrity_publications WHERE repertoire_id=?', (repertoire_id,)).fetchone()[0] == accepted_run
                assert tuple(database.execute('SELECT moves_json,blocks_complete FROM integrity_issue_generations WHERE run_id=?', (current_run,)).fetchone()) == ('["d2d4"]', complete_flag)
                assert database.execute('SELECT COUNT(*) FROM integrity_block_generations WHERE run_id=?', (current_run,)).fetchone()[0] == 0
                saved_task = database.execute('SELECT state,payload_json FROM background_tasks WHERE id=?', (scan_task['id'],)).fetchone()
                assert saved_task[0] == 'leased' and json.dumps(json.loads(saved_task[1]), sort_keys=True) == saved_checkpoint
            assert visible() == accepted_visible
        print('PASS test_postgres_integrity_publication_conflicting_page_replay_fails_before_completion')
        with postgres_store.connection() as database:
            database.execute("UPDATE integrity_issue_generations SET moves_json='[]',blocks_complete=0 WHERE run_id=?", (current_run,))
            database.execute('INSERT INTO integrity_block_generations VALUES(?,?,?,?,?)',
                             (current_run, repertoire_id, card_id, 'issue-000', now))
        assert integrity.execute_postgres_integrity_slice(replay_task)
        assert not integrity.execute_postgres_integrity_slice(replay_task), 'Stale identical replay bypassed lease fencing'
        with postgres_store.connection(read_only=True) as database:
            staged_issue = database.execute('SELECT moves_json,created_at,updated_at,blocks_complete FROM integrity_issue_generations WHERE run_id=?', (current_run,)).fetchone()
            assert tuple(staged_issue) == ('[]', now, now, 1)
            assert database.execute('SELECT COUNT(*) FROM integrity_issue_generations WHERE run_id=?', (current_run,)).fetchone()[0] == 1
            assert database.execute('SELECT COUNT(*) FROM integrity_block_generations WHERE run_id=?', (current_run,)).fetchone()[0] == 1
        assert visible() == accepted_visible
        print('PASS test_postgres_integrity_publication_identical_page_replay_is_idempotent')
        finish_until_cleanup()
        current_visible = visible()
        with postgres_store.connection(read_only=True) as database:
            preserved_study = tuple(tuple(tuple(row) for row in database.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall())
                                    for table in ('cards', 'reviews', 'daily_queue', 'queue_projections', 'repertoire_integrity_issues'))
        for _ in range(16):
            old_counts = staging_counts(accepted_run)
            cleanup_task = claim()
            assert cleanup_task['phase'] == 'cleanup'
            assert integrity.execute_postgres_integrity_slice(cleanup_task)
            new_counts = staging_counts(accepted_run)
            removed = tuple(before - after for before, after in zip(old_counts, new_counts))
            assert all(0 <= count <= limit for count, limit in zip(removed, (16, 2, 1)))
            assert sum(count > 0 for count in removed) <= 1
            assert staging_counts(current_run) == staging_counts(other_run) == (1, 1, 1)
            assert visible() == current_visible
            postgres_store.close_pools()
            with postgres_store.connection(read_only=True) as database:
                if database.execute('SELECT state FROM background_tasks WHERE id=?', (scan_task['id'],)).fetchone()[0] == 'complete':
                    break
        else:
            raise AssertionError('Bounded staging cleanup did not complete')
        assert staging_counts(accepted_run) == (0, 0, 0)
        with postgres_store.connection(read_only=True) as database:
            assert tuple(tuple(tuple(row) for row in database.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall())
                         for table in ('cards', 'reviews', 'daily_queue', 'queue_projections', 'repertoire_integrity_issues')) == preserved_study
            assert database.execute('SELECT run_id FROM integrity_publications WHERE repertoire_id=?', (repertoire_id,)).fetchone()[0] == current_run
            assert database.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='integrity_scan' AND deduplication_key=?", (repertoire_id,)).fetchone()[0] == 1
        print('PASS test_postgres_integrity_scan_identity_reuse_and_bounded_staging_cleanup')


if __name__=='__main__':
    proof_integrity_publications(os.environ['TEMPO_INTEGRITY_PROOF_URL'])
