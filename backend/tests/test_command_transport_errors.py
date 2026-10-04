"""Failed command receipts preserve HTTP semantics across worker timing."""

from contextlib import nullcontext
import json
from types import SimpleNamespace

from celery.exceptions import TimeoutError as CeleryTimeout
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from psycopg.errors import SerializationFailure
import pytest

from app import command_dispatch, command_gateway


class ReceiptTransport:
    """In-memory SQL seam; gateway serialization and dispatch stay real."""

    def __init__(self):
        self.receipts = {}
        self.failure = HTTPException(409, {
            "code": "opening_evidence_conflict", "message": "example conflict",
            "aggregate_review_allowed": True,
        })
        self.deferred = False
        self.handler_calls = 0

    def execute(self, statement, parameters=()):
        row = None
        if statement.startswith("INSERT INTO operation_receipts"):
            operation_id, command_name, request_hash = parameters
            self.receipts.setdefault(operation_id, {
                "command_name": command_name, "request_hash": request_hash,
                "state": "pending", "response_json": None, "error_json": None,
                "attempt_token": None, "attempt_count": 1, "next_retry_at": None,
                "last_error_json": None, "lease_expires_at": None,
                "payload_json": None, "retry_cycle": 0, "cycle_attempt_count": 1,
            })
        elif statement.startswith("UPDATE operation_receipts SET state='failed'"):
            error_json, operation_id = parameters
            self.receipts[operation_id].update(state="failed", error_json=error_json)
        elif statement.startswith("SELECT command_name,request_hash,state,"):
            receipt = self.receipts.get(parameters[0])
            if receipt:
                columns = statement.split(" FROM ", 1)[0].removeprefix("SELECT ").split(",")
                row = tuple(receipt[column] for column in columns)
        else:
            assert statement.startswith(("SELECT pg_advisory_xact_lock", "SAVEPOINT ",
                                         "ROLLBACK TO SAVEPOINT "))
        return SimpleNamespace(fetchone=lambda: row)

    def handler(self, _database, _payload):
        self.handler_calls += 1
        raise self.failure

    def finish_worker(self):
        try:
            command_gateway.execute_command("transport-regression", "test.transport.failure", {})
        except RuntimeError:
            # Celery get(propagate=False) returns a failed task's exception.
            pass

    def get_task_result(self, **options):
        assert options["propagate"] is False
        if self.deferred:
            raise CeleryTimeout()
        self.finish_worker()


@pytest.fixture
def receipt_transport(monkeypatch):
    transport = ReceiptTransport()
    monkeypatch.setattr(command_gateway.postgres_store, "connection",
                        lambda **_options: nullcontext(SimpleNamespace(raw=transport)))
    monkeypatch.setitem(command_gateway._handlers, "test.transport.failure", transport.handler)
    monkeypatch.setattr(command_dispatch.celery_app, "send_task",
                        lambda *_args, **_options: SimpleNamespace(get=transport.get_task_result))
    application = FastAPI()

    @application.post("/review")
    def review():
        return command_dispatch.dispatch_command(
            "test.transport.failure", {}, idempotency_key="transport-regression",
        )

    @application.get("/operations/{operation_id}")
    def operation(operation_id: str):
        return command_gateway.read_operation(operation_id)

    return transport, TestClient(application)


@pytest.mark.parametrize("code", ["opening_evidence_conflict", "opening_evidence_unavailable"])
def test_command_receipt_preserves_structured_http_detail_through_immediate_dispatch(receipt_transport, code):
    transport, client = receipt_transport
    detail = {"code": code, "message": "example conflict", "aggregate_review_allowed": True}
    transport.failure = HTTPException(409, detail)
    response = client.post("/review")
    assert response.status_code == 409
    assert response.json() == {"detail": detail}
    persisted_error = json.loads(transport.receipts["transport-regression"]["error_json"])
    assert persisted_error == {"status_code": 409, "message": str(transport.failure), "detail": detail}
    assert client.post("/review").json() == response.json()
    assert transport.handler_calls == 1


@pytest.mark.parametrize("detail", ["invalid field", {"field": "missing"}, ["invalid"], [], {},
                                    None, False, 0, "", 3.25])
def test_command_dispatch_preserves_json_native_http_detail_including_empty_values(receipt_transport, detail):
    transport, client = receipt_transport
    transport.failure = HTTPException(422, detail)
    # Include explicit null, which HTTPException's constructor otherwise replaces.
    transport.failure.detail = detail
    response = client.post("/review")
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert "detail" in client.get("/operations/transport-regression").json()["error"]


@pytest.mark.parametrize("code", ["opening_evidence_conflict", "opening_evidence_unavailable"])
def test_deferred_command_failure_keeps_legacy_message_and_structured_detail(receipt_transport, code):
    transport, client = receipt_transport
    transport.failure = HTTPException(409, {
        "code": code, "message": "example conflict", "aggregate_review_allowed": True,
    })
    transport.deferred = True
    response = client.post("/review")
    assert response.status_code == 202
    assert response.json()["operation_id"] == "transport-regression"
    transport.finish_worker()
    receipt = client.get("/operations/transport-regression").json()
    assert receipt["state"] == "failed"
    assert receipt["error"]["message"] == str(transport.failure)
    assert code in receipt["error"]["message"]
    assert receipt["error"]["detail"] == transport.failure.detail


def test_non_http_command_failure_retains_existing_message_and_status(receipt_transport):
    transport, client = receipt_transport
    transport.failure = ValueError("legacy failure")
    transport.finish_worker()
    response = client.post("/review")
    assert response.status_code == 500
    assert response.json() == {"detail": "legacy failure"}
    assert json.loads(transport.receipts["transport-regression"]["error_json"]) == {
        "status_code": 500, "message": "legacy failure",
    }
    assert transport.handler_calls == 1


def test_command_dispatch_accepts_legacy_http_failure_receipt_without_detail(receipt_transport):
    transport, client = receipt_transport
    transport.finish_worker()
    legacy_error = {"status_code": 409, "message": str(transport.failure)}
    transport.receipts["transport-regression"]["error_json"] = json.dumps(legacy_error)
    response = client.post("/review")
    assert response.status_code == 409
    assert response.json() == {"detail": legacy_error["message"]}
    assert transport.handler_calls == 1


@pytest.mark.parametrize("detail", [{"unsupported": object()}, ("tuple",), {1: "non-string key"},
                                    float("nan"), "a" * (16_384 - 1)])
def test_command_receipt_unsafe_or_oversized_http_detail_retains_legacy_envelope(receipt_transport, detail):
    transport, client = receipt_transport
    transport.failure = HTTPException(422, detail)
    response = client.post("/review")
    assert response.status_code == 422
    assert response.json() == {"detail": str(transport.failure)}
    assert json.loads(transport.receipts["transport-regression"]["error_json"]) == {
        "status_code": 422, "message": str(transport.failure),
    }


def test_command_receipt_accepts_http_detail_at_exact_serialized_byte_limit(receipt_transport):
    transport, client = receipt_transport
    detail = "a" * (16_384 - 2)  # JSON quotes occupy two bytes.
    assert len(json.dumps(detail).encode("utf-8")) == 16_384
    transport.failure = HTTPException(409, detail)
    assert client.post("/review").json() == {"detail": detail}


def test_command_receipt_cyclic_http_detail_does_not_lose_failure_receipt(receipt_transport):
    transport, client = receipt_transport
    detail = []
    detail.append(detail)
    transport.failure = HTTPException(422, detail)
    response = client.post("/review")
    assert response.status_code == 422
    assert response.json() == {"detail": str(transport.failure)}
    assert client.get("/operations/transport-regression").json()["state"] == "failed"


@pytest.mark.parametrize('failure', [HTTPException(409, {'code':'opening_evidence_conflict', 'aggregate_review_allowed':True}),
                                   ValueError('invalid preparation')])
def test_prepared_command_failure_retains_existing_structured_receipt(receipt_transport, monkeypatch, failure):
    transport, client = receipt_transport
    def prepare(_payload):
        raise failure
    monkeypatch.setitem(command_gateway._preparers, 'test.transport.failure', prepare)
    response = client.post('/review')
    assert response.status_code == (failure.status_code if isinstance(failure, HTTPException) else 500)
    assert response.json() == {'detail':failure.detail if isinstance(failure, HTTPException) else str(failure)}
    receipt = client.get('/operations/transport-regression').json()
    assert receipt['state'] == 'failed' and receipt['error']['message'] == str(failure)
    assert transport.handler_calls == 0


def test_prepared_command_database_failure_remains_retryable_without_failed_receipt(receipt_transport, monkeypatch):
    transport, _client = receipt_transport
    def prepare(_payload):
        raise SerializationFailure('source changed')
    monkeypatch.setitem(command_gateway._preparers, 'test.transport.failure', prepare)
    with pytest.raises(SerializationFailure):
        command_gateway.execute_command('transport-regression', 'test.transport.failure', {})
    assert not transport.receipts and transport.handler_calls == 0
