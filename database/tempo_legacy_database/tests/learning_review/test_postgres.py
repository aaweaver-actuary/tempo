"""Real installed application + legacy adapters in the invocation-owned cluster."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch
import json
import os
import subprocess
import sys
import time
import uuid

import psycopg
import pytest

if not os.environ.get("TEMPO224_FIXTURE", "").startswith("tempo224-"):
    raise RuntimeError("Run database/tempo_legacy_database/tooling/learning_review/run_postgres.py; external databases are forbidden")

from tempo_legacy_database.adapters import postgres_store
from app import review_commands, queue_commands
from app.command_gateway import CommandConflict, execute_command, read_operation
from app.services.learning_review_adapter import LearningReviewAdapter
from app.services.review_reconciliation import card_schedule_state

ADMIN = os.environ["TEMPO224_ADMIN"]
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture
def case():
    identity = "review224-" + uuid.uuid4().hex
    day = date.today().isoformat()
    with postgres_store.connection() as database:
        database.execute("UPDATE daily_queue SET status='buried' WHERE status='queued'")
        database.execute("UPDATE frozen_opening_activation SET run_id=NULL WHERE id=1")
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",(identity,"Review extraction","fixture",day))
        entries = []
        for position, card_id in enumerate((identity+"-a",identity+"-b")):
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,content_type) VALUES(?,?,'prefix',?,'[\"e2e4\"]','white','learning',?,'opening')", (card_id,identity,FEN,day))
            database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",(identity,card_id))
            entries.append(database.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind,admission_repertoire_id) VALUES(?,?,?,'opening','new',?) RETURNING id",(day,card_id,-100000+position,identity)).fetchone()[0])
        database.execute("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=?",(entries[0],))
    payload = {"card_id":identity+"-a","review":{"outcome":"correct","guided":False,
        "queue_entry_id":entries[0],"expected_revision":1,"attempt_id":identity+"-attempt",
        "recorded_at":datetime.now(timezone.utc).isoformat(),"prepared_next":{
            "queue_entry_id":entries[1],"card_id":identity+"-b","revision":1,"cycle":0,"local_date":day}}}
    yield dict(identity=identity,day=day,entries=entries,payload=payload)
    postgres_store.close_pools()


def operation(case, suffix="save", payload=None, command="cards.review"):
    identifier = case["identity"]+"-"+suffix
    result = execute_command(identifier, command, payload or case["payload"])
    return result, read_operation(identifier)


def snapshot(case):
    card_id = case["payload"]["card_id"]
    with postgres_store.connection(read_only=True) as database:
        return {"schedule":card_schedule_state(database,card_id),
            "reviews":[dict(row) for row in database.execute("SELECT * FROM reviews WHERE card_id=? ORDER BY id",(card_id,))],
            "receipts":[dict(row) for row in database.execute("SELECT * FROM review_attempt_receipts WHERE card_id=?",(card_id,))],
            "queue":[dict(row) for row in database.execute("SELECT * FROM daily_queue WHERE card_id IN (?,?) ORDER BY id",(card_id,case["identity"]+"-b"))],
            "evidence":[dict(row) for row in database.execute("SELECT * FROM opening_evidence_attempts WHERE card_id=?",(card_id,))]}


def add_evidence(case):
    from app.services.postgres_opening_evidence import queue_manifests, prepare_checkpoint
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    card = {"id":case["payload"]["card_id"],"content_type":"opening","revision":1,
        "start_fen":FEN,"moves":["e2e4"],"queue_entry_id":case["entries"][0]}
    queue_manifests([card])
    manifest = card["opening_decision_manifest"]
    decision = manifest["decisions"][0]
    now = case["payload"]["review"]["recorded_at"]
    checkpoint = OpeningEvidenceCheckpoint(attempt_id=case["payload"]["review"]["attempt_id"],
        manifest=manifest, origin_queue_entry_id=case["entries"][0],queue_entry_id=case["entries"][0],
        started_at=now,study_timezone="UTC",events=[dict(sequence=1,decision_index=0,
            decision_id=decision["decision_id"],expected_uci=decision["expected_uci"],kind="first_response",
            observed_at=now,response_uci=decision["expected_uci"],disposition="expected")],
        terminal={"state":"complete","final_sequence":1,"ended_at":now})
    case["payload"]["review"]["opening_evidence_completion"] = checkpoint.model_dump(mode="json")
    case["payload"]["prepared_manifest"] = prepare_checkpoint(checkpoint)
    return checkpoint


def test_native_ordinary_required_effects_receipts_and_lost_response(case):
    completion = add_evidence(case)
    from app.services.real_game_feedback import MISS_REASON
    with postgres_store.connection() as database:
        database.execute("UPDATE daily_queue SET gameplay_priority_reason=? WHERE card_id=?",(MISS_REASON,case["payload"]["card_id"]))
    result, transport = operation(case)
    assert result["persisted"] and result["prepared_continuation"]["card_id"] == case["identity"]+"-b"
    saved = snapshot(case)
    assert len(saved["reviews"]) == len(saved["receipts"]) == len(saved["evidence"]) == 1
    assert saved["reviews"][0]["source_ref"] == "attempt:"+completion.attempt_id
    assert saved["evidence"][0]["state"] == "complete" and saved["evidence"][0]["review_id"] == result["review_id"]
    assert saved["schedule"]["first_correct_at"] == case["payload"]["review"]["recorded_at"]
    assert all(row["gameplay_priority_reason"] is None for row in saved["queue"] if row["card_id"] == case["payload"]["card_id"])
    with postgres_store.connection(read_only=True) as database:
        assert database.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=?",(case["entries"][1],)).fetchone()[0] == 1
        assert database.execute("SELECT refresh_pending FROM queue_projections WHERE queue_date=?",(case["day"],)).fetchone()[0] == 1
        assert database.execute("SELECT COUNT(*) FROM review_schedule_snapshots WHERE review_id=?",(result["review_id"],)).fetchone()[0] == 1
        assert database.execute("SELECT COUNT(*) FROM opening_evidence_queue_contexts WHERE queue_entry_id=?",(result["requeue_entry_id"],)).fetchone()[0] == 1
    postgres_store.close_pools()
    assert operation(case)[0] == result
    assert operation(case,"logical-retry")[0] == result
    # Existing evidence binding is completed before successor issuance. A logical
    # retry copies the recorded aggregate result (now including continuation)
    # into that binding, without creating another logical effect.
    from app.services.postgres_opening_evidence import canonical_json
    assert json.loads(saved["evidence"][0]["review_result_json"]) == {
        key:value for key,value in result.items() if key != "prepared_continuation"}
    saved["evidence"][0]["review_result_json"] = canonical_json(result)
    assert snapshot(case) == saved and transport["state"] == "complete"
    with postgres_store.connection(read_only=True) as database:
        assert database.execute("SELECT COUNT(*) FROM opening_evidence_events WHERE attempt_id=?",(completion.attempt_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("stage", ["history","evidence","logical_receipt","transport_receipt"])
def test_native_failure_rolls_back_all_logical_effects(case, stage):
    add_evidence(case)
    before = snapshot(case)
    target = {"history":"save_snapshot","evidence":"persist_evidence","logical_receipt":"save_receipt"}.get(stage)
    if target:
        original = getattr(LearningReviewAdapter,target)
        def fail(adapter,*args,**kwargs):
            original(adapter,*args,**kwargs)
            raise RuntimeError("injected "+stage)
        with patch.object(LearningReviewAdapter,target,fail):
            result,receipt = operation(case)
        assert result is None and receipt["state"] == "failed"
    else:
        original = postgres_store._TimingConnection.execute
        def fail(database,statement,parameters=None,*args,**kwargs):
            if "UPDATE operation_receipts SET state='complete'" in statement:
                raise RuntimeError("injected transport_receipt")
            return original(database,statement,parameters,*args,**kwargs)
        with patch.object(postgres_store._TimingConnection,"execute",fail), pytest.raises(RuntimeError):
            operation(case)
        assert read_operation(case["identity"]+"-save")["state"] == "unknown"
    assert snapshot(case) == before


def test_native_handled_conflict_rolls_back_evidence_history_and_logical_receipt(case):
    from app.contracts.review_conflicts import ReviewConflict
    add_evidence(case)
    before = snapshot(case)
    original = LearningReviewAdapter.save_receipt
    def conflict(adapter,*args,**kwargs):
        original(adapter,*args,**kwargs)
        raise ReviewConflict("injected_conflict","handled after review and evidence writes")
    with patch.object(LearningReviewAdapter,"save_receipt",conflict):
        result,receipt = operation(case,command="cards.review.reconcile")
    assert result == {"persisted":False,"conflict":{"code":"injected_conflict","message":"handled after review and evidence writes","retryable":False}}
    assert receipt["state"] == "complete" and snapshot(case) == before
    assert operation(case,command="cards.review.reconcile")[0] == result


def test_native_concurrent_identical_and_competing_payloads_use_one_logical_attempt(case):
    reached, release = Event(), Event()
    started = Barrier(3)
    original = LearningReviewAdapter.save_receipt
    def hold(adapter,*args,**kwargs):
        result = original(adapter,*args,**kwargs)
        reached.set()
        assert release.wait(10)
        return result
    waiting_pids = []
    original_lock = LearningReviewAdapter.lock_review_cards
    def observe_competing_connection(adapter, command):
        if reached.is_set():
            waiting_pids.append(adapter.database.raw.info.backend_pid)
            started.wait(timeout=10)
        return original_lock(adapter, command)
    def competitor(suffix,payload):
        return operation(case,suffix,payload)
    conflicting = {**case["payload"],"review":{**case["payload"]["review"],"outcome":"again"}}
    with ThreadPoolExecutor(max_workers=3) as executor, patch.object(LearningReviewAdapter,"save_receipt",hold), patch.object(LearningReviewAdapter,"lock_review_cards",observe_competing_connection):
        first = executor.submit(operation,case)
        try:
            assert reached.wait(10)
            duplicate = executor.submit(competitor,"identical",case["payload"])
            competing = executor.submit(competitor,"competing",conflicting)
            started.wait(timeout=10)
            assert len(set(waiting_pids)) == 2
            deadline = time.monotonic()+5
            with psycopg.connect(ADMIN,autocommit=True) as observer:
                while observer.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE pid=ANY(%s) AND wait_event_type='Lock'",(waiting_pids,)).fetchone()[0] != 2:
                    assert time.monotonic() < deadline, "Competing connections never reached native locks"
            release.set()
            result = first.result(timeout=10)[0]
            assert duplicate.result(timeout=10)[0] == result
            assert competing.result(timeout=10)[1]["error"]["code"] == "review_attempt_reused"
        finally:
            release.set()
    saved = snapshot(case)
    assert len(saved["reviews"]) == len(saved["receipts"]) == 1
    assert sum(row["cycle"] == 1 for row in saved["queue"] if row["card_id"] == case["payload"]["card_id"]) == 1
    with pytest.raises(CommandConflict): operation(case,payload=conflicting)


@pytest.mark.parametrize("mutation,code", [("deleted","card_deleted"),("archived","card_archived"),
    ("revision","card_revision_changed"),("pending_validation","card_integrity_blocked")])
def test_native_stale_or_deleted_attempt_cannot_create_effects(case, mutation, code):
    with postgres_store.connection() as database:
        if mutation == "deleted":
            database.execute("INSERT INTO deleted_cards(card_id,deleted_at) VALUES(?,?)",(case["payload"]["card_id"],case["day"]))
        else:
            database.execute(f"UPDATE cards SET {mutation}=? WHERE id=?",(2 if mutation == "revision" else 1,case["payload"]["card_id"]))
    before = snapshot(case)
    result,receipt = operation(case)
    assert result is None and receipt["error"]["code"] == code
    assert snapshot(case) == before


def test_native_committed_replay_precedes_archive_but_not_deletion(case):
    result = operation(case)[0]
    with postgres_store.connection() as database:
        database.execute("UPDATE cards SET archived=1 WHERE id=?",(case["payload"]["card_id"],))
    assert operation(case,"archive-replay")[0] == result
    with postgres_store.connection() as database:
        database.execute("INSERT INTO deleted_cards(card_id,deleted_at) VALUES(?,?)",(case["payload"]["card_id"],case["day"]))
    assert operation(case,"deleted-replay")[1]["error"]["code"] == "card_deleted"
    assert operation(case)[0] == result  # Transport replay retains its committed outcome.


@pytest.mark.parametrize("change", ["removed","day","cycle","revision","defense","study_exercise","contended"])
def test_native_optional_successor_unavailable_never_loses_completed_review(case,change):
    candidate = case["payload"]["review"]["prepared_next"]
    held = None
    try:
        with postgres_store.connection() as database:
            if change == "removed": database.execute("DELETE FROM daily_queue WHERE id=?",(candidate["queue_entry_id"],))
            elif change == "day": candidate["local_date"] = "1999-01-01"
            elif change in {"cycle","revision"}: candidate[change] += 1
            elif change in {"defense","study_exercise"}: database.execute("UPDATE cards SET content_type=? WHERE id=?",(change,candidate["card_id"]))
        if change == "contended":
            held = psycopg.connect(ADMIN)
            held.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("tempo:card-edit:"+candidate["card_id"],))
            held.execute("SELECT id FROM cards WHERE id=%s FOR UPDATE",(candidate["card_id"],))
        result,receipt = operation(case)
        assert result["persisted"] and "prepared_continuation" not in result and receipt["state"] == "complete"
        assert operation(case)[0] == result and len(snapshot(case)["reviews"]) == 1
    finally:
        if held: held.rollback(); held.close()


def test_native_connection_interruption_rolls_back_and_fresh_process_replays(case):
    add_evidence(case)
    before = snapshot(case)
    original = LearningReviewAdapter.save_receipt
    def interrupt(adapter,*args,**kwargs):
        original(adapter,*args,**kwargs)
        pid = adapter.database.raw.info.backend_pid
        with psycopg.connect(ADMIN,autocommit=True) as administration:
            administration.execute("SELECT pg_terminate_backend(%s)",(pid,))
        adapter.database.execute("SELECT 1")
    with patch.object(LearningReviewAdapter,"save_receipt",interrupt), pytest.raises(psycopg.Error): operation(case)
    postgres_store.close_pools()
    assert snapshot(case) == before
    result = operation(case)[0]
    # Prepared manifests are derived and recomputed in the independent process.
    payload = {key:value for key,value in case["payload"].items() if key != "prepared_manifest"}
    source = '''import json,sys
from app import review_commands
from app.command_gateway import execute_command
assert 'app.main' not in sys.modules and 'app.tasks' not in sys.modules
payload=json.loads(sys.stdin.read())
if payload["review"].get("opening_evidence_completion"):
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from app.services.postgres_opening_evidence import prepare_checkpoint
    payload["prepared_manifest"]=prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(payload["review"]["opening_evidence_completion"]))
print(json.dumps(execute_command(sys.argv[1],"cards.review",payload)))
'''
    completed = subprocess.run([sys.executable,"-c",source,case["identity"]+"-fresh"],input=json.dumps(payload),
                               capture_output=True,text=True,timeout=20,check=True)
    assert json.loads(completed.stdout) == result
    assert len(snapshot(case)["reviews"]) == 1


@pytest.mark.parametrize("active", [False,True])
def test_native_frozen_reader_admission_fences_successor_without_reordering(case,active):
    candidate = case["payload"]["review"]["prepared_next"]
    if active:
        # Seed only this owned fixture's publication; no plan creation/apply API.
        with postgres_store.connection() as database:
            run_id = case["identity"]+"-frozen"
            database.execute("INSERT INTO frozen_opening_runs(run_id,plan_checksum,plan_json,created_at,reader_version) VALUES(?,?,?,?,'fixture')",(run_id,run_id,'{}',case["day"]))
            database.execute("UPDATE frozen_opening_activation SET run_id=?,epoch=epoch+1 WHERE id=1",(run_id,))
            database.execute("UPDATE queue_attempt_origins SET legacy=1 WHERE card_id=?",(candidate["card_id"],))
    result = operation(case)[0]
    assert result["persisted"]
    assert ("prepared_continuation" in result) is (not active)
    with postgres_store.connection(read_only=True) as database:
        assert database.execute("SELECT position FROM daily_queue WHERE id=?",(candidate["queue_entry_id"],)).fetchone()[0] == -99999
        assert database.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=?",(candidate["queue_entry_id"],)).fetchone()[0] == int(not active)


def test_native_existing_issuance_edit_and_deletion_regression_uses_relocated_seam(case):
    import database.tests.check_postgres_training_transition as legacy_proof
    from app.contracts.transport.study import ReviewRequest
    identities = {}
    with postgres_store.connection() as database:
        database.execute("UPDATE daily_queue SET status='buried' WHERE status='queued'")
        for index in (30,31,32,33):
            card_id = case["identity"]+f"-legacy-{index}"
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,content_type) VALUES(?,?,'prefix',?,'[\"e2e4\"]','white','learning',?,'opening')",(card_id,case["identity"],FEN,case["day"]))
            entry = database.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?) RETURNING id",(case["day"],card_id,index)).fetchone()[0]
            identities[index] = dict(queue_entry_id=entry,card_id=card_id,revision=1,cycle=0,local_date=case["day"])
    def identity(index): return identities[index]
    def issue(index):
        candidate = identity(index)
        assert execute_command(uuid.uuid4().hex,"queue.issue_head",{"queue_date":case["day"],
            "entry_id":candidate["queue_entry_id"],"card_id":candidate["card_id"],"expected_revision":1})["issued"]
    def review(index,successor):
        candidate = identity(index)
        request = ReviewRequest(outcome="correct",queue_entry_id=candidate["queue_entry_id"],
            expected_revision=1,attempt_id=candidate["card_id"]+"-attempt",prepared_next=identity(successor))
        return {"card_id":candidate["card_id"],"review":request.durable_payload()}
    # Only this function's administrative observation DSN is redirected. Every
    # product connection still uses the runner's separately provisioned roles.
    with patch.object(legacy_proof,"DSN",ADMIN):
        legacy_proof.test_postgres_prepared_issuance_fences_competing_edits_and_deletions(identity,issue,review)


def test_native_distinct_completed_attempt_gets_existing_reconciliation_credit(case):
    first = operation(case)[0]
    competing = {**case["payload"],"review":{**case["payload"]["review"],
        "attempt_id":case["identity"]+"-independent","recorded_at":datetime.now(timezone.utc).isoformat()}}
    second,receipt = operation(case,"independent",competing)
    assert first["persisted"] and second["persisted"] and second["reconciliation"] == "chronological"
    assert "prepared_continuation" not in second and receipt["state"] == "complete"
    assert operation(case,"independent-retry",competing)[0] == second
    assert len(snapshot(case)["reviews"]) == len(snapshot(case)["receipts"]) == 2


@pytest.fixture
def local_host_admission(monkeypatch):
    # These are real restricted-PostgreSQL queue transaction proofs. The shared
    # Redis host gate has a separate owning native suite; retain the local gate
    # and database budgets here without declaring an unavailable shared broker.
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)


def test_native_proportional_presentation_preserves_frozen_selection_quota_and_retry(case, local_host_admission):
    from app.services import frozen_opening_order as frozen, postgres_queue_refresh as refresh, queue_application
    from app.study.openings.frozen_math import content_fingerprint
    with postgres_store.connection() as database:
        database.execute("UPDATE daily_queue SET status='buried' WHERE status='queued' AND id!=?", (case['entries'][0],))
        database.execute("UPDATE daily_queue SET position=0 WHERE id=?", (case['entries'][0],))
        for position in range(1, 101):
            identifier = case['identity'] + f'-mix-{position}'
            kind = 'opening' if position <= 90 else 'tactic'
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,content_type,introduced_at) VALUES(?,?,'prefix',?,'[\"e2e4\"]','white','learning',?,?,?)",
                             (identifier,case['identity'],FEN,case['day'],kind,case['day']))
            database.execute("INSERT INTO repertoire_cards VALUES(?,?)", (case['identity'],identifier))
            database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,?,'new',?)",
                             (case['day'],identifier,position,case['identity']))
        retry = queue_application.requeue(database,case['day'],case['payload']['card_id'],4,'guided')
        rows = [dict(row) for row in database.execute("SELECT * FROM cards WHERE id LIKE ? AND content_type='opening' ORDER BY id DESC", (case['identity']+'-mix-%',))]
        ranks = [{**row,'rank':index,'fingerprint':content_fingerprint(row,[case['identity']]),'memberships':[case['identity']]} for index,row in enumerate(rows,1)]
        publication = dict(run_id=case['identity']+'-order',checksum=case['identity'],ranks=ranks)
        frozen.stage_run(database,publication,datetime.now(timezone.utc).isoformat())
        frozen.stage_ranks(database,publication,ranks)
        frozen.activate_run(database,publication)
        original = [tuple(row) for row in database.execute("SELECT id,card_id,position FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id", (case['day'],))]
        quota = [tuple(row) for row in database.execute("SELECT card_id,cycle,admission_kind,admission_repertoire_id FROM queue_attempt_origins WHERE queue_date=? ORDER BY queue_entry_id",(case['day'],))]
    protected = {case['entries'][0],retry}
    expected_openings = [row['id'] for row in rows]
    for seed in (1, 17):
        with postgres_store.connection() as database:
            for entry,_,position in original:
                database.execute("UPDATE daily_queue SET position=? WHERE id=?",(position,entry))
            database.execute("INSERT INTO daily_queue_days VALUES(?,?,?,'fixture') ON CONFLICT(queue_date) DO UPDATE SET seed=excluded.seed,membership_hash=excluded.membership_hash",(case['day'],seed,'legacy-v2'))
        prepared = refresh._prepare_queue_randomization(case['day'])
        with postgres_store.connection() as database:
            assert refresh._publish_queue_randomization(database,case['day'],prepared)
            current = [dict(row) for row in database.execute("SELECT q.*,c.content_type FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE queue_date=? AND status='queued' ORDER BY position,id",(case['day'],))]
            assert {row['id'] for row in current} == {row[0] for row in original}
            for identifier in protected:
                assert next(row['position'] for row in current if row['id']==identifier) == next(row[2] for row in original if row[0]==identifier)
            ordinary = [row for row in current if row['id'] not in protected]
            assert [row['card_id'] for row in ordinary if row['content_type']=='opening'] == expected_openings
            tactic_positions = [index+1 for index,row in enumerate(ordinary) if row['content_type']=='tactic']
            assert len(tactic_positions)==10 and tactic_positions[-1]>=92
            assert all(9<=right-left<=11 for left,right in zip(tactic_positions,tactic_positions[1:]))
            assert [tuple(row) for row in database.execute("SELECT card_id,cycle,admission_kind,admission_repertoire_id FROM queue_attempt_origins WHERE queue_date=? ORDER BY queue_entry_id",(case['day'],))] == quota
        assert refresh._prepare_queue_randomization(case['day'])['entries'] is None


@pytest.mark.parametrize('change',['issuance','eligibility','position'])
def test_native_prepared_presentation_rejects_changed_attempt_authority(case,change,local_host_admission):
    from app.services import postgres_queue_refresh as refresh
    with postgres_store.connection() as database:
        database.execute("DELETE FROM daily_queue_days WHERE queue_date=?",(case['day'],))
        database.execute("UPDATE daily_queue SET position=-200000 WHERE id=?",(case['entries'][1],))
    prepared = refresh._prepare_queue_randomization(case['day'])
    with postgres_store.connection() as database:
        if change=='issuance':
            assert queue_commands.issue_queue_head(database,dict(queue_date=case['day'],entry_id=case['entries'][1],card_id=case['identity']+'-b',expected_revision=1))['issued']
        elif change=='eligibility':
            database.execute("UPDATE cards SET archived=1 WHERE id=?",(case['identity']+'-b',))
        else:
            database.execute("UPDATE daily_queue SET position=-300000 WHERE id=?",(case['entries'][1],))
        before = [tuple(row) for row in database.execute("SELECT id,position FROM daily_queue WHERE queue_date=? ORDER BY id",(case['day'],))]
        assert refresh._publish_queue_randomization(database,case['day'],prepared) is False
        assert [tuple(row) for row in database.execute("SELECT id,position FROM daily_queue WHERE queue_date=? ORDER BY id",(case['day'],))] == before


@pytest.mark.parametrize('prior_cycle', [False, True])
def test_native_priority_overlay_retains_issued_position_without_stamping_unmixed_day(case, prior_cycle):
    from app.services import queue_application
    from app.services.real_game_feedback import MISS_REASON
    from app.services.real_game_feedback_adapter import prioritize_queued_misses
    from app.services.review_service import ensure_card_queued_after
    from app.services.queue_ordering import PRESENTATION_VERSION
    with postgres_store.connection() as database:
        database.execute("DELETE FROM daily_queue_days WHERE queue_date=?",(case['day'],))
        before = database.execute("SELECT position FROM daily_queue WHERE id=?",(case['entries'][0],)).fetchone()[0]
        if prior_cycle:
            database.execute("UPDATE daily_queue SET status='complete' WHERE id=?",(case['entries'][1],))
            database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(?,'correct',?,0,1,'study')",(case['identity']+'-b',case['day']))
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,'tactic')",(case['identity']+'-ordinary',case['identity'],FEN,case['day']))
            database.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,0)',(case['day'],case['identity']+'-ordinary'))
        ensure_card_queued_after(database,case['identity']+'-b',0,priority_reason=MISS_REASON,queue_date=case['day'],protect_presented=True,preserve_order=False)
        prioritize_queued_misses(database,case['day'])
        assert database.execute("SELECT position FROM daily_queue WHERE id=?",(case['entries'][0],)).fetchone()[0] == before
        assert [row[0] for row in database.execute("SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id LIMIT 2",(case['day'],))] == [case['identity']+'-a',case['identity']+'-b']
        assert not database.execute("SELECT 1 FROM daily_queue_days WHERE queue_date=?",(case['day'],)).fetchone()
        queue_application.randomize_daily_queue(database,case['day'])
        assert database.execute("SELECT membership_hash FROM daily_queue_days WHERE queue_date=?",(case['day'],)).fetchone()[0].startswith(PRESENTATION_VERSION+':')


def test_native_replacement_frozen_rank_is_not_certified_by_interleaved_burial(case, local_host_admission):
    from app.services import frozen_opening_order as frozen, postgres_queue_refresh as refresh, queue_application
    from app.study.openings.frozen_math import content_fingerprint
    with postgres_store.connection() as database:
        for position,suffix in enumerate(('c','d')):
            identifier = case['identity']+'-'+suffix
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,content_type) VALUES(?,?,'prefix',?,'[\"e2e4\"]','white','learning',?,'opening')", (identifier,case['identity'],FEN,case['day']))
            database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)',(case['identity'],identifier))
            database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind) VALUES(?,?,?,'new')",(case['day'],identifier,position))
        cards = [dict(database.execute('SELECT * FROM cards WHERE id=?',(case['identity']+'-'+suffix,)).fetchone()) for suffix in ('b','c','d')]
        for generation,ordered in enumerate((cards,list(reversed(cards))),1):
            ranks = [{**card,'rank':index,'fingerprint':content_fingerprint(card,[case['identity']]),'memberships':[case['identity']]} for index,card in enumerate(ordered,1)]
            publication = dict(run_id=case['identity']+f'-order-{generation}',checksum=case['identity']+str(generation),ranks=ranks)
            frozen.stage_run(database,publication,datetime.now(timezone.utc).isoformat())
            frozen.stage_ranks(database,publication,ranks)
            frozen.activate_run(database,publication)
            if generation==1:
                queue_application.randomize_daily_queue(database,case['day'])
        queue_commands.bury_queue_entry(database,dict(entry_id=case['entries'][0],card_id=case['identity']+'-a',expected_revision=1))
        assert database.execute('SELECT membership_hash FROM daily_queue_days WHERE queue_date=?',(case['day'],)).fetchone()[0].startswith('unmixed:')
    prepared = refresh._prepare_queue_randomization(case['day'])
    assert prepared['entries'] is not None
    with postgres_store.connection() as database:
        assert refresh._publish_queue_randomization(database,case['day'],prepared)
        assert [row[0] for row in database.execute("SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id",(case['day'],))] == [card['id'] for card in reversed(cards)]
        assert database.execute('SELECT status FROM daily_queue WHERE id=?',(case['entries'][0],)).fetchone()[0] == 'buried'


def test_native_large_presentation_uses_bounded_reads_at_fifty_ms(case, local_host_admission, monkeypatch):
    from app.services import postgres_queue_refresh as refresh
    prefix = case['identity']+'-budget-'
    with postgres_store.connection() as database:
        database.execute("UPDATE daily_queue SET status='buried' WHERE status='queued'")
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,content_type)
            SELECT %s||n::text,%s,'prefix',%s,'["e2e4"]','white','learning',%s,'tactic'
            FROM generate_series(1,1024) n""",(prefix,case['identity'],FEN,case['day']))
        database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,admission_kind)
            SELECT %s,%s||n::text,n,'review' FROM generate_series(1,1024) n""",(case['day'],prefix))
        database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind)
            SELECT %s||n::text,'correct',%s,0,1,'study' FROM generate_series(1,1024) n""",(prefix,case['day']))
    observed = []
    original = refresh._bounded_read
    def bounded(statement,parameters=(),**options):
        started = time.monotonic()
        result = original(statement,parameters,**options)
        observed.append((statement,len(result),time.monotonic()-started))
        return result
    monkeypatch.setenv('TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS','50')
    monkeypatch.setattr(refresh,'_bounded_read',bounded)
    prepared = refresh._prepare_queue_randomization(case['day'])
    assert len(prepared['entries']) == 1024
    pages = [row for row in observed if row[0].startswith('SELECT q.id,q.card_id')]
    assert sum(row[1] for row in pages) == 1024 and all(row[1]<=16 for row in pages)
    assert all('FROM reviews' not in row[0] for row in pages)
    assert all(row[1]<=16 for row in observed)
    print('QUEUE_PRESENTATION_BUDGET '+json.dumps(dict(rows=1024,transaction_timeout_ms=50,reads=len(observed),max_read_wall_ms=round(max(row[2] for row in observed)*1000,2))))


def test_native_split_then_refresh_retains_verified_after_four_placement(case):
    from app.services import queue_application
    from app.services.prefix_split import apply_prefix_split
    source = case['identity']+'-a'
    with postgres_store.connection() as database:
        database.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(case['entries'][1],))
        database.execute("UPDATE cards SET revision=3,moves_json='[\"e2e4\",\"e7e5\",\"g1f3\",\"b8c6\",\"f1b5\"]' WHERE id=?",(source,))
        database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(?,'again',?,0,0,'study')",(source,case['day']))
        for index in range(30):
            identifier = case['identity']+f'-split-tactic-{index}'
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,'tactic')",(identifier,case['identity'],FEN,case['day']))
            database.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)',(case['day'],identifier,index+1))
        queue_application.randomize_daily_queue(database,case['day'])
        result = apply_prefix_split(database,source,3)
        parent,child = result['parent']['card_id'],result['continuation']['card_id']
        database.execute('UPDATE cards SET pending_validation=0 WHERE id IN (?,?)',(parent,child))
        before = [tuple(row) for row in database.execute("SELECT id,card_id,position FROM daily_queue WHERE status='queued' ORDER BY position,id")]
        assert [row[1] for row in before[4:6]] == [parent,child]
        assert tuple(database.execute('SELECT cycle,attempt_state FROM daily_queue WHERE card_id=? AND status=\'queued\'',(parent,)).fetchone()) == (0,'clean')
        queue_application.randomize_daily_queue(database,case['day'])
        assert [tuple(row) for row in database.execute("SELECT id,card_id,position FROM daily_queue WHERE status='queued' ORDER BY position,id")] == before
