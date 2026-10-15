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
from tempo_game_evidence_contracts.v1 import EvidenceDelivery

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
            if lose_first and len(requests)==1:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            status=replies.pop(0) if len(replies)>1 else replies[0]
            acknowledgement={"contract_version":1,"workspace_id":payload.workspace_id,"delivery_id":key,
                             "evidence_id":payload.evidence_id,"evidence_revision":payload.evidence_revision,
                             "status":status,"accepted_obligation_ids":[item.obligation_id for item in payload.obligations],
                             "learning_revision":1 if status=="completed" else None,
                             "reason":None if status=="completed" else "receiver_fixture"}
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
def test_receiver_states_remain_truthful_and_corrections_are_durable_blocked_intent(state,status):
    store,workspace,value=state
    game=imported(state)
    with learning_receiver(store,replies=[status]) as (endpoint,_,_):
        assert deliver_one(store,endpoint)
    assert store.report(workspace,game)["delivery"]["status"]==status
    value["revision"]=2
    store.load_snapshot(value)
    drain(store)
    report=store.report(workspace,game)
    assert report["readiness"]=="ready" and report["delivery"]["status"]=="blocked"
    assert report["delivery"]["reason"]=="shared_correction_protocol_required"


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
