"""Provider responses and retained partial coverage recovery."""
from types import SimpleNamespace
import httpx
import pytest
from app.services import repertoire_coverage as coverage


def fetch_response(monkeypatch, response):
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, *_, **__): return response
    monkeypatch.setattr(coverage.httpx, 'Client', Client)
    return coverage._fetch_explorer('synthetic-position', 'blitz:1', '1600', 'synthetic-session')


def test_explorer_rate_limit_honors_retry_after_without_rejecting_session(monkeypatch):
    with pytest.raises(coverage.ExplorerRequestError) as error:
        fetch_response(monkeypatch, httpx.Response(429, headers={'Retry-After':'120'}))
    assert error.value.code == 'rate_limited'
    assert error.value.retry_seconds == 120


@pytest.mark.parametrize('invalid_move', [
    {'uci':'e2e4','white':-1,'draws':0,'black':0},
    {'uci':'e2e4','white':True,'draws':0,'black':0},
    {'uci':'e2e4','white':1.5,'draws':0,'black':0},
    {'uci':'not-a-move','white':1,'draws':0,'black':0},
])
def test_explorer_invalid_source_counts_or_moves_are_explicit_errors(monkeypatch, invalid_move):
    with pytest.raises(coverage.ExplorerRequestError) as error:
        fetch_response(monkeypatch, httpx.Response(200, json={'moves':[invalid_move]}))
    assert error.value.code == 'invalid_response'


def test_explorer_empty_and_valid_source_responses_remain_usable(monkeypatch):
    assert fetch_response(monkeypatch, httpx.Response(200,json={'moves':[]}))['_explorer_games']==0
    result=fetch_response(monkeypatch,httpx.Response(200,json={'moves':[{'uci':'e2e4','white':2,'draws':1,'black':1}]}))
    assert result['_explorer_games']==4 and result['_probabilities']=={'e2e4':1.0}


def test_explorer_retry_after_http_date_and_missing_header_follow_provider_delay():
    from datetime import datetime, timezone
    now=datetime(2026,10,9,tzinfo=timezone.utc)
    assert coverage.explorer_retry_delay('Fri, 09 Oct 2026 00:02:00 GMT',now=now)==120
    assert coverage.explorer_retry_delay(None,now=now)==60
    assert coverage.explorer_retry_delay('invalid',now=now)==60


def test_explorer_unavailable_and_invalid_json_have_distinct_recovery_policy(monkeypatch):
    with pytest.raises(coverage.ExplorerRequestError) as error:
        fetch_response(monkeypatch,httpx.Response(503))
    assert error.value.code=='provider_unavailable' and error.value.retry_seconds==60
    with pytest.raises(coverage.ExplorerRequestError) as error:
        fetch_response(monkeypatch,httpx.Response(200,content=b'not json'))
    assert error.value.code=='invalid_response' and error.value.retry_seconds is None


def test_sqlite_provider_delay_preserves_maia_result_and_excludes_old_attempt(tmp_path,monkeypatch):
    from app import database
    import json, chess
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'coverage.db')
    monkeypatch.delenv('TEMPO_LICHESS_EXPLORER_TOKEN',raising=False)
    for name,value in [('_explorer_session_token',None),('_explorer_session_expires_at',0),('_explorer_rejected_fingerprint',None)]:
        monkeypatch.setattr(coverage,name,value)
    database.initialize()
    settings=json.dumps({'maia_elo':1600,'reply_denominator':100,'cumulative_target':.95,'speed_weights':{'blitz':1}})
    with database.connection() as db:
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Coverage','proof','2026-10-01')")
        for run,created in [('old','2026-10-01'),('current','2026-10-02')]:
            db.execute("INSERT INTO repertoire_coverage_runs(id,repertoire_id,status,settings_json,total_nodes,created_at,updated_at) VALUES(?,'rep','queued',?,1,?,?)",(run,settings,created,created))
            db.execute("INSERT INTO repertoire_coverage_nodes(id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,covered_replies_json,maia_status,updated_at) VALUES(?,?,'rep',?,?,0,'black','[]','[]','complete',?)",(run+'-node',run,chess.STARTING_FEN,chess.STARTING_FEN,created))
            db.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,maia_probability,covered,source_state) VALUES(?,'e2e4',.7,0,'maia-only')",(run+'-node',))
    coverage.set_explorer_session_token('synthetic-recovery-session')
    node=coverage.claim_coverage_node()
    assert node['run_id']=='current'
    monkeypatch.setattr(coverage,'_fetch_explorer',lambda *_: (_ for _ in ()).throw(coverage.ExplorerRequestError('Rate limited',code='rate_limited',retry_seconds=120)))
    coverage.execute_coverage_node(node)
    assert coverage.claim_coverage_node() is None
    with database.connection() as db:
        result=db.execute("SELECT explorer_failure_code,explorer_retry_at,maia_status FROM repertoire_coverage_nodes WHERE id='current-node'").fetchone()
        assert result[0]=='rate_limited' and result[1] and result[2]=='complete'
        assert db.execute("SELECT maia_probability FROM repertoire_coverage_candidates WHERE node_id='current-node'").fetchone()[0]==.7
        assert db.execute("SELECT explorer_status FROM repertoire_coverage_nodes WHERE id='old-node'").fetchone()[0]=='queued'
