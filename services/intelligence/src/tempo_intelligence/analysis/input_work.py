"""Existing input/page admission and finalization through fixed owner operations."""
from ..errors import DomainError
from .models import payload_digest


def admit_manifest(value,admission,workspace,transaction):
    admission()(value.workspace_id)
    payload = value.model_dump(mode="json")
    fingerprint = payload_digest(value)
    with transaction() as operations:
        row = operations.manifest(value,payload,fingerprint)
    return {"schema_version":1,"workspace_id":workspace(),"input_id":value.input_id,"revision":value.revision,"state":row["state"],"digest":fingerprint}


def admit_page(value,workspace,transaction):
    payload = value.model_dump(mode="json")
    fingerprint = payload_digest(value)
    with transaction() as operations:
        row = operations.page_input(value)
        reference = next((p for p in row["payload"]["pages"] if p["page_id"]==value.page_id),None)
        if not reference or reference["digest"] != fingerprint or reference["game_count"] != len(value.games):
            raise DomainError("analysis_page_mismatch",422)
        operations.page(value,payload,fingerprint)
    return {"schema_version":1,"workspace_id":workspace(),"accepted":True,"page_id":value.page_id}


def finalize_input(input_id,command,lookup,workspace,transaction):
    if command.action!="finalize":
        raise DomainError("invalid_input_action",422)
    row,games = lookup()(input_id,command.expected_revision)
    if len(games)!=len(row["payload"]["game_ids"]):
        raise DomainError("analysis_input_incomplete")
    with transaction() as operations:
        saved = operations.receipt(command.command_id,{"input_id":input_id,**command.model_dump(mode="json")})
        if saved:
            return saved
        operations.finalization_scope(input_id,command)
        if sorted(g["game_id"] for g in games) != sorted(row["payload"]["game_ids"]):
            raise DomainError("analysis_game_scope_mismatch",422)
        operations.accept(input_id,command)
        result={"schema_version":1,"workspace_id":workspace(),"accepted":True,"state":"accepted","input_id":input_id,"revision":command.expected_revision}
        operations.save_receipt(command.command_id,{"input_id":input_id,**command.model_dump(mode="json")},result)
    return result
