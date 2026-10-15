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
from tempo_game_evidence.delivery import deliver_one
from tempo_game_evidence.providers import AccountImport, enqueue_import, import_one, cancel_import
from tempo_game_evidence_contracts.v2 import EvidenceDelivery

# This fixture corpus is deliberately local to the owning service.
FIXTURES=Path(__file__).resolve().parents[1]/"fixtures"


@pytest.fixture
def state():
    assert os.environ.get("TEMPO_GAME_EVIDENCE_TEST_CONTAINER","").startswith("tempo-game-evidence-41-")
    store=EvidenceStore(os.environ["TEMPO_GAME_EVIDENCE_DATABASE_URL"])
    # This is exclusively the fresh cluster provisioned by run_postgres.py.
    with psycopg.connect(os.environ["TEMPO_GAME_EVIDENCE_MIGRATION_URL"]) as database:
        database.execute("TRUNCATE import_pages,import_cursors,provider_leases,outbox,comparisons,jobs,game_sources,games,snapshots,workspace_inputs CASCADE")
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


def ready_outbox(store):
    with store.connect() as database:
        database.execute("UPDATE outbox SET next_attempt_at=now(),leased_until=NULL WHERE status IN ('retryable_failure','accepted_pending')")


@contextmanager
def learning_receiver(store, *, replies=None, lose_first=False):
    received={}
    requests=[]
    replies=list(replies or ["completed"])
    heads={}
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
    assert report["delivery"]["status"]==("waiting_predecessor" if status=="accepted_pending" else "pending")


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
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(make_conninfo(store.database_url,dbname="content_guard"))


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
