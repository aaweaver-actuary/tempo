"""Native owner regressions. All resources originate in run_postgres.py."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import psycopg
from psycopg.conninfo import make_conninfo
import pytest
from fastapi.testclient import TestClient
from tempo_analysis_jobs_contracts.v1 import MaiaResult, JobStatus
from tempo_intelligence.api import create_app
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import digest
from tempo_intelligence.migrate import migrate
from tempo_intelligence.models import RequestInput
from tempo_intelligence.store import Store

from .support import request_value, register, ready, maia_result, owner_execute


def submit(store,value=None):
    return store.submit(RequestInput.model_validate(value or request_value()))


def accepted_explorer(state):
    store,_,_=state
    register(state)
    response=submit(store)
    claim=store.claim()
    assert claim
    assert store.publish(claim,{"schema_version":1,"source":"lichess","total_games":1})
    return store.inspect("request")["sources"][0]["evidence_id"]


def test_fresh_and_idempotent_migrations_and_workspace_rebind_refusal(state):
    upgrade=os.environ["TEMPO_INTELLIGENCE_TEST_UPGRADE_URL"]
    migrate(upgrade,"upgrade")
    migrate(upgrade,"upgrade")
    with pytest.raises(ValueError,match="workspace_rebind_refused"):
        migrate(upgrade,"different")
    with psycopg.connect(upgrade) as database:
        assert database.execute("SELECT count(*) FROM intelligence.migration_history").fetchone()[0]==1


@pytest.mark.parametrize("statement",[
    "CREATE TABLE intelligence.forbidden(id integer)","CREATE TEMP TABLE forbidden(id integer)",
    "CREATE SCHEMA forbidden","CREATE ROLE forbidden","SET ROLE intelligence_owner",
    "UPDATE intelligence.instance SET workspace_id='other'", "DELETE FROM intelligence.migration_history",
    "UPDATE intelligence.model_profiles SET artifact_sha256='changed'",
    "DELETE FROM intelligence.evidence", "UPDATE intelligence.requests SET revision=2",
])
def test_restricted_runtime_denies_ddl_authority_and_immutable_record_changes(state,statement):
    with psycopg.connect(os.environ["TEMPO_INTELLIGENCE_TEST_RUNTIME_URL"]) as database:
        with pytest.raises(psycopg.Error):
            database.execute(statement)


@pytest.mark.parametrize("database_name",["learning","content","game_evidence","postgres","template1","intelligence_upgrade"])
def test_no_cross_domain_database_access(state,database_name):
    with pytest.raises(psycopg.Error):
        psycopg.connect(make_conninfo(os.environ["TEMPO_INTELLIGENCE_TEST_RUNTIME_URL"],dbname=database_name),connect_timeout=1)


def test_startup_checks_workspace_migration_checksums_and_runtime_authority(state):
    store,config,_=state
    other=Store(replace(config,workspace_id="other"))
    try:
        with pytest.raises(DomainError,match="workspace_mismatch"):
            other.validate_startup()
    finally:
        other.close()
    owner_execute("UPDATE intelligence.migration_history SET checksum='wrong'")
    try:
        with pytest.raises(DomainError,match="migration_state_mismatch"):
            store.validate_startup()
    finally:
        from tempo_intelligence.migrate import expected_migrations
        for identity,checksum in expected_migrations().items():
            owner_execute("UPDATE intelligence.migration_history SET checksum=%s WHERE id=%s",(checksum,identity))
    privileged=Store(replace(config,database_url=os.environ["TEMPO_INTELLIGENCE_TEST_OWNER_URL"]))
    try:
        with pytest.raises(DomainError,match="restricted_runtime_required"):
            privileged.validate_startup()
    finally:
        privileged.close()


def test_concurrent_first_requests_create_one_shared_cache_and_claim(state):
    store,_,_=state
    register(state)
    barrier=threading.Barrier(2)
    def create(identity):
        barrier.wait(timeout=5)
        return submit(store,request_value(request_id=identity))
    with ThreadPoolExecutor(2) as executor:
        responses=list(executor.map(create,("a","b")))
    assert responses[0]["sources"][0]["cache_id"]==responses[1]["sources"][0]["cache_id"]
    barrier=threading.Barrier(2)
    def claim(_):
        barrier.wait(timeout=5)
        return store.claim()
    with ThreadPoolExecutor(2) as executor:
        claims=list(executor.map(claim,range(2)))
    assert sum(item is not None for item in claims)==1


def test_replay_conflicts_and_replacement_do_not_revive_old_caller_revision(state):
    store,_,_=state
    register(state)
    old=submit(store,request_value(request_id="a"))
    submit(store,request_value(request_id="b"))
    assert submit(store,request_value(request_id="a"))["sources"]==old["sources"]
    claim=store.claim()
    replacement=request_value(request_id="a",sources=[{"kind":"lichess","ratings":[2200]}])
    replacement.update(request_revision=2,expected_revision=1)
    current=submit(store,replacement)
    assert current["sources"][0]["cache_id"]!=old["sources"][0]["cache_id"]
    assert store.publish(claim,{"total_games":123})
    assert store.inspect("a")["request_revision"]==2
    assert store.inspect("a")["sources"][0]["evidence_id"] is None
    assert store.inspect("b")["sources"][0]["evidence_id"]
    conflict=request_value(request_id="a")
    conflict["max_age_seconds"]=1
    with pytest.raises(DomainError,match="request_replay_conflict"):
        submit(store,conflict)


def test_policy_and_content_revision_changes_reuse_identical_source_query(state):
    store,_,_=state
    original=submit(store)
    changed=request_value(request_id="other-policy")
    changed["snapshot"]["policy_context"]={"policy_id":"other"}
    changed["snapshot"]["publication_generation"]=2
    changed["snapshot"]["source_revision"]=2
    changed["snapshot"]["legal_universe_version"]="v2-same-legal-input"
    changed["snapshot"]["legal_universe_digest"]=digest({"legal_universe_version":changed["snapshot"]["legal_universe_version"],"position_key":changed["snapshot"]["position_key"],"legal_moves":sorted(changed["snapshot"]["legal_moves"])})
    assert submit(store,changed)["sources"][0]["cache_id"]==original["sources"][0]["cache_id"]


def test_expired_claim_cannot_publish_after_replacement_and_restart_recovers(state):
    store,config,_=state
    register(state)
    submit(store)
    old=store.claim()
    owner_execute("UPDATE intelligence.caches SET leased_until=now()-interval '1 second'")
    owner_execute("UPDATE intelligence.provider_control SET leased_until=now()-interval '1 second'")
    restarted=Store(config)
    try:
        restarted.validate_startup()
        replacement=restarted.claim()
        assert replacement["execution_generation"]==old["execution_generation"]+1
        assert restarted.publish(replacement,{"generation":"new"})
        assert not store.publish(old,{"generation":"old"})
        identity=store.inspect("request")["sources"][0]["evidence_id"]
        assert store.evidence(identity)["payload"]["generation"]=="new"
    finally:
        restarted.close()


def test_refresh_idempotence_and_failure_retain_immutable_evidence(state):
    store,_,_=state
    evidence_id=accepted_explorer(state)
    command=store.refresh("request","refresh",1)
    assert store.refresh("request","refresh",1)==command
    store.refresh("request","join-active",1)
    assert store.inspect("request")["sources"][0]["work_generation"]==2
    ready(store)
    claim=store.claim()
    assert store.fail(claim,"invalid_response")
    result=store.inspect("request")["sources"][0]
    assert result["state"]=="failed" and result["evidence_id"]==evidence_id
    assert store.evidence(evidence_id)["payload"]["total_games"]==1


def test_publish_rollback_preserves_last_valid_record(state,monkeypatch):
    store,_,_=state
    evidence_id=accepted_explorer(state)
    store.refresh("request","refresh")
    ready(store)
    claim=store.claim()
    original=store._append
    def fail_after_append(database,cache,payload):
        original(database,cache,payload)
        raise RuntimeError("synthetic_rollback")
    monkeypatch.setattr(store,"_append",fail_after_append)
    with pytest.raises(RuntimeError,match="synthetic_rollback"):
        store.publish(claim,{"total_games":999})
    assert store.inspect("request")["sources"][0]["evidence_id"]==evidence_id
    assert owner_execute("SELECT count(*) FROM intelligence.evidence")[0][0]==1


def test_read_freshness_is_independent_of_refresh_failure(state):
    store,_,_=state
    evidence_id=accepted_explorer(state)
    owner_execute("UPDATE intelligence.evidence SET accepted_at=now()-interval '2 days'")
    assert store.inspect("request")["sources"][0]["freshness"]=="stale"
    assert store.evidence(evidence_id)["payload"]["total_games"]==1


def test_terminal_positions_do_not_queue_provider_or_maia_work(state):
    store,_,_=state
    value=request_value(sources=[{"kind":"lichess"},{"kind":"masters"}])
    value["snapshot"]=json.loads((Path(__file__).parent.parent/"fixtures/terminal-position-v1.json").read_text())
    assert {source["state"] for source in submit(store,value)["sources"]}=={"terminal"}
    assert store.claim() is None


def test_snapshot_identity_rejects_changed_input_without_rewriting_history(state):
    store,_,_=state
    submit(store)
    value=request_value(request_id="changed-snapshot")
    value["snapshot"]["fen"]=value["snapshot"]["fen"].replace("0 1","5 10")
    with pytest.raises(DomainError,match="snapshot_replay_conflict"):
        submit(store,value)
    assert store.inspect("request")["snapshot"]["fen"].endswith("0 1")


def test_native_maia_digest_and_legal_set_mismatches_are_not_accepted(state):
    store,_,_=state
    submit(store,request_value(sources=[{"kind":"maia","rating":1500,"model_id":"maia-2","model_version":"fixture-1","artifact_sha256":"0"*64}]))
    job=store.maia_jobs("request")["jobs"][0]
    result=maia_result(job)
    result["provenance"]["input_digest"]="wrong"
    with pytest.raises(DomainError,match="maia_result_mismatch"):
        store.accept_maia(MaiaResult.model_validate(result))
    result=maia_result(job)
    result["predictions"][-1]["move"]="e7e5"
    with pytest.raises(DomainError,match="maia_result_mismatch"):
        store.accept_maia(MaiaResult.model_validate(result))
    assert owner_execute("SELECT count(*) FROM intelligence.evidence")[0][0]==0


def test_valid_maia_result_replay_survives_newer_acceptance_without_rollback(state):
    store,_,_=state
    value=request_value(sources=[{"kind":"maia","rating":1500,"model_id":"maia-2","model_version":"fixture-1","artifact_sha256":"0"*64}])
    submit(store,value)
    job=store.maia_jobs("request")["jobs"][0]
    result=MaiaResult.model_validate(maia_result(job))
    accepted=store.accept_maia(result)
    assert store.accept_maia(result)["evidence_id"]==accepted["evidence_id"]
    store.refresh("request","refresh")
    newer=MaiaResult.model_validate(maia_result(store.maia_jobs("request")["jobs"][0]))
    second=store.accept_maia(newer)
    assert second["evidence_id"]!=accepted["evidence_id"]
    assert store.accept_maia(result)["evidence_id"]==accepted["evidence_id"]
    assert store.inspect("request")["sources"][0]["evidence_id"]==second["evidence_id"]
    conflicting=result.model_copy(deep=True)
    conflicting.provenance.runtime_version="different"
    with pytest.raises(DomainError,match="maia_result_replay_conflict"):
        store.accept_maia(conflicting)


@pytest.mark.parametrize("field,value",[("workspace_id","wrong"),("model_id","wrong"),("model_version","wrong"),("execution_generation",99),("request_revision",99)])
def test_maia_identity_mismatches_reject_without_evidence(state,field,value):
    store,_,_=state
    submit(store,request_value(sources=[{"kind":"maia","rating":1500,"model_id":"maia-2","model_version":"fixture-1","artifact_sha256":"0"*64}]))
    result=maia_result(store.maia_jobs("request")["jobs"][0])
    result[field]=value
    with pytest.raises(DomainError):
        store.accept_maia(MaiaResult.model_validate(result))
    assert owner_execute("SELECT count(*) FROM intelligence.evidence")[0][0]==0


def test_failed_maia_retry_fences_old_results_and_preserves_terminal_status(state):
    store,_,_=state
    submit(store,request_value(sources=[{"kind":"maia","rating":1500,"model_id":"maia-2","model_version":"fixture-1","artifact_sha256":"0"*64}]))
    job=store.maia_jobs("request")["jobs"][0]
    status={key:job[key] for key in ("contract_version","workspace_id","job_id","request_revision","execution_generation","worker") if key!="worker"}
    status.update(status="failed",retryable=True,reason="worker_unavailable")
    store.maia_status(JobStatus.model_validate(status))
    assert store.maia_status(JobStatus.model_validate(status))["status"]=="failed"
    with pytest.raises(DomainError,match="maia_status_transition_conflict"):
        store.maia_status(JobStatus.model_validate({**status,"status":"running","retryable":False}))
    store.retry("request","retry")
    with pytest.raises(DomainError,match="stale_maia_execution"):
        store.accept_maia(MaiaResult.model_validate(maia_result(job)))
    newjob=store.maia_jobs("request")["jobs"][0]
    status.update(execution_generation=newjob["execution_generation"],retryable=False)
    store.maia_status(JobStatus.model_validate(status))
    with pytest.raises(DomainError,match="retry_not_permitted"):
        store.retry("request","terminal-retry")
    assert not store.inspect("request")["sources"][0]["retry_eligible"]


def test_model_version_mapping_cannot_change_and_removed_profile_cannot_request(state):
    store,config,_=state
    altered=replace(config,model_profiles=({**config.model_profiles[0],"artifact_sha256":"f"*64},))
    other=Store(altered)
    try:
        with pytest.raises(DomainError,match="model_version_rebound"):
            other.validate_startup()
    finally:
        other.close()
    without=Store(replace(config,model_profiles=()))
    try:
        without.validate_startup()
        with pytest.raises(DomainError,match="unsupported_model_context"):
            submit(without,request_value(sources=[{"kind":"maia","rating":1500,"model_id":"maia-2","model_version":"fixture-1","artifact_sha256":"0"*64}]))
    finally:
        without.close()


def test_actual_installed_api_cli_acceptance_without_unrelated_services(state,tmp_path):
    store,configuration,_=state
    assert "site-packages" in str(Path(sys.modules[Store.__module__].__file__))
    headers={"Authorization":"Bearer "+configuration.caller_token}
    worker_headers={"Authorization":"Bearer "+configuration.worker_token}
    with TestClient(create_app(configuration,store=store)) as client:
        assert client.get("/healthz").status_code==200
        value=request_value(sources=[{"kind":"lichess"},{"kind":"maia","rating":1500,"model_id":"maia-2","model_version":"fixture-1","artifact_sha256":"0"*64}])
        response=client.post("/v1/requests",json=value,headers=headers)
        assert response.status_code==200
        job=client.get("/v1/requests/request/maia-jobs",headers=headers).json()["jobs"][0]
        accepted=client.post("/v1/maia/results",json=maia_result(job),headers=worker_headers)
        assert accepted.status_code==200
        assert not store.claim()
        assert {source["state"] for source in store.inspect("request")["sources"]}=={"complete","waiting_credentials"}
        identity=accepted.json()["evidence_id"]
        assert client.get("/v1/evidence/"+identity,headers=headers).status_code==200
    environment={key:value for key,value in os.environ.items() if not key.startswith(("PG","TEMPO_","PYTHONPATH","VIRTUAL_ENV"))}
    for name,value in (("DATABASE_URL",configuration.database_url),("CALLER_TOKEN",configuration.caller_token),("OPERATOR_TOKEN",configuration.operator_token),("WORKER_TOKEN",configuration.worker_token)):
        path=tmp_path.resolve()/(name+".private")
        path.write_text(value)
        path.chmod(0o600)
        environment["TEMPO_INTELLIGENCE_"+name+"_FILE"]=str(path)
    environment.update(TEMPO_INTELLIGENCE_WORKSPACE="fixture",TEMPO_INTELLIGENCE_WORKER_ENABLED="0")
    result=subprocess.run([sys.executable,"-I","-m","tempo_intelligence","inspect","request"],cwd=tmp_path,env=environment,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout
    assert json.loads(result.stdout)["request_id"]=="request"
