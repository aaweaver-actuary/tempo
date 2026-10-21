"""Gateway lifecycle and remediation authority retain borrowed storage semantics."""
from contextlib import contextmanager
from datetime import date,datetime,timezone
import json
from types import SimpleNamespace

import chess
from fastapi import HTTPException
import pytest

from tempo_learning_runtime import gateway,remediation_adapter as remediation
from tempo_learning_runtime.review_conflicts import ReviewConflict


DAY=date(2026,10,21)
MOMENT=datetime(2026,10,21,12,tzinfo=timezone.utc)


@pytest.fixture
def gateway_context(monkeypatch):
    events=[]
    @contextmanager
    def connection(**options):
        events.append(("begin",options))
        try:
            yield None
        except Exception:
            events.append("outer rollback")
            raise
        else:
            events.append("commit")
    monkeypatch.setattr(gateway,"connection",connection)
    monkeypatch.setattr(gateway.storage,"lock_operation",lambda *_: events.append("lock"))
    monkeypatch.setattr(gateway.storage,"begin_command_handler",lambda *_: events.append("savepoint"))
    monkeypatch.setattr(gateway.storage,"rollback_command_handler",lambda *_: events.append("savepoint rollback"))
    monkeypatch.setattr(gateway.storage,"release_command_handler",lambda *_: events.append("release"))
    monkeypatch.setattr(gateway.storage,"complete_operation",lambda *_: events.append("complete"))
    monkeypatch.setattr(gateway,"_handlers",{})
    return events


def operation(**changes):
    return {**dict(command_name="study.command",state="queued",payload_json="{}"),**changes}


def test_operation_credential_gate_precedes_savepoint_and_handler(monkeypatch,gateway_context):
    events=gateway_context
    monkeypatch.setattr(gateway.storage,"operation_for_update",lambda *_: operation(command_name="publication.activate.v2"))
    with pytest.raises(HTTPException) as error:
        gateway.execute("operation",publication=False)
    assert error.value.status_code==403
    assert events==[("begin",dict(background=False)),"lock","outer rollback"]


@pytest.mark.parametrize("database_failure",[False,True])
def test_handler_failure_retains_savepoint_receipt_vs_outer_retry_semantics(monkeypatch,gateway_context,database_failure):
    events=gateway_context
    monkeypatch.setattr(gateway.storage,"operation_for_update",lambda *_: operation())
    def handler(*_):
        events.append("handler")
        if database_failure:raise gateway.psycopg.OperationalError("busy")
        raise ReviewConflict("changed","Changed",retryable=False)
    gateway.register_command("study.command",handler)
    def failed(_db,payload,_operation):
        data=json.loads(payload)
        assert (data["code"],data["retryable"],data["status_code"])==("changed",False,409)
        events.append("failed receipt")
    monkeypatch.setattr(gateway.storage,"fail_operation",failed)
    if database_failure:
        with pytest.raises(gateway.psycopg.OperationalError):gateway.execute("operation")
        assert events==[("begin",dict(background=False)),"lock","savepoint","handler","outer rollback"]
    else:
        gateway.execute("operation")
        assert events==[("begin",dict(background=False)),"lock","savepoint","handler","savepoint rollback","failed receipt","commit"]


def test_logical_attempt_conflict_prevents_handler_before_failure_receipt(monkeypatch,gateway_context):
    events=gateway_context
    monkeypatch.setattr(gateway.storage,"operation_for_update",lambda *_: operation(
        command_name="tactics.attempt.submit",payload_json='{"request":{"attempt_id":"attempt"}}'))
    monkeypatch.setattr(gateway.storage,"bind_logical_request",lambda *_: events.append("bind"))
    monkeypatch.setattr(gateway.storage,"logical_request_identity",lambda *_: ("different",))
    monkeypatch.setattr(gateway.storage,"fail_operation",lambda *_: events.append("failed receipt"))
    gateway.register_command("tactics.attempt.submit",lambda *_: pytest.fail("Conflicting logical attempt reached handler"))
    gateway.execute("operation")
    assert events==[("begin",dict(background=False)),"lock","savepoint","bind","savepoint rollback","failed receipt","commit"]


@pytest.mark.parametrize("state",["complete","failed"])
def test_terminal_operation_does_not_reexecute_handler(monkeypatch,gateway_context,state):
    monkeypatch.setattr(gateway.storage,"operation_for_update",lambda *_: operation(state=state))
    gateway.execute("operation")
    assert gateway_context==[("begin",dict(background=False)),"lock","commit"]


def test_intent_identity_conflict_rolls_back_own_transaction(monkeypatch,gateway_context):
    events=gateway_context
    gateway.register_command("study.command",lambda *_: None)
    monkeypatch.setattr(gateway.storage,"insert_operation_intent",lambda *_: events.append("intent"))
    monkeypatch.setattr(gateway.storage,"intent_identity",lambda *_: ("other","digest"))
    with pytest.raises(ReviewConflict) as error:gateway.record_intent("operation","study.command",{})
    assert error.value.code=="command_identity_reused"
    assert events==[("begin",{}),"intent","outer rollback"]


def test_borrowed_connection_retains_per_cursor_tuple_factory_without_mutating_owner():
    calls=[]
    raw=SimpleNamespace(info=object(),row_factory="owner factory")
    def cursor(**options):
        calls.append(options)
        return SimpleNamespace(execute=lambda *args: calls.append(args) or "result")
    raw.cursor=cursor
    borrowed=remediation.BorrowedConnection(SimpleNamespace(raw=raw))
    assert borrowed.execute("statement",("value",))=="result"
    assert calls==[dict(row_factory=remediation.storage.tuple_row),("statement",("value",))]
    assert borrowed.info is raw.info and raw.row_factory=="owner factory"


@pytest.mark.parametrize("rejection",[None,"workspace","ambiguous"])
def test_accepted_definition_uses_learning_workspace_alias_and_unique_projection(monkeypatch,rejection):
    from tempo_learning_runtime import publication_bridge
    monkeypatch.setattr(remediation,"workspace_id",lambda: "workspace")
    monkeypatch.setattr(publication_bridge,"physical_id",lambda *_: "physical")
    monkeypatch.setattr(remediation.storage,"content_alias",lambda *_: ("authored",))
    definition=dict(card_id="authored",exercise_revision=2,decision_id="decision",starting_fen=chess.STARTING_FEN,
        specification=dict(type="move_line",grading_policy="reference",accepted_lines=[["e2e4"]]))
    bindings=[dict(card_id="authored",decision_id="decision",exercise_ply=0)]
    if rejection=="ambiguous":bindings.append(dict(card_id="authored",decision_id="decision",exercise_ply=2))
    document=dict(schema_version=2,publication=dict(workspace_id="other" if rejection=="workspace" else "workspace",definitions=[definition]),
        routes=[dict(bindings=bindings)])
    monkeypatch.setattr(remediation.storage,"accepted_publication_facts",lambda *_: (json.dumps(document),2,1,0,None))
    projected=[]
    monkeypatch.setattr(publication_bridge,"project_decision",lambda *args: projected.append(args) or (chess.STARTING_FEN,("e2e4",)))
    result=remediation.AcceptedDefinitions(None).decision("authored",None,"pub",1)
    if rejection:
        assert result is None and projected==[]
    else:
        assert result.card_id=="physical" and result.aliases==("authored",) and result.active
        assert projected[0][-1]==0


@pytest.mark.parametrize("existing",[False,True])
def test_attempt_binding_preserves_original_issuance_time(monkeypatch,existing):
    current=SimpleNamespace(active=True,publication_id="pub",publication_generation=1)
    monkeypatch.setattr(remediation,"AcceptedDefinitions",lambda _db: SimpleNamespace(for_card=lambda _: current))
    monkeypatch.setattr(remediation,"workspace_id",lambda: "workspace")
    earlier=datetime(2026,10,20,12,tzinfo=timezone.utc)
    monkeypatch.setattr(remediation.storage,"attempt_issued_at",lambda *_: (earlier,) if existing else None)
    clock=[]
    monkeypatch.setattr(remediation,"datetime",SimpleNamespace(now=lambda _zone: clock.append("now") or MOMENT))
    bound=[]
    monkeypatch.setattr(remediation,"repository",lambda *_: SimpleNamespace(bind_attempt=lambda *args: bound.append(args)))
    remediation.bind_issued_attempt(None,4,"card",2)
    assert bound[0][0].issued_at==(earlier if existing else MOMENT)
    assert clock==([] if existing else ["now"])


@pytest.fixture
def settlement(monkeypatch):
    current=SimpleNamespace(active=True,card_id="card",card_revision=2,publication_id="pub",publication_generation=1)
    monkeypatch.setattr(remediation,"AcceptedDefinitions",lambda _db: SimpleNamespace(for_card=lambda _: current))
    monkeypatch.setattr(remediation,"workspace_id",lambda: "workspace")
    monkeypatch.setattr(remediation.storage,"bound_attempt",lambda *_: ("identity","card",2,"pub",1,MOMENT))
    return current


def test_settlement_rejects_changed_bound_authority_before_review_lookup(monkeypatch,settlement):
    monkeypatch.setattr(remediation.storage,"bound_attempt",lambda *_: ("identity","card",2,"pub",2,MOMENT))
    monkeypatch.setattr(remediation.storage,"settlement_review",lambda *_: pytest.fail("Changed authority read review"))
    assert remediation.settle_review(None,"card",7,queue_entry_id=4,revision=2) is None


def test_settlement_requires_issued_origin_without_creating_new_authority(monkeypatch,settlement):
    monkeypatch.setattr(remediation.storage,"settlement_review",lambda *_: ("correct",0,MOMENT.isoformat(),"study",None))
    monkeypatch.setattr(remediation.storage,"issued_origin",lambda *_: None)
    monkeypatch.setattr(remediation,"repository",lambda *_: pytest.fail("Unissued attempt obtained settlement authority"))
    assert remediation.settle_review(None,"card",7,queue_entry_id=4,revision=2) is None


@pytest.mark.parametrize("provided",[False,True])
def test_settlement_followup_preserves_settle_clear_miss_callback_order(monkeypatch,settlement,provided):
    events=[]
    monkeypatch.setattr(remediation.storage,"settlement_review",lambda *_: ("correct",0,MOMENT.isoformat(),"study",None))
    monkeypatch.setattr(remediation.storage,"issued_origin",lambda *_: (0,))
    monkeypatch.setattr(remediation,"repository",lambda *_: SimpleNamespace(
        settle=lambda *_: events.append("settle"),has_current_miss=lambda _: events.append("miss") or True))
    monkeypatch.setattr(remediation,"clear_priority",lambda *_: events.append("clear"))
    monkeypatch.setattr(remediation,"candidate",lambda *_: "candidate")
    monkeypatch.setattr(remediation,"date",SimpleNamespace(today=lambda: DAY))
    monkeypatch.setattr(remediation,"request_remediation",lambda *_: events.append("enqueue"))
    followup=(lambda day: events.append(("followup",day))) if provided else None
    remediation.settle_review(None,"card",7,queue_entry_id=4,revision=2,request_followup=followup)
    assert events==["settle","clear","miss",("followup",DAY.isoformat()) if provided else "enqueue"]
