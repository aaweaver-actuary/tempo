from datetime import date
import json

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app

PGN = b'[Event "Rated blitz game"]\n[White "andy"]\n[Black "opponent"]\n[UTCDate "2026.09.16"]\n[UTCTime "12:30:00"]\n[Site "https://lichess.org/game1"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 *'

def test_prefixes_match_shared_rust_golden_fixtures():
    from pathlib import Path
    from app.services.pgn import prefix_through_user_moves
    fixtures=json.loads((Path(__file__).parents[2]/'tests/fixtures/core-parity.json').read_text())
    for fixture in fixtures:
        assert prefix_through_user_moves(fixture['fen'],fixture['moves'],fixture['color'],fixture['depth'])==fixture['prefix']

def test_legacy_introduced_but_unreviewed_queue_is_capped_without_losing_reviews(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        settings = client.get('/api/settings').json()
        settings['new_cards_per_day'] = 2
        client.put('/api/settings', json=settings)
        imported = client.post('/api/imports/pgn', files={'file': ('mine.pgn', PGN)}, data={'initial_depth': 2}).json()
        first = client.get('/api/queue/today').json()['cards'][0]
        client.post(f"/api/cards/{first['id']}/review", json={'outcome': 'correct', 'queue_entry_id': first['queue_entry_id']})
        with database.connection() as db:
            for i in range(146):
                db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at) VALUES(?,?,'prefix',?,'[]','learning',?,?)", (f'legacy-{i}',imported['repertoire_id'],first['start_fen'],date.today().isoformat(),date.today().isoformat()))
                db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)", (date.today().isoformat(),f'legacy-{i}',i+10))
        queue = client.get('/api/queue/today').json()
        assert queue['count'] == 2  # due reinforcement plus one new introduction
        assert queue == client.get('/api/queue/today').json()
        with database.connection() as db:
            assert db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM cards WHERE state='new' AND introduced_at IS NULL").fetchone()[0] == 145

def test_completed_queue_entry_is_idempotent_and_reinforcement_schedules_into_the_future(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        client.post('/api/imports/pgn', files={'file': ('one.pgn',PGN)}, data={'initial_depth':2})
        first = client.get('/api/queue/today').json()['cards'][0]
        url = f"/api/cards/{first['id']}/review"
        payload = {'outcome':'correct','queue_entry_id':first['queue_entry_id']}
        saved = client.post(url,json=payload).json()
        assert client.post(url,json=payload).json() == saved
        reinforcement = client.get('/api/queue/today').json()['cards'][0]
        assert reinforcement['queue_entry_id'] != first['queue_entry_id']
        assert reinforcement['attempt_state'] == 'reinforcement'
        scheduled = client.post(url,json={'outcome':'correct','queue_entry_id':reinforcement['queue_entry_id']}).json()
        assert scheduled['interval_days'] >= 1
        assert scheduled['next_due'] > date.today().isoformat()
        assert client.get('/api/queue/today').json()['count'] == 0
        with database.connection() as db: assert db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 2

def test_deletion_isolates_records_sharing_a_source_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH',tmp_path/'tempo.db')
    with TestClient(app) as client:
        ids=[]
        for side in ('white','black'):
            result=client.post('/api/imports/pgn',files={'file':('Tempo examples.pgn',PGN)},data={'trained_color':side,'initial_depth':2}).json()
            ids.append(result['repertoire_id'])
        assert ids[0]!=ids[1]
        client.delete(f'/api/repertoires/{ids[0]}')
        assert [r['id'] for r in client.get('/api/repertoires').json()['repertoires']] == [ids[1]]
        assert [r['id'] for r in client.get('/api/repertoires').json()['repertoires']] == [ids[1]]
        assert all(line['repertoire_id']==ids[1] for line in client.get('/api/repertoire/lines').json()['lines'])

def test_tactic_discovery_is_idempotent_and_cursors_are_per_deck(tmp_path, monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'tempo.db')
    with TestClient(app) as client:
        payload={'attempt_id':'first-attempt','puzzle_id':'one','deck_id':'fork-easy','correct':False,'clean':False,'source_fen':'8/8/8/8/8/4k3/7p/6K1 b - - 0 1','moves':['h2h1q','g1h1']}
        result=client.post('/api/tactics/attempt',json=payload)
        assert result.status_code==200
        assert client.post('/api/tactics/attempt',json=payload).json()==result.json()
        assert client.get('/api/tactics/progress').json()['fork:easy']['index']==1
        assert client.get('/api/queue/today').json()['count']==1

def test_local_sync_persists_errors_and_success_without_sample_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'tempo.db')
    original=httpx.AsyncClient
    requests=[]
    status=404
    def handler(request):
        requests.append(request)
        return httpx.Response(status,content=PGN if status==200 else b'not found',request=request)
    monkeypatch.setattr('app.main.httpx.AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(handler),**kwargs))
    with TestClient(app) as client:
        error=client.post('/api/games/sync',json={'lichess_username':'andy'})
        assert error.status_code==404
        state=client.get('/api/games/sync/status').json()['providers'][0]
        assert state['status']=='error'
        assert state['last_started_at'] and state['last_error']=='Lichess username not found'
        assert client.get('/api/games/summary').json()['total']==0
        status=200
        with database.connection() as db: db.execute('UPDATE game_sync_state SET retry_after=NULL')
        assert client.post('/api/games/sync',json={'lichess_username':'andy'}).json()['imported']==1
        assert client.post('/api/games/sync',json={'lichess_username':'andy'}).json()['imported']==0
        assert client.get('/api/games/summary').json()['total']==1
        game_id=client.get('/api/games/summary').json()['games'][0]['id']
        from urllib.parse import quote
        assert client.post(f'/api/games/{quote(game_id,safe="")}/analysis',json={'evaluations':[{'ply':0,'before_cp':0,'after_cp':-150}],'depth':6}).status_code==200
        assert int(requests[-1].url.params['since']) > int(requests[-2].url.params['since'])
        assert client.get('/api/games/sync/status').json()['providers'][0]['last_success_at']

def test_again_reappears_after_four_other_entries(tmp_path,monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'tempo.db')
    with TestClient(app) as client:
        client.post('/api/imports/pgn',files={'file':('one.pgn',PGN)},data={'initial_depth':2})
        first=client.get('/api/queue/today').json()['cards'][0]
        with database.connection() as db:
            for i in range(5):
                db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at) VALUES(?,?,'prefix',?,'[]','learning',?,'2020-01-01')",(f'other-{i}',first['repertoire_id'],first['start_fen'],date.today().isoformat()))
                db.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)',(date.today().isoformat(),f'other-{i}',i+1))
        client.post(f"/api/cards/{first['id']}/review",json={'outcome':'again','queue_entry_id':first['queue_entry_id']})
        ids=[card['id'] for card in client.get('/api/queue/today').json()['cards']]
        assert ids[4]==first['id'] and ids[:4]==[f'other-{i}' for i in range(4)]

def test_game_mistakes_respect_arbitrary_fen_side_to_move():
    from app.services.game_analysis import classify_swings
    result=classify_swings([{'ply':0,'before_cp':0,'after_cp':150}], 'black', starting_color='black')
    assert result['major_mistake_ply']==0
