"""Native owner regressions. All resources originate in run_postgres.py."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from tempo_intelligence.identity import digest, source_descriptor
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
        assert database.execute("SELECT count(*) FROM intelligence.migration_history").fetchone()[0]==len(__import__("tempo_intelligence.migrate",fromlist=["expected_migrations"]).expected_migrations())


@pytest.mark.parametrize("statement",[
    "CREATE TABLE intelligence.forbidden(id integer)","CREATE TEMP TABLE forbidden(id integer)",
    "CREATE SCHEMA forbidden","CREATE ROLE forbidden","SET ROLE intelligence_owner",
    "UPDATE intelligence.instance SET workspace_id='other'", "DELETE FROM intelligence.migration_history",
    "UPDATE intelligence.model_profiles SET artifact_sha256='changed'",
    "DELETE FROM intelligence.evidence", "UPDATE intelligence.requests SET revision=2",
    "UPDATE intelligence.caches SET descriptor='{}'", "UPDATE intelligence.maia_jobs SET payload='{}'",
    "UPDATE intelligence.snapshots SET payload='{}'", "UPDATE intelligence.commands SET payload='{}'",
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


def test_changed_provider_origin_has_distinct_cache_and_cannot_claim_old_origin_work(state):
    store,config,_=state
    register(state)
    original=submit(store)
    alternate=Store(replace(config,provider_base_url="http://127.0.0.1:9"))
    try:
        alternate.validate_startup()
        assert alternate.claim() is None
        changed=submit(alternate,request_value(request_id="alternate-origin"))
        assert changed["sources"][0]["cache_id"]!=original["sources"][0]["cache_id"]
        assert alternate.inspect("request")["sources"][0]["provider_configuration_status"]=="unavailable"
        claim=alternate.claim()
        assert claim["descriptor"]["provider_origin"]=="http://127.0.0.1:9"
        assert alternate.publish(claim,{"source":"fixture-origin","total_games":1})
        ready(store)
        assert store.claim()["descriptor"]["provider_origin"]==config.provider_base_url
    finally:
        alternate.close()


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


@pytest.mark.parametrize("fen",["7k/8/8/8/8/8/8/K7 w - - 0 1","rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 150 1"])
def test_board_terminal_positions_with_legal_moves_do_not_queue_source_distributions(state,fen):
    import chess
    from tempo_intelligence.identity import canonical_position,legal_universe_digest
    store,_,_=state
    active=submit(store)
    value=request_value(request_id="board-terminal")
    value["snapshot"].update(fen=fen,source_revision=2,publication_generation=2)
    board=chess.Board(fen)
    assert board.is_game_over(claim_draw=False) and list(board.legal_moves)
    key,_=canonical_position(fen)
    moves=sorted(move.uci() for move in board.legal_moves)
    value["snapshot"].update(position_key=key,legal_moves=moves,legal_universe_digest=legal_universe_digest(key,moves,value["snapshot"]["legal_universe_version"]))
    terminal=submit(store,value)
    assert {source["state"] for source in terminal["sources"]}=={"terminal"}
    assert terminal["sources"][0]["cache_id"]!=active["sources"][0]["cache_id"]


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
    before=store.inspect("request")["sources"][0]
    assert store.retry("request","terminal-retry")["accepted"]
    assert store.inspect("request")["sources"][0]==before
    assert not store.inspect("request")["sources"][0]["retry_eligible"]


def maia_source():
    return {"kind":"maia","rating":1500,"model_id":"maia-2","model_version":"fixture-1","artifact_sha256":"0"*64}


def sources_by_kind(store,request_id="request"):
    return {source["kind"]:source for source in store.inspect(request_id)["sources"]}


def cache_records():
    return owner_execute("SELECT row_to_json(c) FROM intelligence.caches c ORDER BY cache_id")


def provider_record():
    return owner_execute("SELECT row_to_json(p) FROM intelligence.provider_control p WHERE id=1")[0][0]


def maia_records():
    return owner_execute("SELECT row_to_json(j) FROM intelligence.maia_jobs j ORDER BY job_id,request_revision,execution_generation")


def fail_maia(store,job,*,retryable=True):
    status={key:job[key] for key in ("contract_version","workspace_id","job_id","request_revision","execution_generation")}
    status.update(status="failed",retryable=retryable,reason="worker_unavailable")
    return store.maia_status(JobStatus.model_validate(status))


def ordered_mixed_request(state,explorer_first):
    """Choose real source parameters, rather than changing immutable cache IDs."""
    store,configuration,_=state
    for month in range(1,13):
        candidate=request_value(sources=[{"kind":"lichess","since":f"1952-{month:02d}"},maia_source()])
        request=RequestInput.model_validate(candidate)
        identifiers={}
        for source in request.sources:
            descriptor=source_descriptor(request.snapshot,source)
            if source.kind!="maia":
                descriptor["provider_origin"]=configuration.provider_base_url
            semantic={key:value for key,value in descriptor.items() if key!="legal_universe_digest"}
            identifiers[source.kind]=digest({"workspace":configuration.workspace_id,"descriptor":semantic})
        if (identifiers["lichess"]<identifiers["maia"])==explorer_first:
            result=submit(store,candidate)
            assert [source["cache_id"] for source in result["sources"]]==sorted(identifiers.values())
            assert [source["kind"] for source in result["sources"]]==(["lichess","maia"] if explorer_first else ["maia","lichess"])
            return candidate
    raise AssertionError("fixture_parameters_do_not_establish_requested_cache_order")


def accepted_mixed_request(state,explorer_first=True):
    store,_,_=state
    register(state)
    value=ordered_mixed_request(state,explorer_first)
    claim=store.claim()
    assert claim and claim["descriptor"]["kind"]=="lichess"
    assert store.publish(claim,{"source":"lichess","total_games":17})
    job=store.maia_jobs("request")["jobs"][0]
    store.accept_maia(MaiaResult.model_validate(maia_result(job)))
    evidence={kind:store.evidence(source["evidence_id"]) for kind,source in sources_by_kind(store).items()}
    return value,evidence


@pytest.mark.parametrize("explorer_first",[True,False],ids=["explorer-cache-first","maia-cache-first"])
@pytest.mark.parametrize("restriction",["future-deadline","nonretryable"])
def test_request_retry_is_source_local_in_both_cache_orders_and_replays_without_rollback(state,explorer_first,restriction):
    store,_,_=state
    _,evidence=accepted_mixed_request(state,explorer_first)
    store.refresh("request","initial-refresh")
    ready(store)
    explorer_claim=store.claim()
    future=datetime.now(timezone.utc)+timedelta(hours=1)
    if restriction=="future-deadline":
        assert store.fail(explorer_claim,"rate_limited",retryable=True,retry_at=future)
    else:
        assert store.fail(explorer_claim,"invalid_response",retryable=False)
    failed_job=store.maia_jobs("request")["jobs"][0]
    fail_maia(store,failed_job)
    before=sources_by_kind(store)
    explorer_record=next(row for row in cache_records() if row[0]["kind"]=="lichess")
    control=provider_record()
    historical_jobs=maia_records()
    assert not before["lichess"]["retry_eligible"] and before["maia"]["retry_eligible"]

    receipt=store.retry("request","partial-retry",1)

    assert receipt["accepted"]
    assert next(row for row in cache_records() if row[0]["kind"]=="lichess")==explorer_record
    assert provider_record()==control
    after=sources_by_kind(store)
    assert after["lichess"]==before["lichess"]
    assert after["maia"]["state"]=="awaiting_result"
    assert after["maia"]["work_generation"]==before["maia"]["work_generation"]
    assert after["maia"]["execution_generation"]==before["maia"]["execution_generation"]+1
    assert maia_records()[:len(historical_jobs)]==historical_jobs
    for kind in evidence:
        assert after[kind]["evidence_id"]==evidence[kind]["evidence_id"]
        assert store.evidence(evidence[kind]["evidence_id"])==evidence[kind]
    with pytest.raises(DomainError,match="stale_maia_execution"):
        store.accept_maia(MaiaResult.model_validate(maia_result(failed_job)))
    assert store.claim() is None
    new_job=store.maia_jobs("request")["jobs"][0]
    assert new_job["execution_generation"]==failed_job["execution_generation"]+1
    accepted=store.accept_maia(MaiaResult.model_validate(maia_result(new_job)))
    assert accepted["evidence_id"]!=evidence["maia"]["evidence_id"]
    assert sources_by_kind(store)["lichess"]["evidence_id"]==evidence["lichess"]["evidence_id"]
    assert sources_by_kind(store)["maia"]["evidence_id"]==accepted["evidence_id"]
    progressed_caches,progressed_jobs=cache_records(),maia_records()
    assert store.retry("request","partial-retry",1)==receipt
    assert cache_records()==progressed_caches and maia_records()==progressed_jobs
    assert provider_record()==control

    # A conflicting identity must reject even when another execution is eligible.
    store.refresh("request","prepare-conflict")
    fail_maia(store,store.maia_jobs("request")["jobs"][0])
    conflict_caches,conflict_jobs,conflict_control=cache_records(),maia_records(),provider_record()
    assert sources_by_kind(store)["maia"]["retry_eligible"]
    with pytest.raises(DomainError,match="command_replay_conflict"):
        store.refresh("request","partial-retry",1)
    with pytest.raises(DomainError,match="command_replay_conflict"):
        store.retry("request","partial-retry",2)
    assert cache_records()==conflict_caches and maia_records()==conflict_jobs
    assert provider_record()==conflict_control
    for kind in evidence:
        assert store.evidence(evidence[kind]["evidence_id"])==evidence[kind]
    assert owner_execute("SELECT result FROM intelligence.commands WHERE command_id='partial-retry'")[0][0]==receipt


def test_request_retry_advances_multiple_eligible_sources_but_preserves_provider_permit_and_evidence(state):
    store,_,_=state
    _,evidence=accepted_mixed_request(state)
    store.refresh("request","refresh")
    ready(store)
    claim=store.claim()
    future=datetime.now(timezone.utc)+timedelta(hours=1)
    assert store.fail(claim,"rate_limited",retryable=True,retry_at=future)
    owner_execute("UPDATE intelligence.caches SET next_attempt_at=now()-interval '1 second' WHERE kind='lichess'")
    failed_job=store.maia_jobs("request")["jobs"][0]
    fail_maia(store,failed_job)
    before=sources_by_kind(store)
    assert all(source["retry_eligible"] for source in before.values())
    control=provider_record()

    assert store.retry("request","retry-both")["accepted"]

    after=sources_by_kind(store)
    assert after["lichess"]["state"]=="queued" and after["maia"]["state"]=="awaiting_result"
    assert after["lichess"]["execution_generation"]==before["lichess"]["execution_generation"]
    assert after["maia"]["execution_generation"]==before["maia"]["execution_generation"]+1
    for kind in before:
        assert after[kind]["work_generation"]==before[kind]["work_generation"]
        assert after[kind]["evidence_id"]==evidence[kind]["evidence_id"]
        assert store.evidence(evidence[kind]["evidence_id"])==evidence[kind]
        assert not after[kind]["retry_eligible"]
    assert after["lichess"]["provider_retry_at"]==future.isoformat()
    assert provider_record()==control
    assert store.claim() is None
    assert sources_by_kind(store)["lichess"]["state"]=="queued"
    assert provider_record()==control


def test_request_retry_with_three_ineligible_sources_is_a_durable_noop(state):
    store,_,_=state
    register(state)
    value=request_value(sources=[{"kind":"lichess"},{"kind":"masters"},maia_source()])
    submit(store,value)
    for _ in range(2):
        ready(store)
        claim=store.claim()
        assert claim
        assert store.publish(claim,{"source":claim["descriptor"]["kind"],"total_games":7})
    fail_maia(store,store.maia_jobs("request")["jobs"][0],retryable=False)
    submit(store,request_value(sources=[{"kind":"masters"}],request_id="masters-only"))
    store.refresh("masters-only","masters-refresh")
    ready(store)
    claim=store.claim()
    assert claim and claim["descriptor"]["kind"]=="masters"
    assert store.fail(claim,"rate_limited",retryable=True,retry_at=datetime.now(timezone.utc)+timedelta(hours=1))
    before=sources_by_kind(store)
    assert {kind:source["state"] for kind,source in before.items()}=={"lichess":"complete","masters":"retry_wait","maia":"failed"}
    assert not any(source["retry_eligible"] for source in before.values())
    caches,jobs,control=cache_records(),maia_records(),provider_record()

    receipt=store.retry("request","nothing-eligible")

    assert receipt["accepted"]
    assert store.retry("request","nothing-eligible")==receipt
    assert owner_execute("SELECT count(*) FROM intelligence.commands WHERE command_id='nothing-eligible'")[0][0]==1
    assert cache_records()==caches and maia_records()==jobs and provider_record()==control
    assert store.claim() is None
    assert cache_records()==caches and sources_by_kind(store)==before


@pytest.mark.parametrize("cache_state",["queued","running","retry_wait","waiting_credentials","awaiting_result","failed","complete","terminal"])
@pytest.mark.parametrize("retryable",[True,False],ids=["retryable","nonretryable"])
def test_retry_inspection_matches_source_state_and_retryable_flag(state,cache_state,retryable):
    store,_,_=state
    submit(store,request_value(sources=[{"kind":"lichess"},maia_source()]))
    owner_execute("UPDATE intelligence.caches SET state=%s,retryable=%s,error_code='fixture_state',next_attempt_at=NULL",(cache_state,retryable))
    before=sources_by_kind(store)
    eligible=retryable and cache_state in ("failed","waiting_credentials","retry_wait")
    assert {source["retry_eligible"] for source in before.values()}=={eligible}
    caches,jobs,control=cache_records(),maia_records(),provider_record()

    assert store.retry("request","eligibility")["accepted"]

    after=sources_by_kind(store)
    if not eligible:
        assert cache_records()==caches and maia_records()==jobs and after==before
    else:
        assert after["lichess"]["state"]=="queued"
        assert after["maia"]["state"]=="awaiting_result"
        assert after["maia"]["execution_generation"]==before["maia"]["execution_generation"]+1
        assert after["lichess"]["execution_generation"]==before["lichess"]["execution_generation"]
        assert not any(source["retry_eligible"] for source in after.values())
    assert provider_record()==control


@pytest.mark.parametrize("cache_state",["failed","waiting_credentials","retry_wait"])
@pytest.mark.parametrize("deadline",["elapsed","future"])
def test_retry_inspection_and_execution_use_each_sources_own_deadline(state,cache_state,deadline):
    store,_,_=state
    submit(store,request_value(sources=[{"kind":"lichess"},maia_source()]))
    offset=timedelta(hours=-1 if deadline=="elapsed" else 1)
    timestamp=datetime.now(timezone.utc)+offset
    owner_execute("UPDATE intelligence.caches SET state=%s,retryable=true,next_attempt_at=%s",(cache_state,timestamp))
    before=sources_by_kind(store)
    assert {source["retry_eligible"] for source in before.values()}=={deadline=="elapsed"}
    caches,jobs=cache_records(),maia_records()

    assert store.retry("request","deadline-eligibility")["accepted"]

    after=sources_by_kind(store)
    if deadline=="future":
        assert cache_records()==caches and maia_records()==jobs and after==before
    else:
        assert after["lichess"]["state"]=="queued" and after["maia"]["state"]=="awaiting_result"
        assert all(source["retry_at"] is None for source in after.values())
        assert after["maia"]["execution_generation"]==before["maia"]["execution_generation"]+1


@pytest.mark.parametrize("cache_state",["failed","waiting_credentials","retry_wait"])
def test_provider_origin_mismatch_is_ineligible_without_blocking_maia_retry(state,cache_state):
    store,configuration,_=state
    submit(store,request_value(sources=[{"kind":"lichess"},maia_source()]))
    owner_execute("UPDATE intelligence.caches SET state=%s,retryable=true,next_attempt_at=NULL",(cache_state,))
    alternate=Store(replace(configuration,provider_base_url="http://127.0.0.1:9"))
    try:
        alternate.validate_startup()
        before=sources_by_kind(alternate)
        assert before["lichess"]["provider_configuration_status"]=="unavailable"
        assert not before["lichess"]["retry_eligible"] and before["maia"]["retry_eligible"]
        explorer_record=next(row for row in cache_records() if row[0]["kind"]=="lichess")
        control=provider_record()
        assert alternate.retry("request","origin-eligibility")["accepted"]
        after=sources_by_kind(alternate)
        assert after["lichess"]==before["lichess"]
        assert next(row for row in cache_records() if row[0]["kind"]=="lichess")==explorer_record
        assert after["maia"]["execution_generation"]==before["maia"]["execution_generation"]+1
        assert after["maia"]["state"]=="awaiting_result"
        assert provider_record()==control
    finally:
        alternate.close()


def test_legacy_maia_retry_preserves_persisted_constraints_and_refresh_uses_current_budget(state,monkeypatch):
    from tempo_intelligence import store as store_module
    store,_,_=state
    current_builder=store_module.build_maia_job
    def legacy_builder(*arguments):
        built=current_builder(*arguments)
        job=built.model_dump(mode="json")
        job["constraints"]["max_memory_mb"]=512
        return type(built).model_validate(job)
    with monkeypatch.context() as historical:
        historical.setattr(store_module,"build_maia_job",legacy_builder)
        submit(store,request_value(sources=[maia_source()]))
    original_job=store.maia_jobs("request")["jobs"][0]
    assert original_job["constraints"]=={"max_wall_time_ms":5000,"max_memory_mb":512}
    fail_maia(store,original_job)
    original_record=maia_records()[0]

    assert store.retry("request","retry-legacy")["accepted"]

    retried_job=store.maia_jobs("request")["jobs"][0]
    expected={**original_job,"execution_generation":original_job["execution_generation"]+1}
    assert retried_job==expected
    assert maia_records()[0]==original_record
    store.accept_maia(MaiaResult.model_validate(maia_result(retried_job)))
    historical_records=maia_records()
    assert store.refresh("request","refresh-budget")["accepted"]
    refreshed_job=store.maia_jobs("request")["jobs"][0]
    assert refreshed_job["request_revision"]==original_job["request_revision"]+1
    assert refreshed_job["constraints"]=={"max_wall_time_ms":5000,"max_memory_mb":1024}
    assert maia_records()[:len(historical_records)]==historical_records


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


def test_real_supported_maia_envelope_result_is_accepted_against_exact_persisted_job(state):
    store,configuration,_=state
    fixture=json.loads((Path(__file__).parent.parent/"fixtures/maia-supported-envelope-v1.json").read_text())
    requested=fixture["request"]
    source=requested["sources"][0]
    profile={key:source[key] for key in ("model_id","model_version","artifact_sha256")}
    external=Store(replace(configuration,model_profiles=({**profile,"ratings":[source["rating"]]},)))
    try:
        external.validate_startup()
        external.submit(RequestInput.model_validate(requested))
        assert external.maia_jobs(requested["request_id"])["jobs"]==[fixture["job"]]
        assert fixture["job"]["constraints"]=={"max_memory_mb":1024,"max_wall_time_ms":5000}
        receipt=external.accept_maia(MaiaResult.model_validate(fixture["result"]))
        accepted=external.evidence(receipt["evidence_id"])
        assert accepted["payload"]["job"]==fixture["job"]
        assert accepted["payload"]["result"]==fixture["result"]
        assert external.inspect(requested["request_id"])["sources"][0]["state"]=="complete"
        assert external.accept_maia(MaiaResult.model_validate(fixture["result"]))=={
            "schema_version":1,"evidence_id":receipt["evidence_id"],"replayed":True}
    finally:
        external.close()


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
