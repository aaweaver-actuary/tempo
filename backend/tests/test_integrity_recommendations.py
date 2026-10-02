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
