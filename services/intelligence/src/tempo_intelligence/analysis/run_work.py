"""Existing run admission, command, receipt response and dismissal workflows."""
from ..errors import DomainError
from ..identity import digest
from .classify import ordered_positions
from .models import payload_digest


def receipt_result(command_id,payload,operations):
    row=operations.receipt_row(command_id,payload)
    return {**row["payload"], "request_digest": row["digest"]} if row else None


def save_receipt_result(command_id,payload,result,operations):
    result["request_digest"] = digest(payload)
    operations.write_receipt(command_id,payload,result)


def submit_run(value,admission,lookup,evidence,workspace,transaction):
    admission()(value.workspace_id)
    source,games=lookup()(value.input_id,value.input_revision)
    if source["state"] != "accepted":
        raise DomainError("analysis_input_not_accepted")
    records=evidence()(value.source_evidence_ids)
    for record in records:
        if value.ranking_evidence_id==record["evidence_id"] and record["descriptor"]["kind"]!=value.ranking_source:
            raise DomainError("ranking_population_mismatch",422)
    positions=ordered_positions(games)
    if len(positions)>10000:
        raise DomainError("analysis_scope_exceeds_position_limit",422)
    payload={**value.model_dump(mode="json"), "derivation_version":2}
    with transaction() as operations:
        # Per-run transaction lock prevents concurrent first submissions/head replacement.
        operations.lock_run(value)
        old=operations.existing_run(value)
        head=operations.head_revision(value)
        operations.verify_submission(value,old,head,lambda:payload_digest(value))
        previous=operations.previous(value,head) if head else None
        next_generation=previous["requested_generation"]+1 if previous else 1
        operations.create_run(value,payload,next_generation,previous,lambda:payload_digest(value))
    for start in range(0,len(positions),64):
        with transaction() as operations:
            for ordinal,position in enumerate(positions[start:start+64],start):
                position_id=digest({"game":position["game_id"],"revision":position["source_revision"],"ply":position["ply"]})
                position.update(position_id=position_id,workspace_id=workspace())
                operations.position(value,ordinal,position_id,position,lambda:"pending" if ordinal<value.position_budget else "unbudgeted")
    with transaction() as operations:
        operations.lock_run(value)
        head=operations.locked_head_revision(value)
        if operations.newer_head(head,value):
            return operations.current_status(value.run_id)
        operations.promote(value)
        return operations.current_status(value.run_id)


def command_run(run_id,command,transaction):
    payload={"run_id":run_id,**command.model_dump(mode="json")}
    with transaction() as operations:
        run=operations.head(run_id,True)
        saved=operations.receipt(command.command_id,payload)
        if saved:
            return saved
        operations.verify_command(run,command)
        if command.action in {"continue","resume","retry"}:
            if command.action=="continue":
                operations.extend_budget(command,run_id,run)
            if command.action=="retry":
                operations.retry_quality(run_id,run)
            if command.action in {"retry","resume"}:
                operations.resume_failed(run_id,run)
            operations.queue_run(run_id,run)
        elif command.action=="cancel":
            operations.reap_cancelled(run_id,run)
            operations.request_cancel(run_id,run)
            operations.cancel_pending(run_id,run)
            running=operations.running(run_id,run)
            if not running:
                operations.finish_cancel(run_id,run)
        else:
            raise DomainError("invalid_run_action",422)
        result=operations.current_status(run_id); result.update(accepted=True,command_id=command.command_id)
        operations.save_receipt(command.command_id,payload,result)
        return result


def dismiss_finding(run_id,finding_id,generation,command,finding,workspace,transaction):
    payload={"run_id":run_id,"finding_id":finding_id,"generation":generation,**command.model_dump(mode="json")}
    with transaction() as operations:
        saved=operations.receipt(command.command_id,payload)
        if saved:
            return saved
    item=finding()(run_id,finding_id,generation)["finding"]
    with transaction() as operations:
        operations.lock_user_state()
        run=operations.head(run_id,True)
        saved=operations.receipt(command.command_id,payload)
        if saved:
            return saved
        operations.verify_dismissal(command,item,run,generation)
        state="dismissed" if command.action=="dismiss" else "open"
        operations.dismiss(finding_id,state,item,run_id)
        result={"schema_version":1,"workspace_id":workspace(),"accepted":True,"command_id":command.command_id,"dismissal_state":state,"publication_pending":True}
        operations.save_receipt(command.command_id,payload,result)
    return result
