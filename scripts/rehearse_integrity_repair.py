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
    guided_repair_postgres_report_attachment_cannot_lose_wakeup(observer)
    guided_repair_postgres_attachment_contention_yields_without_spending_retry(observer)
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


def _seed_attachment_rehearsal(observer, label, prefix):
    """A fresh legal source and an already-leased, shared full-history request."""
    from app.database import connection
    from app.services import durable_tasks, integrity_recommendations as recommendations
    from app.services.discovery_admission import _full_history_request
    from app.services.threat_validation import AnalysisReport, AnalysisLine, EngineScore

    repertoire_id, issue_id = label, label + '-issue'
    position = chess.Board()
    for move in prefix: position.push_uci(move)
    responses = [move.uci() for move in position.legal_moves][:5]
    descriptors = []
    observer.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'fixture','2026-10-02')", (repertoire_id, label))
    for ordinal, response in enumerate(responses[:2]):
        source_id = label + f'-line-{ordinal}'
        descriptors.append({'type': 'line', 'id': source_id, 'move_index': len(prefix), 'move': response})
        observer.execute("INSERT INTO repertoire_lines VALUES(%s,%s,%s,'white',%s,%s,'2026-10-02')",
            (source_id, repertoire_id, source_id, chess.STARTING_FEN, json.dumps([*prefix, response])))
    observer.execute("INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation) VALUES(%s,'needs_repair','idle','attachment:1')", (repertoire_id,))
    observer.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json,created_at,updated_at) VALUES(%s,%s,'multiple_responses',%s,%s,'white','signature',%s,%s,'2026-10-02','2026-10-02')",
        (issue_id, repertoire_id, ' '.join(position.fen().split()[:4]), position.fen(), json.dumps(responses[:2]), json.dumps(descriptors)))
    _, request = _full_history_request({'start_fen': chess.STARTING_FEN,
        'moves_json': json.dumps([*prefix, responses[0]]), 'color': 'white', 'ply': len(prefix)})
    lease_id = label + '-engine-lease'
    observer.execute("INSERT INTO threat_analysis_requests(id,request_json,state,lease_id,lease_expires_at,attempts,created_at,updated_at) VALUES(%s,%s,'leased',%s,'2099-01-01',1,'2026-10-02','2026-10-02')",
        (request.request_id, json.dumps(asdict(request)), lease_id))
    observer.commit()
    payload = {'repertoire_id': repertoire_id, 'issue_id': issue_id, 'signature': 'signature'}
    with connection() as database: admission = recommendations.admit_recommendation(database, payload)
    task = durable_tasks.claim_task(kind='integrity_recommendation')
    assert task and task['id'] == admission['task_id'] and task['payload']['phase'] == 'route'
    evidence = AnalysisReport(request, tuple(AnalysisLine(move, (move,), EngineScore(cp=30-ordinal),14)
        for ordinal, move in enumerate(responses)), True)
    return payload, task, {'request_id': request.request_id, 'lease_id': lease_id, 'report': asdict(evidence)}


def _drain_attachment_rehearsal(observer, payload, route_task, report_payload, *, minimum_rank_generation=1):
    from app.services import durable_tasks, integrity_recommendations as recommendations
    from app.services.activity_gate import activity_gate

    # Expire a delivery to prove restart resumes the persisted cursor.
    interrupted = durable_tasks.claim_task(kind='integrity_recommendation')
    if interrupted:
        observer.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=%s", (interrupted['id'],)); observer.commit()
        assert not recommendations.execute_integrity_recommendation_slice(interrupted)
    for _ in range(20):
        task = durable_tasks.claim_task(kind='integrity_recommendation')
        if not task: break
        with activity_gate.background_job(task['kind'], task['id']): recommendations.execute_integrity_recommendation_slice(task)
    else: raise AssertionError('Attachment recommendation exceeded bounded slices')
    preview = recommendations.recommendation_status(payload['repertoire_id'], payload['issue_id'], payload['signature'])
    assert preview['state'] == 'ready', ('Lost report wake-up left a permanent waiting recommendation', preview)
    observer.commit()
    assert observer.execute('SELECT COUNT(*) FROM threat_analysis_requests WHERE id=%s', (report_payload['request_id'],)).fetchone()[0] == 1
    assert observer.execute('SELECT attempts FROM threat_analysis_requests WHERE id=%s', (report_payload['request_id'],)).fetchone()[0] == 1
    assert observer.execute('SELECT COUNT(*) FROM integrity_recommendation_requests WHERE issue_id=%s', (payload['issue_id'],)).fetchone()[0] == 1
    publications = observer.execute("SELECT generation,COUNT(*) FROM background_task_events WHERE task_id=%s AND event='published' AND generation>=%s AND generation IN (SELECT generation FROM background_task_events WHERE task_id=%s AND phase='rank') GROUP BY generation",
        (route_task['id'], minimum_rank_generation, route_task['id'])).fetchall()
    assert len(publications) == 1 and publications[0][1] == 1, publications
    assert not recommendations.execute_integrity_recommendation_slice(route_task)
    with recommendations.connection() as database:
        before = database.execute('SELECT generation FROM background_tasks WHERE id=?', (route_task['id'],)).fetchone()[0]
        recommendations.admit_recommendation(database, payload)
        assert database.execute('SELECT generation FROM background_tasks WHERE id=?', (route_task['id'],)).fetchone()[0] == before
    observer.commit()


def guided_repair_postgres_report_attachment_cannot_lose_wakeup(observer):
    # The atomic command already conflicts with the first association's FK
    # key-share lock. The shared save path updates non-key columns and can pass
    # that lock; exercise both against the same real PostgreSQL isolation.
    for report_path in ('shared_save', 'atomic_command'):
        _rehearse_report_attachment_interleaving(observer, report_path)
    print('PASS guided_repair_postgres_report_attachment_cannot_lose_wakeup')


def _rehearse_report_attachment_interleaving(observer, report_path):
    """Force uncommitted subscriber visibility against real report completion."""
    import time
    from app import postgres_store
    from app.services import integrity_recommendations as recommendations
    from app.services.activity_gate import activity_gate
    from app.threat_analysis_commands import submit_threat_report
    from app.services import threat_pipeline

    prefix = ['d2d4', 'd7d5'] if report_path == 'shared_save' else ['c2c4', 'e7e5']
    payload, route_task, report_payload = _seed_attachment_rehearsal(observer, 'guided-attachment-' + report_path, prefix)
    route_observed_leased = threading.Event()
    release_route = threading.Event()
    report_committed = threading.Event()
    report_connected = threading.Event()
    failures, backend_ids = [], {}
    original_connection = recommendations.connection
    original_report_connection = threat_pipeline.connection

    @contextmanager
    def controlled_route_connection(*, background=False):
        # Only these barrier-controlled connections get test deadlines. This
        # bypasses the process-local gate to model independent worker processes.
        with postgres_store.connection() as database:
            database.execute_native("SET LOCAL transaction_timeout='10s'")
            database.execute_native("SET LOCAL statement_timeout='8s'")
            backend_ids['route'] = database.execute_native('SELECT pg_backend_pid()').fetchone()[0]
            original_execute = database.execute
            def observed_execute(statement, parameters=()):
                cursor = original_execute(statement, parameters)
                if statement == 'SELECT state FROM threat_analysis_requests WHERE id=?':
                    route_observed_leased.set()
                    assert release_route.wait(6), 'Coordinator did not release the uncommitted subscriber'
                return cursor
            database.execute = observed_execute
            yield database

    def route_worker():
        try:
            with activity_gate.background_job(route_task['kind'], route_task['id']):
                recommendations.execute_integrity_recommendation_slice(route_task)
        except BaseException as error: failures.append(error)
    @contextmanager
    def controlled_report_connection(*, background=False):
        with postgres_store.connection() as database:
            database.execute_native("SET LOCAL statement_timeout='8s'")
            backend_ids['report'] = database.execute_native('SELECT pg_backend_pid()').fetchone()[0]
            report_connected.set()
            yield database
    def report_worker():
        try:
            if report_path == 'shared_save':
                threat_pipeline.save_analysis_report(report_payload['request_id'], report_payload['lease_id'], report_payload['report'])
            else:
                with controlled_report_connection() as database:
                    submit_threat_report(database, report_payload)
            report_committed.set()
        except BaseException as error: failures.append(error)

    threat_pipeline.connection = controlled_report_connection
    recommendations.connection = controlled_route_connection
    route_thread = threading.Thread(target=route_worker)
    report_thread = threading.Thread(target=report_worker)
    report_blocked = False
    try:
        route_thread.start()
        assert route_observed_leased.wait(5), failures
        # The subscriber is still invisible from the independent observer.
        assert observer.execute('SELECT request_id FROM integrity_recommendation_requests WHERE issue_id=%s', (payload['issue_id'],)).fetchone()[0] is None
        observer.commit()
        report_thread.start()
        assert report_connected.wait(3), failures
        deadline = time.monotonic() + 5
        while not report_committed.is_set() and not failures:
            blockers = observer.execute('SELECT pg_blocking_pids(%s)', (backend_ids['report'],)).fetchone()[0]
            observer.commit()
            if backend_ids['route'] in blockers:
                report_blocked = True
                break
            assert time.monotonic() < deadline, ('Report neither committed nor blocked on attachment', backend_ids)
        # Baseline: B's callback saw no subscriber and committed first. Fix: B
        # cannot pass A's engine lock, so A must commit its association first.
        release_route.set()
        route_thread.join(8); report_thread.join(8)
        assert not route_thread.is_alive() and not report_thread.is_alive() and not failures, failures
        assert report_committed.is_set()
    finally:
        release_route.set()
        route_thread.join(8)
        if report_thread.ident is not None: report_thread.join(8)
        recommendations.connection = original_connection
        threat_pipeline.connection = original_report_connection
    print(f'Attachment interleaving: path={report_path}; report_blocked={report_blocked}; report committed; route committed')
    _drain_attachment_rehearsal(observer, payload, route_task, report_payload)
    assert report_blocked, 'Report completion was not serialized against the subscriber attachment'
    if report_path == 'atomic_command':
        guided_repair_postgres_stranded_report_repreparation_coalesces(observer, payload, route_task, report_payload)


def guided_repair_postgres_attachment_contention_yields_without_spending_retry(observer):
    from app import postgres_store
    from app.services import durable_tasks, integrity_recommendations as recommendations
    from app.threat_analysis_commands import submit_threat_report
    from app.services.activity_gate import activity_gate
    from psycopg.errors import LockNotAvailable

    payload, route_task, report_payload = _seed_attachment_rehearsal(observer, 'guided-attachment-contention', ['e2e4', 'c7c5'])
    # B owns the engine first. A must roll back instead of waiting while it
    # holds the target task row; the ordinary worker contention path defers it.
    with postgres_store.connection() as reporting:
        reporting.execute_native('SELECT id FROM threat_analysis_requests WHERE id=%s FOR UPDATE', (report_payload['request_id'],))
        try:
            with activity_gate.background_job(route_task['kind'], route_task['id']):
                recommendations.execute_integrity_recommendation_slice(route_task)
        except LockNotAvailable:
            assert durable_tasks.defer_task_for_contention(route_task['id'], route_task['generation'], route_task['lease_token'], kind=route_task['kind'])
        else: raise AssertionError('Attachment did not yield when report owned the engine')
        assert observer.execute('SELECT request_id FROM integrity_recommendation_requests WHERE issue_id=%s', (payload['issue_id'],)).fetchone()[0] is None
        yielded = observer.execute('SELECT generation,state,attempt_count,payload_json FROM background_tasks WHERE id=%s', (route_task['id'],)).fetchone()
        assert yielded[:3] == (route_task['generation'], 'retrying', 0), yielded
        assert json.loads(yielded[3]) == route_task['payload']
        observer.commit()
        submit_threat_report(reporting, report_payload)
    # Advance only this fixture's due time, rather than sleep through backoff.
    observer.execute("UPDATE background_tasks SET next_attempt_at='2000-01-01' WHERE id=%s", (route_task['id'],)); observer.commit()
    _drain_attachment_rehearsal(observer, payload, route_task, report_payload)
    print('PASS guided_repair_postgres_attachment_contention_yields_without_spending_retry')


def guided_repair_postgres_stranded_report_repreparation_coalesces(observer, payload, route_task, report_payload):
    """Concurrent explicit preparations and a late wake reuse one rank generation."""
    from app import postgres_store
    from app.services import integrity_recommendations as recommendations
    from app.threat_analysis_commands import submit_threat_report

    observer.execute("UPDATE integrity_recommendation_requests SET state='waiting',preview_json=NULL,accumulation_json=NULL WHERE issue_id=%s", (payload['issue_id'],))
    prior_generation = observer.execute('SELECT generation FROM background_tasks WHERE id=%s', (route_task['id'],)).fetchone()[0]
    observer.commit()
    preparations_start = threading.Barrier(3)
    failures, admissions = [], []
    def prepare_worker():
        try:
            with postgres_store.connection() as database:
                preparations_start.wait(timeout=5)
                admissions.append(recommendations.admit_recommendation(database, payload))
        except BaseException as error: failures.append(error)
    workers = [threading.Thread(target=prepare_worker) for _ in range(2)]
    try:
        for worker in workers: worker.start()
        preparations_start.wait(timeout=5)
        for worker in workers: worker.join(8)
        assert not any(worker.is_alive() for worker in workers) and not failures, failures
    finally:
        preparations_start.abort()
        for worker in workers: worker.join(8)
    assert len(admissions) == 2 and all(admission['task_id'] == route_task['id'] for admission in admissions)
    assert observer.execute('SELECT generation FROM background_tasks WHERE id=%s', (route_task['id'],)).fetchone()[0] == prior_generation + 1
    with postgres_store.connection() as database:
        recommendations.wake_report_previews(database, report_payload['request_id'])
        assert submit_threat_report(database, report_payload) == {'status': 'complete', 'candidate_count': 0}
    _drain_attachment_rehearsal(observer, payload, route_task, report_payload, minimum_rank_generation=prior_generation + 1)
    assert observer.execute('SELECT generation FROM background_tasks WHERE id=%s', (route_task['id'],)).fetchone()[0] == prior_generation + 1
    observer.commit()
    print('PASS guided_repair_postgres_stranded_report_repreparation_coalesces')
