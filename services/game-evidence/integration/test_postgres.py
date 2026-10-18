from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import pytest
from fastapi.testclient import TestClient
from tempo_game_evidence import EvidenceStore
from tempo_game_evidence.api import create_app
from tempo_game_evidence.delivery import deliver_one as deliver_authenticated
from tempo_game_evidence.providers import AccountImport, enqueue_import, import_one, cancel_import
from tempo_game_evidence_contracts.v2 import EvidenceDelivery

# This fixture corpus is deliberately local to the owning service.
FIXTURES=Path(__file__).resolve().parents[1]/"fixtures"


def deliver_one(*arguments, **keywords):
    # These synthetic strict receivers do not load user credentials. Production
    # requires a usable token before any claim, just like the actual assembly.
    keywords.setdefault('token', 'native-fixture-credential-' + 't' * 40)
    return deliver_authenticated(*arguments, **keywords)


@pytest.fixture
def state(tmp_path, monkeypatch):
    assert os.environ.get("TEMPO_GAME_EVIDENCE_TEST_CONTAINER","").startswith("tempo-game-evidence-41-")
    store=EvidenceStore(os.environ["TEMPO_GAME_EVIDENCE_DATABASE_URL"])
    credential = tmp_path / 'strict-receiver-token'
    credential.write_text('native-fixture-credential-' + 't' * 40)
    credential.chmod(0o600)
    monkeypatch.setenv('TEMPO_GAME_EVIDENCE_LEARNING_TOKEN_FILE', str(credential))
    # This is exclusively the fresh cluster provisioned by run_postgres.py.
    with psycopg.connect(os.environ["TEMPO_GAME_EVIDENCE_MIGRATION_URL"]) as database:
        database.execute("TRUNCATE analysis_export_pages,analysis_exports,accepted_heads,import_pages,import_cursors,provider_leases,outbox,comparisons,jobs,game_sources,games,snapshots,workspace_inputs CASCADE")
    workspace=uuid.uuid4().hex
    value=json.loads((FIXTURES/"snapshot.json").read_text())
    value["publication"]["workspace_id"]=workspace
    value["activation"]["workspace_id"]=workspace
    return store,workspace,value


def drain(store):
    for _ in range(300):
        if not store.process_one():
            return
    raise AssertionError("job_processing_did_not_converge")


def imported(state,source="miss"):
    store,workspace,value=state
    store.load_snapshot(value)
    game=store.import_pgn(workspace,"pgn",source,"Learner",(FIXTURES/(source+".pgn")).read_bytes())
    drain(store)
    return game


def test_A_interrupted_export_resumes_frozen_input_after_game_revision_changes(state,tmp_path,monkeypatch):
    from tempo_game_evidence import analysis_export
    from tempo_game_evidence.analysis_schema import verify_directory
    store,_,_ = state
    snapshot=json.loads((FIXTURES/'bridge-snapshot-v2.json').read_text())
    workspace=snapshot['publication']['workspace_id']
    store.load_snapshot(snapshot)
    game=store.import_pgn(workspace,'pgn','resume-game','Learner',b'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 c5 2. Nf3 1-0')
    drain(store)
    output=tmp_path/'frozen-input'
    original_write=analysis_export.write_exact
    def interrupt(path,value):
        if path.name.startswith('routes-'):
            raise OSError('synthetic output interruption')
        original_write(path,value)
    with monkeypatch.context() as patch:
        patch.setattr(analysis_export,'write_exact',interrupt)
        with pytest.raises(OSError,match='synthetic output interruption'):
            analysis_export.create_directory_export(store,workspace,[game],output)
    assert not (output/'manifest.json').exists()
    reserved=json.loads((output/'.tempo-analysis-export.json').read_text())
    assert store.import_pgn(workspace,'pgn','resume-game','Learner',b'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 1-0')==game
    drain(store)
    resumed=analysis_export.create_directory_export(store,workspace,[game],output)
    assert resumed['input_id']==reserved['input_id']
    assert verify_directory(output)['status']=='verified'
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store.connect() as database:
            database.execute('UPDATE analysis_exports SET manifest=manifest WHERE input_id=%s',(resumed['input_id'],))
    current=analysis_export.create_directory_export(store,workspace,[game],tmp_path/'current-input')
    assert current['input_id']!=resumed['input_id']
    with pytest.raises(ValueError,match='resume_scope_conflict'):
        analysis_export.create_directory_export(store,'foreign-workspace',[game],output)
    page=analysis_export.export_page(store,workspace,resumed['input_id'],'positions')
    assert page['items'][0]['game_revision']==1
    with pytest.raises(ValueError):
        analysis_export.export_page(store,workspace,resumed['input_id'],'positions','{"page_index":1}')


def ready_outbox(store):
    with store.connect() as database:
        database.execute("UPDATE outbox SET next_attempt_at=now(),leased_until=NULL WHERE status IN ('retryable_failure','accepted_pending')")


def auth_retry_cli(store, workspace, delivery_id, tmp_path):
    credential = tmp_path / 'auth-recovery-token'
    credential.write_text('synthetic-private-credential-' + 'x' * 40)
    credential.chmod(0o600)
    environment = {**os.environ, 'TEMPO_GAME_EVIDENCE_DATABASE_URL': store.database_url}
    return subprocess.run([sys.executable, '-I', '-m', 'tempo_game_evidence.cli',
        'retry-auth-delivery', '--workspace', workspace, '--delivery-id', delivery_id,
        '--learning-endpoint', 'http://127.0.0.1:1/internal/evidence', '--learning-token-file', str(credential)],
        env=environment, capture_output=True, text=True, timeout=10)


def test_learning_401_retries_original_delivery_after_credential_correction(state):
    store, workspace, _ = state
    game = imported(state)
    with store.connect() as database:
        original = database.execute('SELECT * FROM outbox WHERE game_id=%s', (game,)).fetchone()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401))) as client:
        assert deliver_one(store, 'http://fixture/evidence', client=client, token='wrong-' + 'w' * 40)
    with store.connect() as database:
        row = database.execute('SELECT *,extract(epoch FROM next_attempt_at-now()) AS remaining FROM outbox WHERE game_id=%s', (game,)).fetchone()
        assert (row['status'], row['reason'], row['acknowledgement']) == ('retryable_failure', 'remote_rejected_401', None)
        assert 55 <= row['remaining'] <= 905
        assert row['delivery_id'] == original['delivery_id'] and row['payload'] == original['payload']
        assert database.execute('SELECT count(*) AS n FROM accepted_heads').fetchone()['n'] == 0
    ready_outbox(store)
    with learning_receiver(store) as (endpoint, _, requests):
        assert deliver_one(EvidenceStore(store.database_url), endpoint, token='correct-' + 'c' * 40)
        assert requests == [original['delivery_id']]
    with store.connect() as database:
        final = database.execute('SELECT * FROM outbox WHERE game_id=%s', (game,)).fetchone()
        assert final['status'] == 'completed' and final['attempts'] == 2
        assert final['payload'] == original['payload']


def test_retry_auth_delivery_recovers_only_legacy_401_block(state, tmp_path):
    store, workspace, _ = state
    game = imported(state)
    with store.connect() as database:
        database.execute("UPDATE outbox SET status='blocked',reason='remote_rejected_401',attempts=1,lease_token='expired',leased_until=now()-interval '1 second' WHERE game_id=%s", (game,))
        original = database.execute('SELECT * FROM outbox WHERE game_id=%s', (game,)).fetchone()
    result = auth_retry_cli(store, workspace, original['delivery_id'], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    with store.connect() as database:
        recovered = database.execute('SELECT * FROM outbox WHERE game_id=%s', (game,)).fetchone()
        assert recovered['status'] == 'retryable_failure' and recovered['leased_until'] is None
        for field in ('delivery_id', 'payload', 'evidence_revision', 'comparison_revision', 'bucket', 'game_id', 'attempts', 'reason', 'acknowledgement'):
            assert recovered[field] == original[field]
        assert database.execute('SELECT count(*) AS n FROM accepted_heads').fetchone()['n'] == 0
    assert auth_retry_cli(store, workspace, original['delivery_id'], tmp_path).returncode == 0
    with store.connect() as database:
        assert database.execute('SELECT * FROM outbox WHERE game_id=%s', (game,)).fetchone() == recovered


@pytest.mark.parametrize('refusal', ['workspace', '403', '404', 'other', 'conflict', 'stale_revision', 'acknowledged', 'leased', 'completed', 'unattempted'])
def test_retry_auth_delivery_rejects_other_states_and_active_leases(state, tmp_path, refusal):
    store, workspace, _ = state
    game = imported(state)
    with store.connect() as database:
        database.execute("UPDATE outbox SET status='blocked',reason='remote_rejected_401',attempts=1 WHERE game_id=%s", (game,))
        if refusal in ('403', '404', 'other'):
            database.execute('UPDATE outbox SET reason=%s', ('remote_rejected_' + refusal,))
        elif refusal in ('conflict', 'stale_revision', 'completed'):
            database.execute('UPDATE outbox SET status=%s', (refusal,))
        elif refusal == 'acknowledged':
            database.execute("UPDATE outbox SET acknowledgement='{}'::jsonb")
        elif refusal == 'leased':
            database.execute("UPDATE outbox SET lease_token='live',leased_until=now()+interval '1 minute'")
        elif refusal == 'unattempted':
            database.execute('UPDATE outbox SET attempts=0')
        original = database.execute('SELECT * FROM outbox WHERE game_id=%s', (game,)).fetchone()
    result = auth_retry_cli(store, 'wrong-workspace' if refusal == 'workspace' else workspace, original['delivery_id'], tmp_path)
    assert result.returncode != 0
    with store.connect() as database:
        assert database.execute('SELECT * FROM outbox WHERE game_id=%s', (game,)).fetchone() == original


def test_auth_retry_preserves_predecessor_and_acknowledgement_fences(state):
    store, workspace, _ = state
    game = imported(state)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401))) as client:
        assert deliver_one(store, 'http://fixture/evidence', client=client, token='wrong-' + 'w' * 40)
    store.import_pgn(workspace, 'pgn', 'miss', 'Learner', (FIXTURES / 'alternative.pgn').read_bytes())
    drain(store)
    with store.connect() as database:
        assert database.execute('SELECT count(*) AS n FROM outbox').fetchone()['n'] == 1
        assert database.execute('SELECT count(*) AS n FROM accepted_heads').fetchone()['n'] == 0
    ready_outbox(store)
    with learning_receiver(store) as (endpoint, _, requests):
        assert deliver_one(store, endpoint, token='correct-' + 'c' * 40)
        drain(store)
        assert deliver_one(store, endpoint, token='correct-' + 'c' * 40)
        assert len(requests) == 2 and requests[0] != requests[1]


@contextmanager
def learning_receiver(store, *, replies=None, lose_first=False, actual_snapshot=None):
    class Received(dict):
        pass
    received=Received()
    requests=[]
    replies=list(replies or ["completed"])
    heads={}
    received.heads=heads
    committed={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):
            pass
        def do_POST(self):
            payload=EvidenceDelivery.model_validate_json(self.rfile.read(int(self.headers["Content-Length"])))
            with store.connect() as database:
                assert database.execute("SELECT count(*) AS count FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction'").fetchone()["count"]==0
            document=payload.model_dump(mode="json")
            key=payload.delivery_id
            assert key not in received or received[key]==document
            received[key]=document
            requests.append(key)
            status=replies.pop(0) if len(replies)>1 else replies[0]
            if status=="completed" and key not in committed:
                for withdrawal in payload.withdrawals:
                    assert heads[withdrawal.obligation_id]==withdrawal.expected_obligation_revision
                    del heads[withdrawal.obligation_id]
                for obligation in payload.obligations:
                    assert obligation.obligation_revision>heads.get(obligation.obligation_id,0)
                    heads[obligation.obligation_id]=obligation.obligation_revision
            acknowledgement=committed.get(key) or {"contract_version":2,"workspace_id":payload.workspace_id,"delivery_id":key,
                             "evidence_id":payload.evidence_id,"evidence_revision":payload.evidence_revision,
                             "status":status,"accepted_obligation_ids":[item.obligation_id for item in payload.obligations] if status=="completed" else [],
                             "learning_revision":1 if status=="completed" else None,
                             "reason":None if status=="completed" else "receiver_fixture"}
            if actual_snapshot is not None:
                from tempo_learning_remediation import PostgresRepository
                from tempo_learning_remediation.models import AcceptedDecision, ReceiveContext
                from tempo_game_evidence.snapshot import parse_snapshot
                from tempo_chess_core import canonical_fen_key
                snapshot=parse_snapshot(actual_snapshot)
                class Authority:
                    def decision(self,card_id,decision_id,publication_id,publication_generation):
                        for definition in snapshot.publication.definitions:
                            if (definition.card_id,definition.decision_id,snapshot.publication.publication_id,snapshot.publication.publication_generation)==(card_id,decision_id,publication_id,publication_generation):
                                return AcceptedDecision(card_id,definition.exercise_revision,decision_id,publication_id,publication_generation,
                                    canonical_fen_key(definition.starting_fen),tuple(line[0] for line in definition.specification.accepted_lines),payload.workspace_id)
                # Explicit synthetic source/content authority; this is a test receiver,
                # not production authentication or runtime adoption.
                context=ReceiveContext(snapshot.publication.workspace_id,payload.game_id,payload.game_revision,
                    payload.evidence_id,payload.evidence_revision,datetime.now(timezone.utc),Authority())
                with psycopg.connect(os.environ['TEMPO_GAME_EVIDENCE_TEST_LEARNING_URL']) as receiver_database:
                    receiver_database.execute('SELECT 1')
                    repository=PostgresRepository(receiver_database,payload.workspace_id)
                    receipt=(repository.receive(payload,context) if status=='completed' else repository.record_status(payload,status,'fixture_resolution'))
                    acknowledgement=receipt.model_dump(mode='json')
                    assert receipt.status==status
            if status=="completed":
                committed[key]=acknowledgement
            if lose_first and len(requests)==1:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(acknowledgement).encode())
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/evidence",received,requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_native_pgn_findings_commit_with_outbox_and_empty_comparison(state):
    store,workspace,_=state
    game=imported(state)
    report=store.report(workspace,game)
    assert report["findings"][0]["status"]=="missed" and report["delivery"]["status"]=="pending"
    with store.connect() as database:
        row=database.execute("SELECT payload,delivery_id FROM outbox WHERE game_id=%s",(game,)).fetchone()
        assert row["payload"]["delivery_id"]==row["delivery_id"]
    alternative=store.import_pgn(workspace,"pgn","alternative","Learner",(FIXTURES/"alternative.pgn").read_bytes())
    empty=store.import_pgn(workspace,"pgn","empty","Learner",(FIXTURES/"empty.pgn").read_bytes())
    drain(store)
    assert store.report(workspace,alternative)["findings"][0]["status"]=="matched"
    assert store.report(workspace,empty)["findings"]==[]
    assert store.report(workspace,empty)["delivery"]["status"]=="not_required"


def test_duplicate_source_and_snapshot_refresh_reuse_verified_index(state,monkeypatch):
    store,workspace,value=state
    game=imported(state)
    import tempo_game_evidence.store as storage
    def forbidden(*_args,**_kwargs):
        raise AssertionError("unchanged_source_was_reparsed")
    monkeypatch.setattr(storage,"normalize_pgn",forbidden)
    monkeypatch.setattr(storage,"replay_positions",forbidden)
    assert store.import_pgn(workspace,"pgn","miss","Learner",(FIXTURES/"miss.pgn").read_bytes())==game
    value["revision"]=2
    store.load_snapshot(value)
    assert store.report(workspace,game)["readiness"]=="pending"
    drain(store)
    assert store.report(workspace,game)["comparison_revision"]==2
    with store.connect() as database:
        assert database.execute("SELECT revision FROM games WHERE game_id=%s",(game,)).fetchone()["revision"]==1


@pytest.mark.parametrize("change",["source","snapshot"])
def test_changed_inputs_during_computation_fence_publication(state,change):
    store,workspace,value=state
    game=imported(state)
    store.request_comparison(game)
    def mutate():
        if change=="source":
            store.import_pgn(workspace,"pgn","miss","Learner",(FIXTURES/"alternative.pgn").read_bytes())
        else:
            value["revision"]=2
            store.load_snapshot(value)
    assert store.process_one(before_publish=mutate)
    assert store.report(workspace,game)["comparison_revision"]==1
    drain(store)
    assert store.report(workspace,game)["comparison_revision"]==2


def test_rollback_never_leaves_comparison_without_delivery_intent(state):
    store,workspace,value=state
    store.load_snapshot(value)
    game=store.import_pgn(workspace,"pgn","miss","Learner",(FIXTURES/"miss.pgn").read_bytes())
    with psycopg.connect(os.environ["TEMPO_GAME_EVIDENCE_MIGRATION_URL"]) as database:
        database.execute("CREATE OR REPLACE FUNCTION reject_fixture_outbox() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.game_id='"+game+"' THEN RAISE EXCEPTION 'fixture rollback'; END IF; RETURN NEW; END $$")
        database.execute("CREATE TRIGGER reject_fixture BEFORE INSERT ON outbox FOR EACH ROW EXECUTE FUNCTION reject_fixture_outbox()")
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            drain(store)
        with store.connect() as database:
            assert database.execute("SELECT count(*) AS count FROM comparisons WHERE game_id=%s",(game,)).fetchone()["count"]==0
    finally:
        with psycopg.connect(os.environ["TEMPO_GAME_EVIDENCE_MIGRATION_URL"]) as database:
            database.execute("DROP TRIGGER reject_fixture ON outbox")
            database.execute("DROP FUNCTION reject_fixture_outbox()")


def test_lost_acknowledgement_and_fresh_process_replay_have_one_logical_effect(state):
    store,workspace,_=state
    game=imported(state)
    with learning_receiver(store,lose_first=True) as (endpoint,received,requests):
        assert deliver_one(store,endpoint)
        assert store.report(workspace,game)["delivery"]["status"]=="retryable_failure"
        ready_outbox(store)
        result=subprocess.run([sys.executable,"-m","tempo_game_evidence.cli","work","--once","--learning-endpoint",endpoint],capture_output=True,text=True)
        assert result.returncode==0,result.stdout
        assert store.report(workspace,game)["delivery"]["status"]=="completed"
        assert len(received)==1 and len(requests)==2


@pytest.mark.parametrize("status",["accepted_pending","terminal_failure","stale_revision","conflict"])
def test_receiver_states_remain_truthful_and_corrections_wait_for_receipts(state,status):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store,replies=[status]) as (endpoint,_,_):
        assert deliver_one(store,endpoint)
    assert store.report(workspace,game)["delivery"]["status"]==status
    value["revision"]=2
    store.load_snapshot(value)
    drain(store)
    report=store.report(workspace,game)
    assert report["readiness"]=="ready"
    assert report["delivery"]["status"]==("waiting_predecessor" if status=="accepted_pending" else "blocked" if status in {"conflict","stale_revision"} else "pending")
    if status in {"conflict","stale_revision"}:
        assert report["delivery"]["reason"]=="receiver_authority_unresolved"
        with learning_receiver(store) as (endpoint,received,_):
            assert not deliver_one(store,endpoint)
            assert not received


@pytest.mark.parametrize("replacement",["alternative","empty"])
def test_exact_v2_withdrawal_removes_only_the_corrected_source_contribution(state,replacement):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store) as (endpoint,received,requests):
        assert deliver_one(store,endpoint)
        store.import_pgn(workspace,"pgn","miss","Learner",(FIXTURES/(replacement+".pgn")).read_bytes())
        drain(store)
        assert deliver_one(store,endpoint)
        assert len(requests)==2
        old,new=list(received.values())
        assert new["obligations"]==[] and len(new["withdrawals"])==1
        assert new["withdrawals"][0]["obligation_id"]==old["obligations"][0]["obligation_id"]
        assert new["withdrawals"][0]["expected_obligation_revision"]==1
    assert store.report(workspace,game)["delivery"]["status"]=="completed"


def test_partial_snapshot_cannot_imply_withdrawal_of_an_accepted_miss(state):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store) as (endpoint,received,_):
        assert deliver_one(store,endpoint)
        value.update(revision=2,complete=False,routes=[])
        store.load_snapshot(value)
        drain(store)
        assert not deliver_one(store,endpoint)
        assert len(received)==1
    assert store.report(workspace,game)["delivery"]["status"]=="blocked"


def test_source_reversion_retains_revisions_and_cannot_reuse_stale_computation(state):
    store,workspace,_=state
    game=imported(state)
    for source in ("alternative","miss"):
        store.import_pgn(workspace,"pgn","miss","Learner",(FIXTURES/(source+".pgn")).read_bytes())
        drain(store)
    report=store.report(workspace,game)
    assert report["comparison_revision"]==3 and report["game"]["revision"]==3
    assert report["findings"][0]["status"]=="missed" and report["delivery"]["status"]=="pending"
    with store.connect() as database:
        assert database.execute("SELECT count(*) AS count FROM game_sources WHERE game_id=%s",(game,)).fetchone()["count"]==3


def test_bounded_rescope_and_expired_lease_resume_without_lost_jobs(state):
    store,workspace,value=state
    for index in range(65):
        store.import_pgn(workspace,"pgn",str(index),"Learner",(FIXTURES/"empty.pgn").read_bytes())
    store.load_snapshot(value)
    assert store.process_one()
    with store.connect() as database:
        row=database.execute("SELECT requested,published,cursor FROM jobs WHERE kind='rescope'").fetchone()
        assert row["requested"]>row["published"] and row["cursor"]
    job=store.claim_job()
    with store.connect() as database:
        database.execute("UPDATE jobs SET leased_until=now()-interval '1 second' WHERE job_key=%s",(job["job_key"],))
    drain(store)
    with store.connect() as database:
        assert database.execute("SELECT count(*) AS count FROM jobs WHERE requested>published").fetchone()["count"]==0
        assert database.execute("SELECT count(*) AS count FROM comparisons").fetchone()["count"]==65


def test_cli_pgn_to_report_and_unknown_source_are_inspectable(state):
    store,workspace,value=state
    store.load_snapshot(value)
    def cli(*arguments):
        result=subprocess.run([sys.executable,"-m","tempo_game_evidence.cli",*arguments],capture_output=True,text=True)
        assert result.returncode==0,result.stdout
        return json.loads(result.stdout)
    game=cli("import-pgn",str(FIXTURES/"miss.pgn"),"--workspace",workspace,"--player","Learner","--source-id","cli-fixture")["game_id"]
    cli("work","--max-jobs","64")
    assert cli("report",game,"--workspace",workspace)["findings"][0]["status"]=="missed"
    bad=store.import_pgn(workspace,"pgn","invalid","Learner",b"not a PGN")
    drain(store)
    assert store.report(workspace,bad)["readiness"]=="unknown"


def test_chesscom_saved_month_resumes_in_bounded_slices_without_refetch(state):
    store,workspace,_=state
    config=AccountImport(workspace_id=workspace,provider="chess.com",account="Learner",
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc))
    import_id=enqueue_import(store,config)
    games=[{"uuid":str(index),"end_time":int(config.since.timestamp()),"pgn":(FIXTURES/"miss.pgn").read_text()} for index in range(65)]
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={"games":games})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert import_one(store,client=client)
        assert len(store.list_games(workspace,limit=100))==64
        assert import_one(store,client=client)
    assert len(calls)==1 and len(store.list_games(workspace,limit=100))==65
    with store.connect() as database:
        assert database.execute("SELECT status FROM import_cursors WHERE import_id=%s",(import_id,)).fetchone()["status"]=="completed"


def test_rate_limited_provider_persists_retry_after_without_advancing_cursor(state):
    store,workspace,_=state
    config=AccountImport(workspace_id=workspace,provider="lichess",account="Learner",
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc))
    import_id=enqueue_import(store,config)
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(429,headers={"Retry-After":"3600"}))) as client:
        assert import_one(store,client=client)
        assert not import_one(store,client=client)
    with store.connect() as database:
        row=database.execute("SELECT status,cursor,next_attempt_at FROM import_cursors WHERE import_id=%s",(import_id,)).fetchone()
        assert row["status"]=="retrying" and row["cursor"]["seen"]==[]
        assert (row["next_attempt_at"]-datetime.now(timezone.utc)).total_seconds()>3500
    assert store.import_status(workspace,import_id)["status"]=="retrying"
    client=TestClient(create_app(store))
    assert client.get(f"/v1/imports/{import_id}",params={"workspace_id":workspace}).json()["status"]=="retrying"
    assert client.get(f"/v1/imports/{import_id}",params={"workspace_id":"another"}).status_code==404


def test_acknowledgement_mismatch_never_invents_completion(state):
    store,workspace,_=state
    game=imported(state)
    def handler(request):
        payload=json.loads(request.content)
        acknowledgement={key:payload[key] for key in ["contract_version","workspace_id","delivery_id","evidence_id","evidence_revision"]}
        acknowledgement.update(status="completed",learning_revision=1,reason=None,accepted_obligation_ids=[])
        return httpx.Response(200,json=acknowledgement)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert deliver_one(store,"http://learning.invalid/evidence",client=client)
    assert store.report(workspace,game)["delivery"]["status"]=="retryable_failure"


def test_native_database_restart_preserves_pending_delivery(state):
    store,workspace,_=state
    game=imported(state)
    container=os.environ["TEMPO_GAME_EVIDENCE_TEST_CONTAINER"]
    subprocess.run(["docker","restart",container],check=True,capture_output=True)
    deadline=time.monotonic()+30
    while True:
        try:
            report=store.report(workspace,game)
            break
        except psycopg.OperationalError:
            if time.monotonic()>deadline:
                raise
            time.sleep(.1)
    assert report["delivery"]["status"]=="pending"
    with learning_receiver(store) as (endpoint,_,_):
        assert deliver_one(store,endpoint)
    assert store.report(workspace,game)["delivery"]["status"]=="completed"


def test_runtime_role_cannot_migrate_or_connect_to_another_domain(state):
    store,_,_=state
    with store.connect() as database:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            database.execute("CREATE TABLE forbidden_schema_change(id integer)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.migrate()
    for forbidden_database in ("content_guard","learning_fixture"):
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(make_conninfo(store.database_url,dbname=forbidden_database))


def test_read_api_requires_workspace_and_exposes_separate_readiness(state):
    store,workspace,_=state
    game=imported(state)
    client=TestClient(create_app(store))
    assert client.get("/healthz").status_code==200
    assert client.get("/v1/games").status_code==422
    assert client.get("/v1/games",params={"workspace_id":workspace,"limit":101}).status_code==422
    assert client.get(f"/v1/games/{game}/comparison",params={"workspace_id":"another"}).status_code==404
    response=client.get(f"/v1/games/{game}/comparison",params={"workspace_id":workspace}).json()
    assert response["readiness"]=="ready" and response["delivery"]["status"]=="pending"


def test_provider_source_checkpoint_retry_and_cancellation(state):
    store,workspace,_=state
    config=AccountImport(workspace_id=workspace,provider="lichess",account="Learner",
                         since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc))
    import_id=enqueue_import(store,config)
    seen=[]
    def handle(request):
        seen.append(request)
        assert request.url.params.get("rated") is None
        return httpx.Response(200,content=json.dumps({"id":"provider-one","createdAt":int(config.since.timestamp()*1000),
                                                     "pgn":(FIXTURES/"miss.pgn").read_text()})+"\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert import_one(store,client=client)
    with store.connect() as database:
        assert database.execute("SELECT status FROM import_cursors WHERE import_id=%s",(import_id,)).fetchone()["status"]=="completed"
        assert database.execute("SELECT count(*) AS count FROM import_pages WHERE import_id=%s",(import_id,)).fetchone()["count"]==1
    assert len(seen)==1
    config=config.model_copy(update={"account":"Cancelled"})
    cancelled=enqueue_import(store,config)
    cancel_import(store,cancelled)
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail("cancelled_import_made_network_request"))) as client:
        assert not import_one(store,client=client)


@pytest.mark.parametrize('resolution,target',[('terminal_failure',1),('completed',2)])
def test_rejected_replacement_reconciles_exact_accepted_revision(state,resolution,target):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store,replies=['completed','accepted_pending',resolution,'completed']) as (endpoint,received,requests):
        assert deliver_one(store,endpoint)
        value['revision']=2
        store.load_snapshot(value)
        drain(store)
        assert deliver_one(store,endpoint)
        store.import_pgn(workspace,'pgn','miss','Learner',(FIXTURES/'empty.pgn').read_bytes())
        drain(store)
        assert not deliver_one(store,endpoint)
        ready_outbox(store)
        assert deliver_one(store,endpoint)
        drain(EvidenceStore(store.database_url))
        assert deliver_one(store,endpoint)
        correction=received[requests[-1]]
        assert correction['withdrawals'][0]['expected_obligation_revision']==target
        assert store.report(workspace,game)['delivery']['status']=='completed'
        assert not received.heads


def test_rejected_withdrawal_cannot_erase_missing_successor(state):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store,replies=['completed','accepted_pending','terminal_failure','completed']) as (endpoint,received,requests):
        assert deliver_one(store,endpoint)
        store.import_pgn(workspace,'pgn','miss','Learner',(FIXTURES/'empty.pgn').read_bytes())
        drain(store)
        assert deliver_one(store,endpoint)
        value['revision']=2
        store.load_snapshot(value)
        drain(store)
        ready_outbox(store)
        assert deliver_one(store,endpoint)
        drain(EvidenceStore(store.database_url))
        assert deliver_one(store,endpoint)
        assert received[requests[-1]]['withdrawals'][0]['expected_obligation_revision']==1
        assert store.report(workspace,game)['delivery']['status']=='completed'


def test_nonintersecting_snapshot_refresh_converges_without_replay(state,monkeypatch):
    store,workspace,value=state
    store.load_snapshot(value)
    raw=b'[White "Learner"]\n[Black "Other"]\n[Result "1/2-1/2"]\n[SetUp "1"]\n[FEN "8/8/8/8/8/8/4K3/7k w - - 0 1"]\n\n1. Kf3 Kg1 1/2-1/2\n'
    game=store.import_pgn(workspace,'pgn','custom','Learner',raw)
    drain(store)
    assert store.report(workspace,game)['readiness']=='ready'
    import tempo_game_evidence.store as storage
    def forbidden(*args,**kwargs):
        raise AssertionError('verified_index_replayed')
    monkeypatch.setattr(storage,'replay_positions',forbidden)
    monkeypatch.setattr(storage,'normalize_pgn',forbidden)
    value.update(revision=2,routes=[])
    store.load_snapshot(value)
    drain(store)
    assert store.report(workspace,game)['readiness']=='ready'
    assert store.report(workspace,game)['comparison_revision']==2


@pytest.mark.parametrize('invalid',[b'<html>temporary failure</html>',b'{"games":[{}]}'])
def test_malformed_provider_checkpoint_refetches_valid_page(state,invalid):
    store,workspace,_=state
    config=AccountImport(workspace_id=workspace,provider='chess.com',account='Learner',
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc))
    import_id=enqueue_import(store,config)
    responses=[invalid,json.dumps({'games':[{'uuid':'fixed','end_time':int(config.since.timestamp()),'pgn':(FIXTURES/'miss.pgn').read_text()}]}).encode()]
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,content=responses.pop(0))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert import_one(store,client=client)
        assert store.import_status(workspace,import_id)['cursor']['offset']==0
        assert store.import_status(workspace,import_id)['status']=='retrying'
        with store.connect() as database:
            database.execute('UPDATE import_cursors SET next_attempt_at=now() WHERE import_id=%s',(import_id,))
        assert import_one(EvidenceStore(store.database_url),client=client)
    assert len(calls)==2
    assert store.import_status(workspace,import_id)['status']=='completed'
    assert len(store.list_games(workspace))==1


def test_rate_limit_covers_other_accounts_without_starving_other_provider(state):
    store,workspace,_=state
    for account in ('Alpha','Beta'):
        enqueue_import(store,AccountImport(workspace_id=workspace,provider='lichess',account=account,
            since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc)))
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(429,headers={'Retry-After':'3600'}) if request.url.host=='lichess.org' else httpx.Response(200,json={'games':[]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert import_one(store,client=client)
        assert not import_one(EvidenceStore(store.database_url),client=client)
        enqueue_import(store,AccountImport(workspace_id=workspace,provider='chess.com',account='Other',
            since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc)))
        assert import_one(store,client=client)
    assert [request.url.host for request in calls]==['lichess.org','api.chess.com']


@pytest.mark.parametrize('resolution,target',[('terminal_failure',1),('completed',2)])
def test_real_remediation_reconciles_rejected_or_accepted_intermediate(state,resolution,target):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store,replies=['completed','accepted_pending',resolution,'completed'],actual_snapshot=value) as (endpoint,received,requests):
        assert deliver_one(store,endpoint)
        value['revision']=2
        store.load_snapshot(value)
        drain(store)
        assert deliver_one(store,endpoint)
        store.import_pgn(workspace,'pgn','miss','Learner',(FIXTURES/'empty.pgn').read_bytes())
        drain(store)
        ready_outbox(store)
        assert deliver_one(store,endpoint)
        # Simulate stopping after the acknowledgement transaction, before finalization.
        with store.connect() as database:
            assert database.execute("SELECT count(*) AS count FROM jobs WHERE kind='reconcile' AND published<requested").fetchone()['count']>0
        result=subprocess.run([sys.executable,'-m','tempo_game_evidence.cli','work','--max-jobs','64','--learning-endpoint',endpoint],capture_output=True,text=True)
        assert result.returncode==0,result.stdout
        assert received[requests[-1]]['withdrawals'][0]['expected_obligation_revision']==target
        assert not received.heads
    with psycopg.connect(os.environ['TEMPO_GAME_EVIDENCE_TEST_LEARNING_URL']) as database:
        assert database.execute('SELECT count(*) FROM remediation_obligation_heads WHERE workspace_id=%s AND active',(workspace,)).fetchone()[0]==0
        assert database.execute('SELECT count(*) FROM remediation_contributions WHERE workspace_id=%s',(workspace,)).fetchone()[0]==(2 if resolution=='completed' else 1)
    assert store.report(workspace,game)['delivery']['status']=='completed'


def test_actual_receiver_lost_response_has_one_contribution(state):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store,lose_first=True,actual_snapshot=value) as (endpoint,received,requests):
        assert deliver_one(store,endpoint)
        ready_outbox(store)
        result=subprocess.run([sys.executable,'-m','tempo_game_evidence.cli','work','--once','--learning-endpoint',endpoint],capture_output=True,text=True)
        assert result.returncode==0,result.stdout
        assert len(received)==1 and len(requests)==2
    with psycopg.connect(os.environ['TEMPO_GAME_EVIDENCE_TEST_LEARNING_URL']) as database:
        assert database.execute('SELECT count(*) FROM remediation_contributions WHERE workspace_id=%s',(workspace,)).fetchone()[0]==1
    assert store.report(workspace,game)['delivery']['status']=='completed'


def test_multiple_source_changes_while_waiting_use_latest_desired_state(state):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store,replies=['completed','accepted_pending','terminal_failure','completed']) as (endpoint,received,requests):
        assert deliver_one(store,endpoint)
        value['revision']=2
        store.load_snapshot(value)
        drain(store)
        assert deliver_one(store,endpoint)
        for source in ('empty','miss','alternative'):
            store.import_pgn(workspace,'pgn','miss','Learner',(FIXTURES/(source+'.pgn')).read_bytes())
            drain(store)
        with store.connect() as database:
            assert database.execute('SELECT count(*) AS count FROM outbox WHERE game_id=%s',(game,)).fetchone()['count']==2
        ready_outbox(store)
        assert deliver_one(store,endpoint)
        drain(store)
        assert deliver_one(store,endpoint)
        assert received[requests[-1]]['game_revision']==4
        assert received[requests[-1]]['withdrawals'][0]['expected_obligation_revision']==1
        assert not received.heads


def test_snapshot_change_mid_rescope_and_import_converge_after_restart(state,monkeypatch):
    store,workspace,value=state
    for index in range(65):
        store.import_pgn(workspace,'pgn',str(index),'Learner',(FIXTURES/'empty.pgn').read_bytes())
    store.load_snapshot(value)
    assert store.process_one()
    value.update(revision=2,routes=[])
    store.load_snapshot(value)
    added=store.import_pgn(workspace,'pgn','new','Learner',(FIXTURES/'miss.pgn').read_bytes())
    drain(EvidenceStore(store.database_url))
    for item in store.list_games(workspace,limit=100):
        report=store.report(workspace,item['game_id'])
        assert report['readiness']=='ready' and report['snapshot']['revision']==2
    assert store.report(workspace,added)['readiness']=='ready'


def test_equivalent_windows_share_persisted_identity_and_utc_month(state):
    store,workspace,_=state
    arguments=dict(workspace_id=workspace,provider='chess.com',account='Learner')
    offset=AccountImport(**arguments,since=datetime.fromisoformat('2026-10-01T00:00:00+02:00'),until=datetime.fromisoformat('2026-10-01T01:00:00+02:00'))
    utc=AccountImport(**arguments,since=offset.since.astimezone(timezone.utc),until=offset.until.astimezone(timezone.utc))
    import_id=enqueue_import(store,offset)
    assert enqueue_import(store,utc)==import_id
    assert store.import_status(workspace,import_id)['cursor']=={'year':2026,'month':9,'offset':0}
    with store.connect() as database:
        assert database.execute('SELECT count(*) AS count FROM import_cursors').fetchone()['count']==1


def test_concurrent_senders_replay_one_immutable_delivery(state):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    store,workspace,_=state
    game=imported(state)
    entered,release=Event(),Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        payload=json.loads(request.content)
        return httpx.Response(200,json={**{key:payload[key] for key in ('contract_version','workspace_id','delivery_id','evidence_id','evidence_revision')},
            'status':'completed','accepted_obligation_ids':[o['obligation_id'] for o in payload['obligations']],'learning_revision':1,'reason':None})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client,ThreadPoolExecutor(max_workers=2) as workers:
        first=workers.submit(deliver_one,store,'http://fixture/evidence',client=client)
        assert entered.wait(5)
        try:
            assert not workers.submit(deliver_one,EvidenceStore(store.database_url),'http://fixture/evidence',client=client).result(5)
        finally:
            release.set()
        assert first.result(5)
    assert store.report(workspace,game)['delivery']['status']=='completed'
    with store.connect() as database:
        assert database.execute('SELECT count(*) AS count FROM accepted_heads WHERE active').fetchone()['count']==1


def test_concurrent_provider_workers_own_one_request_after_cooldown(state):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    store,workspace,_=state
    for account in ('A','B'):
        enqueue_import(store,AccountImport(workspace_id=workspace,provider='lichess',account=account,
            since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc)))
    with store.connect() as database:
        database.execute("INSERT INTO provider_leases(provider,cooldown_until) VALUES('lichess',now()-interval '1 second')")
    entered,release=Event(),Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200,content=b'')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client,ThreadPoolExecutor(max_workers=2) as workers:
        first=workers.submit(import_one,store,client=client)
        assert entered.wait(5)
        try:
            assert not workers.submit(import_one,EvidenceStore(store.database_url),client=client).result(5)
        finally:
            release.set()
        assert first.result(5)


def test_saved_month_validates_later_slice_and_recovers_after_cancel(state):
    store,workspace,_=state
    config=AccountImport(workspace_id=workspace,provider='chess.com',account='Learner',
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc))
    import_id=enqueue_import(store,config)
    game={'uuid':'good','end_time':int(config.since.timestamp()),'pgn':(FIXTURES/'miss.pgn').read_text()}
    responses=[{'games':[game]*64+[{}]},{'games':[game]}]
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=responses.pop(0))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert import_one(store,client=client)
        assert not store.list_games(workspace)
        with store.connect() as database:
            database.execute('UPDATE import_cursors SET next_attempt_at=now()')
        assert import_one(store,client=client)
    assert len(calls)==2 and len(store.list_games(workspace))==1
    assert store.import_status(workspace,import_id)['status']=='completed'
    cancel_import(store,import_id)
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('cancelled_request'))) as client:
        assert not import_one(store,client=client)


@pytest.mark.parametrize('existing,delay,minimum',[(7200,3600,7100),(3600,7200,7100)])
def test_rate_limit_deadline_never_shortens_and_extends(state,existing,delay,minimum):
    store,workspace,_=state
    enqueue_import(store,AccountImport(workspace_id=workspace,provider='lichess',account='Learner',
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc)))
    def handler(request):
        # A second source of provider coordination establishes a deadline while
        # the first request is in flight. Its later limit must survive release.
        with store.connect() as database:
            database.execute("UPDATE provider_leases SET cooldown_until=now()+%s*interval '1 second'",(existing,))
        return httpx.Response(429,headers={'Retry-After':str(delay)})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert import_one(store,client=client)
    with store.connect() as database:
        assert database.execute('SELECT extract(epoch FROM cooldown_until-now()) AS seconds FROM provider_leases').fetchone()['seconds']>minimum
        row=database.execute('SELECT token,leased_until FROM provider_leases').fetchone()
        assert row['token'] is None and row['leased_until'] is None


def test_multi_bucket_correction_preserves_other_scope_obligations(state):
    import importlib.util
    spec=importlib.util.spec_from_file_location('evidence_test_factories',FIXTURES.parent/'tests/factories.py')
    factories=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(factories)
    store,workspace,_=state
    route=['g1h3','g8h6','h3g1','h6g8']*34
    observed=['g1f3','g8f6','f3g1','f6g8']*34
    value=factories.snapshot(moves=route,reentry=True,workspace=workspace)
    value['routes'][0]['bindings']=[{'ply':ply,'card_id':'card-shared','reentry':True} for ply in range(0,136,4)]
    store.load_snapshot(value)
    game=store.import_pgn(workspace,'pgn','buckets','Learner',factories.pgn(observed))
    drain(store)
    with learning_receiver(store) as (endpoint,received,requests):
        while deliver_one(store,endpoint):
            pass
        assert len(requests)==3 and len(received.heads)==34
        initial=set(received.heads)
        store.import_pgn(workspace,'pgn','buckets','Learner',factories.pgn(observed[:-4]+route[-4:]))
        drain(store)
        while deliver_one(store,endpoint):
            pass
        changes=[received[key] for key in requests[3:]]
        withdrawals=[item for payload in changes for item in payload['withdrawals']]
        assert len(withdrawals)==1 and withdrawals[0]['expected_obligation_revision']==1
        assert len(received.heads)==33
        assert initial-set(received.heads)=={withdrawals[0]['obligation_id']}
        corrected=next(payload for payload in changes if payload['withdrawals'])
        assert all(encounter['ply']>=128 for encounter in corrected['encounters'])
    assert store.report(workspace,game)['delivery']['status']=='completed'


def test_database_restart_preserves_reconciliation_cooldown_and_valid_checkpoint(state):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store,replies=['completed','accepted_pending','terminal_failure','completed']) as (endpoint,received,requests):
        assert deliver_one(store,endpoint)
        value['revision']=2
        store.load_snapshot(value)
        drain(store)
        assert deliver_one(store,endpoint)
        store.import_pgn(workspace,'pgn','miss','Learner',(FIXTURES/'empty.pgn').read_bytes())
        drain(store)
        month=AccountImport(workspace_id=workspace,provider='chess.com',account='Learner',
            since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc))
        month_id=enqueue_import(store,month)
        games=[{'uuid':str(index),'end_time':int(month.since.timestamp()),'pgn':(FIXTURES/'miss.pgn').read_text()} for index in range(65)]
        with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'games':games}))) as client:
            assert import_one(store,client=client)
        enqueue_import(store,AccountImport(workspace_id=workspace,provider='lichess',account='RateLimited',since=month.since,until=month.until))
        # Cool down Lichess directly in the owning disposable database; its HTTP
        # path is covered separately and no real wait is needed here.
        with store.connect() as database:
            database.execute("INSERT INTO provider_leases(provider,cooldown_until) VALUES('lichess',now()+interval '1 hour')")
            database.execute("UPDATE jobs SET published=0 WHERE kind='reconcile' AND game_id=%s",(game,))
        subprocess.run(['docker','restart',os.environ['TEMPO_GAME_EVIDENCE_TEST_CONTAINER']],check=True,capture_output=True)
        deadline=time.monotonic()+30
        while True:
            try:
                assert store.import_status(workspace,month_id)['cursor']['offset']==64
                break
            except psycopg.OperationalError:
                if time.monotonic()>deadline:
                    raise
                time.sleep(.1)
        with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('durable_checkpoint_or_cooldown_lost'))) as client:
            assert import_one(EvidenceStore(store.database_url),client=client)
            assert not import_one(store,client=client)
        ready_outbox(store)
        assert deliver_one(store,endpoint)
        drain(EvidenceStore(store.database_url))
        # Account imports have their own evidence deliveries; inspect and deliver
        # the corrected source first by its game, without changing unrelated data.
        for _ in range(70):
            if store.report(workspace,game)['delivery']['status']=='completed':
                break
            assert deliver_one(store,endpoint)
        assert store.report(workspace,game)['delivery']['status']=='completed'
        corrections=[payload for payload in received.values() if payload['game_id']==game and payload['withdrawals']]
        assert corrections[0]['withdrawals'][0]['expected_obligation_revision']==1
        assert store.import_status(workspace,month_id)['status']=='completed'


def test_version_one_migration_recovers_completed_history_in_bounded_pages(state):
    from importlib.resources import files
    from copy import deepcopy
    from tempo_game_evidence.payloads import make_delivery
    from tempo_game_evidence.snapshot import ComparisonSnapshot
    store,workspace,value=state
    game=imported(state)
    with store.connect() as database:
        source=database.execute('SELECT * FROM games WHERE game_id=%s',(game,)).fetchone()
        report=database.execute('SELECT report FROM comparisons WHERE game_id=%s',(game,)).fetchone()['report']
    upgrade_url=os.environ['TEMPO_GAME_EVIDENCE_TEST_UPGRADE_URL']
    upgrade=EvidenceStore(upgrade_url)
    with upgrade.connect() as database:
        database.execute(files('tempo_game_evidence').joinpath('migrations/001.sql').read_text())
        database.execute('INSERT INTO workspace_inputs(workspace_id,snapshot_digest) VALUES(%s,%s)',(workspace,report['snapshot']['digest']))
        database.execute('INSERT INTO snapshots VALUES(%s,%s,%s,%s,%s)',(report['snapshot']['digest'],workspace,value['snapshot_id'],1,Jsonb(value)))
        database.execute('INSERT INTO games(game_id,workspace_id,provider,provider_game_id,player,revision,input_digest,source_digest,raw_source,document,positions,index_receipt,report_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,65)',
            (game,workspace,source['provider'],source['provider_game_id'],source['player'],1,source['input_digest'],source['source_digest'],source['raw_source'],Jsonb(source['document']),Jsonb(source['positions']),Jsonb(source['index_receipt'])))
        for revision in range(1,66):
            current=deepcopy(report)
            current['comparison_revision']=revision
            database.execute('INSERT INTO comparisons VALUES(%s,%s,%s,%s)',(game,revision,str(revision),Jsonb(current)))
            payload=make_delivery(source['document'],report['findings'],revision,ComparisonSnapshot.model_validate(value).publication)
            acknowledgement={**{key:payload[key] for key in ('contract_version','workspace_id','delivery_id','evidence_id','evidence_revision')},'status':'completed','learning_revision':revision,'reason':None,'accepted_obligation_ids':[o['obligation_id'] for o in payload['obligations']]}
            database.execute("INSERT INTO outbox(delivery_id,game_id,evidence_revision,bucket,payload,status,attempts,acknowledgement) VALUES(%s,%s,%s,0,%s,'completed',1,%s)",(payload['delivery_id'],game,revision,Jsonb(payload),Jsonb(acknowledgement)))
    upgrade.migrate()
    upgrade.migrate()
    assert upgrade.process_one()
    with upgrade.connect() as database:
        assert not database.execute('SELECT heads_initialized FROM games').fetchone()['heads_initialized']
        assert database.execute('SELECT obligation_revision FROM accepted_heads').fetchone()['obligation_revision']==64
    drain(upgrade)
    with upgrade.connect() as database:
        assert database.execute('SELECT heads_initialized FROM games').fetchone()['heads_initialized']
        assert database.execute('SELECT obligation_revision FROM accepted_heads').fetchone()['obligation_revision']==65
        assert database.execute('SELECT count(*) AS count FROM evidence_schema_migrations').fetchone()['count']==3
    upgrade.import_pgn(workspace,'pgn','miss','Learner',(FIXTURES/'empty.pgn').read_bytes())
    drain(upgrade)
    with upgrade.connect() as database:
        correction=database.execute("SELECT payload FROM outbox WHERE status='pending'").fetchone()['payload']
        assert correction['withdrawals'][0]['expected_obligation_revision']==65


def test_expired_provider_worker_cannot_replace_newer_valid_checkpoint(state):
    store,workspace,_=state
    config=AccountImport(workspace_id=workspace,provider='chess.com',account='Learner',
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc))
    import_id=enqueue_import(store,config)
    def response(source):
        return httpx.Response(200,json={'games':[{'uuid':source,'end_time':int(config.since.timestamp()),'pgn':(FIXTURES/'miss.pgn').read_text()}]})
    def old_handler(request):
        with store.connect() as database:
            database.execute("UPDATE import_cursors SET leased_until=now()-interval '1 second'")
            database.execute("UPDATE provider_leases SET leased_until=now()-interval '1 second'")
        with httpx.Client(transport=httpx.MockTransport(lambda _:response('new-owner'))) as newer:
            assert import_one(EvidenceStore(store.database_url),client=newer)
        return response('expired-owner')
    with httpx.Client(transport=httpx.MockTransport(old_handler)) as client:
        assert import_one(store,client=client)
    assert store.import_status(workspace,import_id)['status']=='completed'
    with store.connect() as database:
        assert database.execute('SELECT provider_game_id FROM games').fetchone()['provider_game_id']=='new-owner'
        page=database.execute('SELECT raw_response,validation_status FROM import_pages').fetchone()
        assert page['validation_status']=='valid' and b'new-owner' in bytes(page['raw_response'])


def test_late_rejection_from_expired_sender_cannot_undo_completed_receipt(state):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    store,workspace,_=state
    game=imported(state)
    entered,release=Event(),Event()
    def acknowledgement(request,status):
        payload=json.loads(request.content)
        return httpx.Response(200,json={**{key:payload[key] for key in ('contract_version','workspace_id','delivery_id','evidence_id','evidence_revision')},
            'status':status,'accepted_obligation_ids':[o['obligation_id'] for o in payload['obligations']] if status=='completed' else [],
            'learning_revision':1 if status=='completed' else None,'reason':None if status=='completed' else 'fixture_rejected'})
    def old_handler(request):
        entered.set()
        assert release.wait(5)
        return acknowledgement(request,'terminal_failure')
    with httpx.Client(transport=httpx.MockTransport(old_handler)) as older,ThreadPoolExecutor(max_workers=2) as workers:
        first=workers.submit(deliver_one,store,'http://fixture/evidence',client=older)
        assert entered.wait(5)
        try:
            with store.connect() as database:
                database.execute("UPDATE outbox SET leased_until=now()-interval '1 second'")
            with httpx.Client(transport=httpx.MockTransport(lambda request:acknowledgement(request,'completed'))) as newer:
                assert deliver_one(EvidenceStore(store.database_url),'http://fixture/evidence',client=newer)
        finally:
            release.set()
        assert first.result(5)
    drain(store)
    assert store.report(workspace,game)['delivery']['status']=='completed'
    with store.connect() as database:
        assert database.execute('SELECT count(*) AS count FROM accepted_heads WHERE active').fetchone()['count']==1


def test_oversized_provider_response_stays_explicitly_blocked(state):
    from tempo_game_evidence.source import MAX_SOURCE_BYTES
    store,workspace,_=state
    import_id=enqueue_import(store,AccountImport(workspace_id=workspace,provider='lichess',account='Learner',
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc)))
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'x'*(MAX_SOURCE_BYTES+1)))) as client:
        assert import_one(store,client=client)
        assert not import_one(store,client=client)
    status=store.import_status(workspace,import_id)
    assert status['status']=='blocked' and status['reason']=='remote_response_bound_exceeded'
    assert status['cursor']['seen']==[] and not store.list_games(workspace)


def test_invalid_historical_completed_receipt_blocks_projection_recovery(state):
    store,workspace,_=state
    game=imported(state)
    with learning_receiver(store) as (endpoint,_,_):
        assert deliver_one(store,endpoint)
    with store.connect() as database:
        database.execute("UPDATE outbox SET acknowledgement=jsonb_set(acknowledgement,'{accepted_obligation_ids}','[]')")
        database.execute('DELETE FROM accepted_heads')
        database.execute('UPDATE games SET heads_initialized=false')
    drain(store)
    report=store.report(workspace,game)
    assert report['delivery']['status']=='blocked' and report['delivery']['reason']=='invalid_accepted_history'
    with store.connect() as database:
        assert database.execute('SELECT count(*) AS count FROM accepted_heads').fetchone()['count']==0


def test_deferred_game_does_not_starve_other_game_delivery(state):
    store,workspace,value=state
    first=imported(state)
    second=store.import_pgn(workspace,'pgn','second','Learner',(FIXTURES/'miss.pgn').read_bytes())
    drain(store)
    earlier,later=sorted([first,second])
    with store.connect() as database:
        database.execute("UPDATE outbox SET attempts=1,status='accepted_pending',next_attempt_at=now()+interval '1 hour' WHERE game_id=%s",(earlier,))
    value['revision']=2
    store.load_snapshot(value)
    drain(store)
    with learning_receiver(store) as (endpoint,received,_):
        assert deliver_one(store,endpoint)
        assert next(iter(received.values()))['game_id']==later
    assert store.report(workspace,earlier)['delivery']['status']=='waiting_predecessor'


def test_repeated_malformed_pages_remain_refetchable_until_valid(state):
    store,workspace,_=state
    import_id=enqueue_import(store,AccountImport(workspace_id=workspace,provider='lichess',account='Learner',
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc)))
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,content=b'not-json' if len(calls)<4 else b'')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for attempt in range(4):
            with store.connect() as database:
                database.execute('UPDATE import_cursors SET next_attempt_at=now()')
            assert import_one(store,client=client)
            status=store.import_status(workspace,import_id)
            assert status['cursor']['seen']==[]
            assert status['status']==('retrying' if attempt<3 else 'completed')
    assert len(calls)==4 and not store.list_games(workspace)


def test_cancellation_during_invalid_checkpoint_recovery_prevents_commits(state):
    store,workspace,_=state
    import_id=enqueue_import(store,AccountImport(workspace_id=workspace,provider='chess.com',account='Learner',
        since=datetime(2026,10,1,tzinfo=timezone.utc),until=datetime(2026,10,2,tzinfo=timezone.utc)))
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(200,content=b'invalid')
        cancel_import(store,import_id)
        return httpx.Response(200,json={'games':[{'uuid':'cancelled','end_time':1790812800,'pgn':(FIXTURES/'miss.pgn').read_text()}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert import_one(store,client=client)
        with store.connect() as database:
            database.execute('UPDATE import_cursors SET next_attempt_at=now()')
        assert import_one(store,client=client)
    assert store.import_status(workspace,import_id)['status']=='cancelled'
    assert not store.list_games(workspace)


@pytest.mark.parametrize('historical_first', [True, False])
def test_A_snapshot_installation_orders_preserve_current_generation(state, historical_first):
    store, _, _ = state
    historical = json.loads((FIXTURES / 'bridge-historical-snapshot-v2.json').read_text())
    successor = json.loads((FIXTURES / 'bridge-successor-snapshot-v2.json').read_text())
    workspace = successor['publication']['workspace_id']
    if historical_first:
        old_digest = store.load_snapshot(historical)
        current_digest = store.load_snapshot(successor)
        assert old_digest != current_digest
    else:
        current_digest = store.load_snapshot(successor)
        with pytest.raises(ValueError, match='stale_snapshot_generation'):
            store.load_snapshot(historical)
    with store.connect() as database:
        row = database.execute('SELECT snapshot_digest FROM workspace_inputs WHERE workspace_id=%s', (workspace,)).fetchone()
    assert row['snapshot_digest'] == current_digest
    assert store.load_snapshot(successor) == current_digest
    game = store.import_pgn(workspace, 'pgn', 'current-generation', 'Learner', b'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 c5 2. Nf3 1-0')
    drain(store)
    assert store.report(workspace, game)['snapshot']['publication_generation'] == 2


def test_A_post_retirement_snapshot_cannot_claim_current_serving(state):
    store, _, _ = state
    active = json.loads((FIXTURES / 'bridge-snapshot-v2.json').read_text())
    retired = json.loads((FIXTURES / 'bridge-retired-snapshot-v2.json').read_text())
    workspace = active['publication']['workspace_id']
    original_digest = store.load_snapshot(active)
    game = store.import_pgn(workspace, 'pgn', 'retired-comparison', 'Learner', b'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. c4 1-0')
    drain(store)
    # Establish a genuine accepted receipt in the native remediation database;
    # a never-delivered pending candidate may legitimately be superseded.
    with learning_receiver(store, actual_snapshot=active) as (endpoint, received, requests):
        assert deliver_one(store, endpoint)
    drain(store)
    with store.connect() as database:
        assert database.execute('SELECT count(*) AS count FROM accepted_heads WHERE active').fetchone()['count'] > 0
    with store.connect() as database:
        deliveries = database.execute('SELECT delivery_id,status,acknowledgement FROM outbox ORDER BY delivery_id').fetchall()
    retired_digest = store.load_snapshot(retired)
    assert retired_digest != original_digest
    drain(store)
    report = store.report(workspace, game)
    assert report['readiness'] == 'unknown' and report['snapshot']['digest'] == retired_digest
    with store.connect() as database:
        assert database.execute('SELECT count(*) AS count FROM snapshots').fetchone()['count'] == 2
        assert database.execute('SELECT count(*) AS count FROM accepted_heads WHERE active').fetchone()['count'] > 0
        assert database.execute('SELECT delivery_id,status,acknowledgement FROM outbox ORDER BY delivery_id').fetchall() == deliveries


def test_C3_export_derivation_upgrade_preserves_reserved_original_bytes(state,tmp_path,monkeypatch):
    from tempo_game_evidence import analysis_export
    from tempo_game_evidence.analysis_schema import verify_directory
    store,_,_=state
    snapshot=json.loads((FIXTURES/'bridge-snapshot-v2.json').read_text())
    workspace=snapshot['publication']['workspace_id']
    store.load_snapshot(snapshot)
    game=store.import_pgn(workspace,'pgn','derivation-upgrade','Learner',
        b'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 c5 2. Nf3 1-0')
    drain(store)
    with monkeypatch.context() as previous:
        previous.setattr(analysis_export,'EXPORT_DERIVATION_VERSION',1)
        original=analysis_export.create_directory_export(store,workspace,[game],tmp_path/'original')
    original_bytes={path.name:path.read_bytes() for path in (tmp_path/'original').iterdir()}
    current=analysis_export.create_directory_export(store,workspace,[game],tmp_path/'current')
    assert current['input_id']!=original['input_id']
    resumed=analysis_export.create_directory_export(store,workspace,[game],tmp_path/'original')
    assert resumed==original
    assert {path.name:path.read_bytes() for path in (tmp_path/'original').iterdir()}==original_bytes
    assert verify_directory(tmp_path/'original')['status']=='verified'


def test_C3_dense_80_game_export_keeps_scope_and_closes_source_transaction(state, tmp_path, monkeypatch):
    """Byte packing preserves all selected observations and complete encountered scopes."""
    from tempo_game_evidence import analysis_export
    from tempo_game_evidence.analysis_schema import verify_directory
    from tempo_game_evidence.snapshot import parse_snapshot
    store, _, _ = state
    snapshot = json.loads((FIXTURES / 'bridge-snapshot-v2.json').read_text())
    workspace = snapshot['publication']['workspace_id']
    store.load_snapshot(snapshot)
    pgn = b'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 1-0'
    games = [store.import_pgn(workspace, 'pgn', f'dense-{index}', 'Learner', pgn)
             for index in range(80)]
    drain(store)
    original = analysis_export.deviations
    traversal_calls = []
    def checked_traversal(*arguments, **keywords):
        with store.connect() as database:
            assert database.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'").fetchone()['n'] == 0
        traversal_calls.append(arguments[0]['game_id'])
        return original(*arguments, **keywords)
    monkeypatch.setattr(analysis_export, 'deviations', checked_traversal)
    monkeypatch.setattr(analysis_export, 'MAX_PAGE_BYTES', 8192)
    output = tmp_path / 'dense-complete'
    manifest = analysis_export.create_directory_export(store, workspace, games, output)
    assert verify_directory(output)['status'] == 'verified'
    assert sorted(traversal_calls) == sorted(games)
    pages = [json.loads((output / reference['filename']).read_text()) for reference in manifest['pages']]
    assert all(len(analysis_export.canonical(page).encode()) <= 8192 for page in pages)
    exported = {kind: [item for page in pages if page['kind'] == kind for item in page['items']]
                for kind in analysis_export.KINDS}
    assert sorted(item['game_id'] for item in exported['games']) == sorted(games)
    assert len(exported['comparisons']) == 80
    assert len(exported['positions']) == 80 * 4
    assert len([page for page in pages if page['kind'] == 'comparisons']) > 2
    encountered = {item['comparison_key'] for item in exported['positions']
                   if item['move'] is not None and item['turn'] == 'black'}
    expected = [item for item in parse_snapshot(snapshot).content_export['legal_replies']
                if item['position_key'] in encountered]
    assert exported['legal_replies'] == expected
    assert len(expected) == 1 < len(snapshot['content_export']['legal_replies'])
    assert exported['routes'] == snapshot['content_export']['routes']
