"""Existing pure engine-job validation and durable execution application workflows."""
import uuid

from ..errors import DomainError
from ..identity import digest
from .engine import validate_result, comparison, compatible_worker, validate_pair_jobs


def claim_engine_work(run_id,build_job,worker_identity,workspace,transaction):
    """build_job is a pure B client helper; it performs no process/network I/O."""
    with transaction() as operations:
        run=operations.head(run_id,True)
        if run["state"] in {"cancel_requested","cancelled"}:
            # Expired executions have lost their owning session; late results remain fenced.
            running=operations.reap_cancelled(run_id,run)
            if not running and run["state"]=="cancel_requested":
                operations.finish_cancelled_claim(run_id,run)
            return None
        if run["payload"].get("derivation_version",1)!=2:
            raise DomainError("legacy_analysis_revision_required")
        row=operations.eligible_position(run_id,run)
        if not row:
            return None
        if not operations.reserve_capacity():
            return None
        generation=row["execution_generation"]+1
        depth=20
        if row["accepted_root"]:
            root=operations.claim_root(row)
            depth=root["variations"][0]["depth"]
            if worker_identity is not None and not compatible_worker(root, worker_identity):
                operations.incompatible_profile(run_id,run,row)
                return {"error":"incompatible_engine_profile"}
        job=build_job(row["payload"],run_id,run["revision"],generation,row["stage"],depth)
        from tempo_analysis_jobs_contracts.v1 import StockfishJob
        job=StockfishJob.model_validate(job).model_dump(mode="json")
        position=row["payload"]
        expected_roots=[] if row["stage"]=="root" else [position["actual_move"]]
        if (job["workspace_id"],job["request_revision"],job["execution_generation"],job["fen"],job["moves"],job["search_moves"],job["max_depth"],job["multi_pv"])!=(workspace(),run["revision"],generation,position["starting_fen"],position["history"],expected_roots,depth,1):
            raise DomainError("analysis_job_input_mismatch",422)
        token=uuid.uuid4().hex
        operations.lease(job,generation,run_id,run,row,token)
        return {**row,"execution_generation":generation,"lease_token":token,"job":job}


def accept_engine_result(claim,result,transaction):
    wire=validate_result(claim["job"],result)
    result_id=digest(wire)
    with transaction() as operations:
        run=operations.head(claim["run_id"],True)
        old=operations.existing_result(wire)
        if old:
            operations.verify_replay(old,wire)
            return {"result_id":old["result_id"],"replayed":True}
        row=operations.locked_position(claim)
        operations.verify_acceptance(run,claim,wire,row)
        operations.verify_execution(wire,claim)
        operations.save_result(result_id,wire)
        if row["stage"]=="root":
            complete=wire["variations"][0]["moves"][0]==row["payload"]["actual_move"] or wire["variations"][0]["depth"]<18
            operations.accept_root(result_id,lambda:("complete" if complete else "pending","root" if complete else "played","insufficient_quality" if wire["variations"][0]["depth"]<18 else None),claim)
        else:
            root=operations.accepted_root(row)
            root_job=operations.root_job(row)
            validate_pair_jobs(root_job, claim["job"], row["payload"]["actual_move"])
            compared=comparison(root,wire,row["payload"]["actual_move"])
            operations.accept_played(result_id,lambda:"insufficient_quality" if compared["status"]=="insufficient_quality" else None,claim)
        operations.finish_acceptance(claim)
    return {"result_id":result_id,"replayed":False}


def fail_engine_work(claim,retryable,code,deferred,status_payload,workspace,transaction):
    allowed={"engine_unavailable","worker_session_lost","worker_busy","execution_failed","cancelled","incompatible_engine_profile"}
    if code not in allowed:
        code="execution_failed"
    with transaction() as operations:
        run=operations.head(claim["run_id"],True)
        row=operations.failure_position(claim)
        if not operations.active_failure_claim(row,claim):
            return False
        if status_payload is not None:
            from tempo_analysis_jobs_contracts.v1 import JobStatus
            status=JobStatus.model_validate(status_payload)
            if (status.workspace_id,status.job_id,status.request_revision,status.execution_generation)!=(workspace(),claim["job"]["job_id"],claim["revision"],claim["execution_generation"]) or status.status not in {"failed","cancelled"}:
                raise DomainError("analysis_status_identity_mismatch",422)
            event={"model":"JobStatus","payload":status.model_dump(mode="json")}
        else:
            event={"model":"IntelligenceExecutionFailure","reason":code}
        operations.save_event(lambda:digest({"job":claim["job"]["job_id"],"generation":claim["execution_generation"],"event":event}),claim,event)
        attempts=row["attempts"]+(0 if deferred else 1)
        cancelled=run["state"] in {"cancel_requested","cancelled"} or code=="cancelled"
        state="cancelled" if cancelled else "retry_wait" if retryable and attempts<3 else "failed"
        operations.fail_position(state,attempts,code,lambda:min(60,2**attempts),claim)
        if cancelled and not operations.running_after_failure(claim):
            operations.finish_cancelled_failure(claim)
    return True


def mark_engine_work_unavailable(run_id,transaction):
    with transaction() as operations:
        run=operations.head(run_id,True)
        if run["payload"].get("derivation_version",1)!=2:
            raise DomainError("legacy_analysis_revision_required")
        changed=operations.mark_unavailable(run_id,run)
        if changed:
            operations.request_unavailable_generation(run_id,run)
