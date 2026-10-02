"""Regular-suite repair recommendation, replay and status regressions."""
from contextlib import contextmanager
from dataclasses import asdict
import json
import threading

import chess
from fastapi.testclient import TestClient
import pytest

from app import database
from app.main import app
from app.services import integrity_recommendations as service
from app.services.activity_gate import activity_gate
from app.services.durable_tasks import claim_task
from app.services.threat_pipeline import claim_analysis_request, save_analysis_report
from app.services.threat_validation import AnalysisRequest, AnalysisReport, AnalysisLine, EngineScore


@pytest.fixture
def repair(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'repair.db')
    database.initialize()
    position = chess.Board(); position.push_uci('e2e4'); position.push_uci('e7e5')
    with database.connection() as db:
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Repair','fixture','2026-10-02')")
        for identifier, moves in [('one', ['e2e4','e7e5','g1f3']), ('two', ['e2e4','e7e5','f1c4'])]:
            db.execute('INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)',
                (identifier,'rep',identifier,'white',chess.STARTING_FEN,json.dumps(moves),'2026-10-02'))
        sources = [{'type':'line','id':'one','move_index':2,'move':'g1f3'}, {'type':'line','id':'two','move_index':2,'move':'f1c4'}]
        db.execute("INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation) VALUES('rep','needs_repair','idle','scan:1')")
        db.execute('INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('issue','rep','multiple_responses',' '.join(position.fen().split()[:4]),position.fen(),'white','signature', '["g1f3","f1c4"]',json.dumps(sources),'2026-10-02','2026-10-02'))
    from app.services.database_executor import database_writer
    database_writer.start()
    try: yield TestClient(app)
    finally: database_writer.stop()


def step():
    task = claim_task(allowed_kinds=('integrity_recommendation',))
    if task:
        with activity_gate.background_job(task['kind'], task['id']):
            service.execute_integrity_recommendation_slice(task)
    return task


def report():
    claimed = claim_analysis_request()
    assert claimed
    request = AnalysisRequest(**{**claimed['request'], 'position_prefix_uci': tuple(claimed['request']['position_prefix_uci'])})
    assert request.position_prefix_uci == ('e2e4','e7e5')
    assert request.depth == 14 and request.multipv == 5
    evidence = AnalysisReport(request, (
        AnalysisLine('g1f3', ('g1f3','b8c6','f1c4'), EngineScore(cp=30),14),
        AnalysisLine('f1c4', ('f1c4','g8f6','d2d3'), EngineScore(cp=10),14),
        AnalysisLine('a2a3', ('a2a3','g8f6'), EngineScore(cp=-100),14),
        AnalysisLine('d2d4', ('d2d4','e5d4'), EngineScore(cp=5),14),
        AnalysisLine('b1c3', ('b1c3','g8f6'), EngineScore(cp=0),14),
    ),True)
    save_analysis_report(claimed['id'], claimed['lease_id'], asdict(evidence))
    return evidence


def prepare(client):
    response = client.post('/api/repertoires/rep/integrity/issues/issue/recommendations', json={'signature':'signature'})
    assert response.status_code == 200, response.text
    assert step()
    return response.json()


def create_legacy_card_source(association, trained_color=None):
    descriptor = {'type': 'card', 'id': 'legacy-card', 'move_index': 2, 'move': 'g1f3'}
    with database.connection() as db:
        if association == 'linked':
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('owner','Owner','fixture','2026-10-02')")
            db.execute('INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)',
                ('owner-line', 'owner', 'Owner', 'black', chess.STARTING_FEN, '["e2e4","e7e5"]', '2026-10-01'))
        db.execute('INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,trained_color) VALUES(?,?,?,?,?,?,?,?)',
            ('legacy-card', 'owner' if association == 'linked' else 'rep', 'prefix',
             chess.STARTING_FEN, '["e2e4","e7e5","g1f3"]', '2026-10-02', 'opening', trained_color))
        if association == 'linked':
            db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep','legacy-card')")
        db.execute("UPDATE repertoire_integrity_issues SET sources_json=? WHERE id='issue'", (json.dumps([descriptor]),))
    return descriptor


@pytest.mark.parametrize('association', ['direct', 'linked'])
def test_integrity_recommendations_infer_legacy_card_color_from_the_requested_repertoire(repair, monkeypatch, association):
    descriptor = create_legacy_card_source(association)
    original_prepare = service._full_history_request
    prepared_colors = []
    def checked_prepare(source):
        prepared_colors.append(source['color'])
        assert source['color'] == 'white'
        return original_prepare(source)
    monkeypatch.setattr(service, '_full_history_request', checked_prepare)
    prepare(repair)
    assert prepared_colors == ['white']
    report()
    while step(): pass
    endpoint = '/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature'
    result = repair.get(endpoint).json()
    assert result['state'] == 'ready' and result['trained_color'] == 'white', result
    assert result['suggested_move_uci'] == 'g1f3'
    assert all(candidate['source_type'] == 'card' and candidate['source_id'] == 'legacy-card' for candidate in result['candidates'])
    from app.services.repertoire_integrity import _source_rows
    with database.read_connection() as db:
        scanned_card = next(source for source in _source_rows(db, 'rep') if source['source_type'] == 'card')
        assert service._source(db, 'rep', descriptor)['trained_color'] == scanned_card['trained_color'] == 'white'
        assert db.execute("SELECT trained_color FROM cards WHERE id='legacy-card'").fetchone()[0] is None
        record = dict(db.execute("SELECT * FROM integrity_recommendation_requests WHERE issue_id='issue'").fetchone())
        assert json.loads(record['source_json'])['trained_color'] == 'white'
        assert service._source_current(db, record)
    assert repair.get(endpoint).json()['state'] == 'ready'
    # Normalizing the legacy storage representation preserves its effective evidence.
    with database.connection() as db:
        db.execute("UPDATE cards SET trained_color='white' WHERE id='legacy-card'")
    assert repair.get(endpoint).json()['state'] == 'ready'
    with database.connection() as db:
        db.execute("UPDATE cards SET trained_color=NULL WHERE id='legacy-card'")
        db.execute("UPDATE repertoire_lines SET trained_color='black' WHERE id='one'")
    assert repair.get(endpoint).status_code == 409


@pytest.mark.parametrize('card_color', [None, 'black'])
def test_integrity_card_source_preserves_explicit_color_and_canonical_line_order(repair, card_color):
    descriptor = create_legacy_card_source('direct', card_color)
    with database.connection() as db:
        # Earlier creation outranks IDs, and IDs break creation-time ties.
        db.execute("UPDATE repertoire_lines SET trained_color='black',created_at='2026-10-03' WHERE id='one'")
        db.execute('INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)',
            ('z-earliest', 'rep', 'Earliest', 'white', chess.STARTING_FEN, '[]', '2026-10-01'))
        db.execute('INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)',
            ('zz-tie', 'rep', 'Tie', 'black', chess.STARTING_FEN, '[]', '2026-10-01'))
        assert service._source(db, 'rep', descriptor)['trained_color'] == (card_color or 'white')
        assert service._source(db, 'rep', {'type': 'line', 'id': 'one'})['trained_color'] == 'black'


def test_integrity_recommendations_do_not_guess_a_legacy_card_color_without_repertoire_lines(repair):
    descriptor = create_legacy_card_source('direct')
    with database.connection() as db:
        db.execute("DELETE FROM repertoire_lines WHERE repertoire_id='rep'")
        assert service._source(db, 'rep', descriptor)['trained_color'] is None
    prepare(repair)
    while step(): pass
    result = repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature').json()
    assert result['state'] == 'unavailable' and 'No legal saved source' in result['reason']
    assert claim_analysis_request() is None


def test_unavailable_integrity_recommendations_reprepare_a_now_valid_legacy_card(repair):
    create_legacy_card_source('direct', 'black')
    first = prepare(repair)
    while step(): pass
    endpoint = '/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature'
    assert repair.get(endpoint).json()['state'] == 'unavailable'
    with database.connection() as db:
        prior_generation = db.execute('SELECT generation FROM background_tasks WHERE id=?', (first['task_id'],)).fetchone()[0]
        db.execute("UPDATE cards SET trained_color=NULL WHERE id='legacy-card'")
    second = repair.post(endpoint.split('?')[0], json={'signature': 'signature'})
    assert second.status_code == 200 and second.json()['state'] == 'waiting'
    with database.read_connection() as db:
        assert db.execute('SELECT generation FROM background_tasks WHERE id=?', (second.json()['task_id'],)).fetchone()[0] > prior_generation
    assert step()
    report()
    while step(): pass
    result = repair.get(endpoint).json()
    assert result['state'] == 'ready' and result['trained_color'] == 'white', result
    assert repair.post(endpoint.split('?')[0], json={'signature': 'signature'}).json()['state'] == 'ready'
    assert step() is None


def test_integrity_recommendations_match_discovery_ranking_without_selecting_a_response(repair):
    first = prepare(repair)
    assert repair.post('/api/repertoires/rep/integrity/issues/issue/recommendations',json={'signature':'signature'}).json()['task_id'] == first['task_id']
    evidence = report()
    while step(): pass
    result = repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature').json()
    assert result['state'] == 'ready', result
    assert result['suggested_move_uci'] == 'g1f3'
    assert result['route_uci'] == ['e2e4','e7e5']
    assert 'a2a3' not in [item['move_uci'] for item in result['candidates']]
    assert result['candidates'][0]['repertoire_line_count'] == 1
    with database.read_connection() as db:
        lines = [dict(row) for row in db.execute("SELECT * FROM repertoire_lines ORDER BY id")]
        examples, _ = service._repertoire_positions(lines, 'white')
        position = chess.Board(); position.push_uci('e2e4'); position.push_uci('e7e5')
        expected = service.continuation_recommendation(position, evidence.request, evidence, examples, 'white',
            {'source_type': 'line', 'source_id': 'one', 'source_ply': 2})
        assert result['candidates'] == expected['candidates']
        assert db.execute("SELECT COUNT(*) FROM repertoire_integrity_issues").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_integrity_recommendations_reject_stale_issue_source_and_scan(repair):
    prepare(repair)
    with database.connection() as db:
        db.execute("UPDATE repertoire_lines SET moves_json='[]' WHERE id='one'")
    response = repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature')
    assert response.status_code == 409
    with database.connection() as db:
        db.execute("UPDATE repertoire_integrity_issues SET signature='changed' WHERE id='issue'")
    assert repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature').status_code == 409
    assert claim_analysis_request() is None


def test_integrity_recommendations_support_current_legacy_scan_without_a_generation(repair):
    with database.connection() as db:
        db.execute("UPDATE repertoire_integrity_state SET scan_generation=NULL WHERE repertoire_id='rep'")
    prepare(repair)
    report()
    while step(): pass
    result = repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature').json()
    assert result['state'] == 'ready' and result['suggested_move_uci'] == 'g1f3', result


def test_integrity_recommendations_close_reads_yield_to_foreground_and_replay_after_restart(repair, monkeypatch):
    repair.post('/api/repertoires/rep/integrity/issues/issue/recommendations',json={'signature':'signature'})
    task = claim_task(allowed_kinds=('integrity_recommendation',))
    active_reads = 0
    original_read = service.read_connection
    original_compute = service._full_history_request
    computed = threading.Event()
    @contextmanager
    def tracked_read():
        nonlocal active_reads
        with original_read() as db:
            active_reads += 1
            try: yield db
            finally: active_reads -= 1
    def checked_compute(source):
        assert active_reads == 0
        computed.set()
        return original_compute(source)
    monkeypatch.setattr(service,'read_connection',tracked_read)
    monkeypatch.setattr(service,'_full_history_request',checked_compute)
    started = threading.Event(); finished = threading.Event()
    def run():
        started.set()
        with activity_gate.background_job('integrity_recommendation',task['id']):
            service.execute_integrity_recommendation_slice(task)
        finished.set()
    with activity_gate.foreground():
        worker = threading.Thread(target=run); worker.start()
        assert started.wait(2)
        assert not computed.is_set()
    assert finished.wait(3); worker.join()
    with database.read_connection() as db:
        before = db.execute('SELECT COUNT(*) FROM threat_analysis_requests').fetchone()[0]
    service.execute_integrity_recommendation_slice(task)  # expired/replayed delivery is inert
    assert before == 1
    report()
    # Reclaiming the persisted cursor simulates a fresh worker at each slice.
    while step(): pass
    with database.read_connection() as db:
        assert db.execute('SELECT COUNT(*) FROM threat_analysis_requests').fetchone()[0] == 1
    assert repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature').json()['state'] == 'ready'


def test_repair_status_waits_for_published_validation_and_rejects_unrelated_tasks(repair):
    from app.services.durable_tasks import enqueue_task
    task = enqueue_task('integrity_repair','rep:signature',{'repertoire_id':'rep','issue_id':'issue','signature':'signature','selected_move_uci':'g1f3'})
    endpoint = f"/api/repertoires/rep/integrity/repairs/{task['id']}?issue_id=issue&generation={task['generation']}"
    assert repair.get(endpoint).json()['state'] == 'waiting'
    with database.connection() as db:
        db.execute("UPDATE background_tasks SET state='complete' WHERE id=?",(task['id'],))
        db.execute("UPDATE repertoire_integrity_state SET scan_status='queued' WHERE repertoire_id='rep'")
        db.execute("DELETE FROM repertoire_integrity_issues WHERE id='issue'")
    assert repair.get(endpoint).json()['state'] == 'waiting'
    with database.connection() as db:
        db.execute("UPDATE repertoire_integrity_state SET scan_status='idle' WHERE repertoire_id='rep'")
    assert repair.get(endpoint).json()['state'] == 'complete'
    assert repair.get(endpoint.replace('/rep/', '/other/')).status_code == 404


def test_integrity_recommendations_fence_expired_leases_and_changed_input_generations(repair):
    repair.post('/api/repertoires/rep/integrity/issues/issue/recommendations', json={'signature':'signature'})
    expired = claim_task(allowed_kinds=('integrity_recommendation',))
    with database.connection() as db:
        db.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=?", (expired['id'],))
    assert not service.execute_integrity_recommendation_slice(expired)
    with database.read_connection() as db:
        assert db.execute('SELECT COUNT(*) FROM threat_analysis_requests').fetchone()[0] == 0
    assert step()
    report()
    with database.connection() as db:
        db.execute("UPDATE repertoire_integrity_state SET scan_generation='scan:2' WHERE repertoire_id='rep'")
    while step(): pass
    assert repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature').status_code == 409


def test_integrity_recommendations_report_provider_failure_without_mutating_repair(repair):
    prepare(repair)
    with database.connection() as db:
        db.execute("UPDATE threat_analysis_requests SET state='failed',last_error='Docker Stockfish unavailable'")
    result = repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature').json()
    assert result['state'] == 'failed' and result['reason'] == 'Docker Stockfish unavailable'
    assert repair.get('/api/repertoires/rep/integrity').json()['issue_count'] == 1


def test_repair_status_does_not_report_a_cached_scan_failure_after_its_task_retry_commits(monkeypatch):
    from app import integrity_api
    from types import SimpleNamespace
    graph = {'kind': 'opening_graph_rebuild', 'generation': 2, 'state': 'complete',
             'payload_json': json.dumps({'repertoire_id': 'rep'})}
    scan = {'generation': 2, 'state': 'queued', 'payload_json': json.dumps({'graph_generation': 2})}
    class Database:
        def execute(self, query, parameters):
            if 'FROM background_tasks' in query:
                row = graph if parameters == ('graph',) else scan
            elif 'FROM repertoire_integrity_state' in query:
                row = {'scan_status': 'failed', 'scan_generation': 'scan:2', 'scan_error': 'Old scan failure'}
            elif 'COUNT(*)' in query: row = (0,)
            elif 'FROM repertoire_integrity_issues' in query: row = None
            else: raise AssertionError(query)
            return SimpleNamespace(fetchone=lambda: row)
    @contextmanager
    def reader(): yield Database()
    monkeypatch.setattr(integrity_api, 'read_connection', reader)
    monkeypatch.setattr(integrity_api.postgres_store, 'configured', lambda: True)
    for retry_state in ('queued', 'leased'):
        scan['state'] = retry_state
        assert integrity_api.repair_status('rep', 'graph', 'issue', 2)['state'] == 'waiting'
    scan['state'] = 'failed'
    assert integrity_api.repair_status('rep', 'graph', 'issue', 2)['state'] == 'failed'
    for retry_state in ('queued', 'leased'):
        graph['state'] = retry_state
        assert integrity_api.repair_status('rep', 'graph', 'issue', 2)['state'] == 'waiting'
    graph['state'] = 'complete'
    scan.update(state='queued', generation=3)
    assert integrity_api.repair_status('rep', 'graph', 'issue', 2)['state'] == 'failed'


def completed_engine_without_preview(client):
    """Persist the lost-notification outcome without fabricating engine evidence."""
    admission = prepare(client)
    evidence = report()
    with database.connection() as db:
        db.execute("UPDATE background_tasks SET state='complete',lease_token=NULL,lease_expires_at=NULL WHERE deduplication_key=?",
            ('report:' + evidence.request.request_id,))
        record = dict(db.execute("SELECT * FROM integrity_recommendation_requests WHERE issue_id='issue'").fetchone())
        assert record['state'] == 'waiting' and record['preview_json'] is None
        assert db.execute('SELECT state FROM background_tasks WHERE id=?', (record['task_id'],)).fetchone()[0] == 'complete'
        assert db.execute('SELECT state FROM threat_analysis_requests WHERE id=?', (record['request_id'],)).fetchone()[0] == 'complete'
        generation = db.execute('SELECT generation FROM background_tasks WHERE id=?', (record['task_id'],)).fetchone()[0]
    return admission, evidence, generation


def test_integrity_recommendations_reprepare_stranded_completed_engine(repair):
    admission, evidence, generation = completed_engine_without_preview(repair)
    response = repair.post('/api/repertoires/rep/integrity/issues/issue/recommendations', json={'signature': 'signature'})
    assert response.status_code == 200, response.text
    assert response.json()['task_id'] == admission['task_id']
    with database.read_connection() as db:
        task = db.execute('SELECT * FROM background_tasks WHERE id=?', (admission['task_id'],)).fetchone()
        assert task['generation'] == generation + 1 and json.loads(task['payload_json'])['phase'] == 'rank'
    while step(): pass
    result = repair.get('/api/repertoires/rep/integrity/issues/issue/recommendations?signature=signature').json()
    assert result['state'] == 'ready', result
    with database.read_connection() as db:
        assert db.execute('SELECT COUNT(*) FROM integrity_recommendation_requests').fetchone()[0] == 1
        engines = db.execute('SELECT id,state,attempts FROM threat_analysis_requests').fetchall()
        assert [(row['id'], row['state'], row['attempts']) for row in engines] == [(evidence.request.request_id, 'complete', 1)]
        assert db.execute("SELECT COUNT(*) FROM background_task_events WHERE task_id=? AND generation=? AND event='published'",
            (admission['task_id'], generation + 1)).fetchone()[0] == 1


def test_integrity_recommendation_wake_and_reprepare_coalesce_ranking(repair):
    admission, evidence, generation = completed_engine_without_preview(repair)
    with database.connection() as db:
        service.wake_report_previews(db, evidence.request.request_id)
    delayed_wake = claim_task(kind='integrity_recommendation')
    assert delayed_wake and delayed_wake['payload']['phase'] == 'wake'
    endpoint = '/api/repertoires/rep/integrity/issues/issue/recommendations'
    assert repair.post(endpoint, json={'signature': 'signature'}).status_code == 200
    rank = claim_task(kind='integrity_recommendation')
    assert rank and rank['payload']['phase'] == 'rank'
    assert rank['generation'] == generation + 1
    # Both a leased rank slice and its eventual ready result must survive late wakes.
    assert service.execute_integrity_recommendation_slice(delayed_wake)
    assert repair.post(endpoint, json={'signature': 'signature'}).status_code == 200
    with database.read_connection() as db:
        current = db.execute('SELECT generation,state,lease_token FROM background_tasks WHERE id=?', (rank['id'],)).fetchone()
        assert (current['generation'], current['state'], current['lease_token']) == (rank['generation'], 'leased', rank['lease_token'])
    assert service.execute_integrity_recommendation_slice(rank)
    while step(): pass
    before = repair.get(endpoint + '?signature=signature').json()
    assert before['state'] == 'ready'
    with database.connection() as db:
        service.wake_report_previews(db, evidence.request.request_id)
    while step(): pass
    assert repair.get(endpoint + '?signature=signature').json() == before
    assert repair.post(endpoint, json={'signature': 'signature'}).status_code == 200
    with database.read_connection() as db:
        assert db.execute('SELECT generation FROM background_tasks WHERE id=?', (admission['task_id'],)).fetchone()[0] == generation + 1
        assert db.execute("SELECT COUNT(*) FROM background_task_events WHERE task_id=? AND generation=? AND event='published'",
            (admission['task_id'], generation + 1)).fetchone()[0] == 1


@pytest.mark.parametrize('changed_evidence', ['source', 'signature', 'scan', 'graph'])
def test_integrity_recommendation_delayed_wake_rejects_changed_evidence(repair, changed_evidence):
    admission, evidence, generation = completed_engine_without_preview(repair)
    with database.connection() as db:
        service.wake_report_previews(db, evidence.request.request_id)
    wake = claim_task(kind='integrity_recommendation')
    assert wake and wake['payload']['phase'] == 'wake'
    with database.connection() as db:
        if changed_evidence == 'source':
            db.execute("UPDATE repertoire_lines SET moves_json='[]' WHERE id='one'")
        elif changed_evidence == 'signature':
            db.execute("UPDATE repertoire_integrity_issues SET signature='changed' WHERE id='issue'")
        elif changed_evidence == 'scan':
            db.execute("UPDATE repertoire_integrity_state SET scan_generation='scan:2' WHERE repertoire_id='rep'")
        else:
            from app.services.durable_tasks import enqueue_task_in_transaction
            enqueue_task_in_transaction(db, 'opening_graph_rebuild', 'rep', {'repertoire_id': 'rep'})
    assert service.execute_integrity_recommendation_slice(wake)
    while step(): pass
    with database.read_connection() as db:
        assert db.execute('SELECT generation FROM background_tasks WHERE id=?', (admission['task_id'],)).fetchone()[0] == generation
        assert db.execute("SELECT preview_json FROM integrity_recommendation_requests WHERE issue_id='issue'").fetchone()[0] is None
