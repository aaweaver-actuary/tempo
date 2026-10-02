"""Named repair regressions on the durability runner's isolated PostgreSQL database."""
from contextlib import contextmanager
from dataclasses import asdict
import json
import threading

import chess
from fastapi import HTTPException


def guided_repair_postgres_contention_restart_replay_and_publication(observer):
    from app.database import connection
    from app.services import durable_tasks, integrity_recommendations as recommendations
    from app.services import postgres_opening_graph, postgres_integrity
    from app.services.activity_gate import activity_gate
    from app.services.repertoire_integrity import prepare_issue_resolution
    from app.integrity_repair_commands import resolve_integrity_issue
    from app.integrity_api import repair_status
    from app.threat_analysis_commands import claim_threat_analysis, submit_threat_report
    from app.services.threat_pipeline import report_from_json
    from app.services.threat_validation import AnalysisReport, AnalysisLine, EngineScore

    repertoire_id = 'guided-repair'
    position = chess.Board(); position.push_uci('e2e4'); position.push_uci('e7e5')
    sources = []
    observer.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'repair.pgn','2026-10-02')", (repertoire_id, repertoire_id))
    for ordinal, response in enumerate(('g1f3', 'f1c4')):
        source_id = f'guided-line-{ordinal}'
        sources.append({'type': 'line', 'id': source_id, 'move_index': 2, 'move': response})
        observer.execute("INSERT INTO repertoire_lines VALUES(%s,%s,%s,'white',%s,%s,'2026-10-02')",
                         (source_id, repertoire_id, source_id, chess.STARTING_FEN, json.dumps(['e2e4', 'e7e5', response])))
    # Historical idle scans can predate generation tracking. They remain bound
    # to their current null generation until a real scan supersedes them.
    observer.execute("INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation) VALUES(%s,'needs_repair','idle',NULL)", (repertoire_id,))
    observer.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json,created_at,updated_at) VALUES('guided-issue',%s,'multiple_responses',%s,%s,'white','signature',%s,%s,'2026-10-02','2026-10-02')",
                     (repertoire_id, ' '.join(position.fen().split()[:4]), position.fen(), json.dumps(['g1f3', 'f1c4']), json.dumps(sources)))
    observer.commit()
    payload = {'repertoire_id': repertoire_id, 'issue_id': 'guided-issue', 'signature': 'signature'}
    with connection() as database:
        admission = recommendations.admit_recommendation(database, payload)
        assert recommendations.admit_recommendation(database, payload)['task_id'] == admission['task_id']
    task = durable_tasks.claim_task(kind='integrity_recommendation')
    assert task and task['id'] == admission['task_id']

    active_reads = 0
    computation_started = threading.Event(); worker_started = threading.Event()
    failures = []
    original_read = recommendations.background_read_connection
    original_prepare = recommendations._full_history_request
    @contextmanager
    def counted_read():
        nonlocal active_reads
        with original_read() as database:
            active_reads += 1
            try: yield database
            finally: active_reads -= 1
    def checked_prepare(source):
        assert active_reads == 0, 'Computation retained a PostgreSQL read transaction'
        computation_started.set()
        return original_prepare(source)
    def run_slice():
        worker_started.set()
        try:
            with activity_gate.background_job('integrity_recommendation', task['id']):
                recommendations.execute_integrity_recommendation_slice(task)
        except Exception as error: failures.append(error)
    recommendations.background_read_connection = counted_read
    recommendations._full_history_request = checked_prepare
    try:
        with activity_gate.foreground():
            worker = threading.Thread(target=run_slice); worker.start()
            assert worker_started.wait(3)
            assert not computation_started.is_set()
            observer.execute('SELECT 1'); observer.commit()
        worker.join(10)
        assert not worker.is_alive() and not failures, failures
        assert computation_started.is_set()
    finally:
        recommendations.background_read_connection = original_read
        recommendations._full_history_request = original_prepare
    assert not recommendations.execute_integrity_recommendation_slice(task)
    with connection() as database:
        engine = claim_threat_analysis(database, {})['job']
    assert engine, 'Repair did not make its full-history engine request claimable'
    request = report_from_json({'request': engine['request'], 'lines': [], 'complete': False}).request
    assert request.position_prefix_uci == ('e2e4', 'e7e5')
    report = AnalysisReport(request, (
        AnalysisLine('g1f3', ('g1f3', 'b8c6'), EngineScore(cp=30),14),
        AnalysisLine('f1c4', ('f1c4', 'g8f6'), EngineScore(cp=10),14),
        AnalysisLine('a2a3', ('a2a3', 'g8f6'), EngineScore(cp=-100),14),
        AnalysisLine('d2d4', ('d2d4', 'e5d4'), EngineScore(cp=5),14),
        AnalysisLine('b1c3', ('b1c3', 'g8f6'), EngineScore(cp=0),14)), True)
    with connection() as database:
        submit_threat_report(database, {'request_id': engine['id'], 'lease_id': engine['lease_id'], 'report': asdict(report)})
    # Simulate a process crash before consuming the next persisted cursor.
    crashed = durable_tasks.claim_task(kind='integrity_recommendation')
    assert crashed
    observer.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=%s", (crashed['id'],)); observer.commit()
    assert not recommendations.execute_integrity_recommendation_slice(crashed)
    for _ in range(12):
        restarted = durable_tasks.claim_task(kind='integrity_recommendation')
        if not restarted: break
        with activity_gate.background_job('integrity_recommendation', restarted['id']):
            recommendations.execute_integrity_recommendation_slice(restarted)
    else: raise AssertionError('Recommendation failed to yield/finish bounded slices')
    preview = recommendations.recommendation_status(repertoire_id, 'guided-issue', 'signature')
    assert preview['state'] == 'ready' and preview['suggested_move_uci'] == 'g1f3', preview
    assert observer.execute('SELECT COUNT(*) FROM threat_analysis_requests WHERE id=%s', (engine['id'],)).fetchone()[0] == 1
    guided_repair_postgres_legacy_card_color_and_unavailable_recovery(observer, repertoire_id, position)
    observer.execute("UPDATE repertoire_lines SET moves_json='[]' WHERE id='guided-line-0'"); observer.commit()
    try: recommendations.recommendation_status(repertoire_id, 'guided-issue', 'signature')
    except HTTPException as error: assert error.status_code == 409
    else: raise AssertionError('Changed source evidence was published')
    observer.execute("UPDATE repertoire_lines SET moves_json=%s WHERE id='guided-line-0'", (json.dumps(['e2e4','e7e5','g1f3']),)); observer.commit()

    prepared = prepare_issue_resolution(repertoire_id, 'guided-issue', 'signature', 'g1f3')
    with connection() as database: saved = resolve_integrity_issue(database, prepared)
    def status(): return repair_status(repertoire_id, saved['task_id'], 'guided-issue', saved['task_generation'])
    assert status()['state'] == 'waiting'
    # A superseding graph must be followed; the original generation cannot confirm.
    with connection() as database: successor = postgres_opening_graph.request_graph_rebuild_in_transaction(database, repertoire_id, '2026-10-02')
    assert successor['generation'] > saved['task_generation']
    handlers = {'opening_graph_rebuild': postgres_opening_graph.execute_postgres_opening_graph_slice,
                'integrity_scan': postgres_integrity.execute_postgres_integrity_slice}
    for _ in range(160):
        restarted = durable_tasks.claim_task(allowed_kinds=tuple(handlers))
        if not restarted: break
        with activity_gate.background_job(restarted['kind'], restarted['id']): handlers[restarted['kind']](restarted)
        if restarted['kind'] == 'opening_graph_rebuild': assert status()['state'] != 'complete'
    else: raise AssertionError('Graph/integrity workflow did not finish its slices')
    confirmed = status()
    assert confirmed['state'] == 'complete' and confirmed['task_generation'] == successor['generation'], confirmed
    # A retry resets its durable task before the scan worker clears the cached
    # failure. The authoritative task state must keep confirmation pollable.
    from app.activity_commands import retry_failed_task
    scan_generation = observer.execute('SELECT scan_generation FROM repertoire_integrity_state WHERE repertoire_id=%s', (repertoire_id,)).fetchone()[0]
    scan_id = scan_generation.rsplit(':', 1)[0]
    observer.execute("UPDATE background_tasks SET state='failed' WHERE id=%s", (scan_id,))
    observer.execute("UPDATE repertoire_integrity_state SET scan_status='failed',scan_error='Old scan failure' WHERE repertoire_id=%s", (repertoire_id,))
    observer.commit()
    assert status()['state'] == 'failed'
    with connection() as database: retry_failed_task(database, {'task_id': scan_id})
    assert status()['state'] == 'waiting', 'Cached scan failure stranded the committed task retry'
    observer.execute("UPDATE background_tasks SET state='complete' WHERE id=%s", (scan_id,))
    observer.execute("UPDATE repertoire_integrity_state SET scan_status='idle',scan_error=NULL WHERE repertoire_id=%s", (repertoire_id,))
    observer.commit()
    assert status()['state'] == 'complete'
    print('PASS guided_repair_postgres_contention_restart_replay_and_publication')


def guided_repair_postgres_legacy_card_color_and_unavailable_recovery(observer, repertoire_id, position):
    """Use the rehearsal's validated engine report for both legacy memberships."""
    from app.database import connection
    from app.services import durable_tasks, integrity_recommendations as recommendations, postgres_integrity
    from app.services.activity_gate import activity_gate

    observer.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('guided-card-owner','Owner','fixture','2026-10-02')")
    observer.execute("INSERT INTO repertoire_lines VALUES('guided-owner-line','guided-card-owner','Owner','black',%s,'[\"e2e4\",\"e7e5\"]','2026-10-01')", (chess.STARTING_FEN,))
    source_cursor = ''
    for association in ('direct', 'linked'):
        source_id, issue_id = f'guided-card-{association}', f'guided-card-issue-{association}'
        descriptor = {'type': 'card', 'id': source_id, 'move_index': 2, 'move': 'g1f3'}
        observer.execute('INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,trained_color) VALUES(%s,%s,\'prefix\',%s,%s,\'2026-10-02\',\'opening\',\'black\')',
            (source_id, repertoire_id if association == 'direct' else 'guided-card-owner', chess.STARTING_FEN, json.dumps(['e2e4', 'e7e5', 'g1f3'])))
        if association == 'linked':
            observer.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (repertoire_id, source_id))
        observer.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json,created_at,updated_at) VALUES(%s,%s,'multiple_responses',%s,%s,'white','signature',%s,%s,'2026-10-02','2026-10-02')",
            (issue_id, repertoire_id, ' '.join(position.fen().split()[:4]), position.fen(), '["g1f3","f1c4"]', json.dumps([descriptor])))
        observer.commit()
        payload = {'repertoire_id': repertoire_id, 'issue_id': issue_id, 'signature': 'signature'}
        def prepare_and_drain():
            with connection() as database: admission = recommendations.admit_recommendation(database, payload)
            for _ in range(12):
                task = durable_tasks.claim_task(kind='integrity_recommendation')
                if not task: break
                with activity_gate.background_job(task['kind'], task['id']): recommendations.execute_integrity_recommendation_slice(task)
            else: raise AssertionError('Legacy card recommendation failed to finish bounded slices')
            return admission
        first = prepare_and_drain()
        assert recommendations.recommendation_status(repertoire_id, issue_id, 'signature')['state'] == 'unavailable'
        prior_generation = observer.execute('SELECT generation FROM background_tasks WHERE id=%s', (first['task_id'],)).fetchone()[0]
        observer.execute('UPDATE cards SET trained_color=NULL WHERE id=%s', (source_id,)); observer.commit()
        scanned = postgres_integrity.prepare_next_integrity_source(repertoire_id, 'card', source_cursor)
        assert scanned and scanned.source_id == source_id and not scanned.invalid
        assert scanned.positions and all(item['trained_color'] == 'white' for item in scanned.positions)
        second = prepare_and_drain()
        assert observer.execute('SELECT generation FROM background_tasks WHERE id=%s', (second['task_id'],)).fetchone()[0] > prior_generation
        result = recommendations.recommendation_status(repertoire_id, issue_id, 'signature')
        assert result['state'] == 'ready' and result['trained_color'] == 'white' and result['suggested_move_uci'] == 'g1f3', result
        assert all(item['source_type'] == 'card' and item['source_id'] == source_id for item in result['candidates'])
        with connection() as database:
            record = database.execute('SELECT * FROM integrity_recommendation_requests WHERE issue_id=?', (issue_id,)).fetchone()
            assert json.loads(record['source_json'])['trained_color'] == 'white' and recommendations._source_current(database, record)
        assert observer.execute('SELECT trained_color FROM cards WHERE id=%s', (source_id,)).fetchone()[0] is None
        source_cursor = source_id
    print('PASS guided_repair_postgres_legacy_card_color_and_unavailable_recovery')
