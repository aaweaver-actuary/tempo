from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Event
from zoneinfo import ZoneInfo
import json, time, uuid
import chess, psycopg, pytest
from tempo_chess_core import card_id
from tempo_learning_database.connections import connection
from tempo_learning_runtime import gateway
from tempo_learning_runtime.durable_tasks import enqueue_task_in_transaction
from tempo_learning_runtime.maintenance import run_once


def bundle(specifications=None):
    now = datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    specifications = specifications or [
        (
            "opening",
            {
                "type": "move_line",
                "accepted_lines": [["e2e4"]],
                "mode": "single",
                "grading_policy": "reference",
            },
        )
    ]
    definitions = []
    facts = []
    for index, (kind, specification) in enumerate(specifications):
        identifier = (
            card_id(chess.STARTING_FEN, specification["accepted_lines"][0])
            if kind in ("opening", "tactic", "middlegame")
            else f"card-{index}"
        )
        definitions.append(
            {
                "card_id": identifier,
                "decision_id": f"decision-{index}",
                "policy_id": "policy-a",
                "source_revision": 1,
                "publication_id": "publication-a",
                "publication_generation": 1,
                "exercise_revision": 1,
                "starting_fen": chess.STARTING_FEN,
                "prompt": "Exercise",
                "specification": specification,
            }
        )
        fact = {
            "card_id": identifier,
            "content_type": kind,
            "memberships": ["repertoire-a"] if kind != "study_exercise" else [],
            "trained_color": "white",
            "due_date": day,
            "route_order": index,
        }
        if kind == "study_exercise":
            fact.update(
                study_id="study-a",
                exercise_id=f"exercise-{index}",
                legacy_specification={
                    **specification,
                    "prompt": "Exercise",
                    "hint": "",
                    "explanation": "",
                },
            )
        facts.append(fact)
    return {
        "schema_version": 1,
        "publication": {
            "contract_version": 1,
            "workspace_id": "workspace-a",
            "publication_id": "publication-a",
            "source_revision": 1,
            "publication_generation": 1,
            "built_at": now.isoformat(),
            "definitions": definitions,
        },
        "facts": facts,
    }


def activate(case, payload=None):
    payload = payload or bundle()
    response = case.client.post(
        "/internal/publications/stage",
        json=payload,
        headers={"Authorization": "Bearer " + case.configuration.operator_token},
    )
    assert response.status_code == 200, response.text
    # Staging alone must not admit or create a serving card.
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0
    response = case.client.post(
        "/internal/publications/activate",
        json={
            "activation_id": uuid.uuid4().hex,
            "publication_id": "publication-a",
            "publication_generation": 1,
            "expected_activation_revision": 0,
        },
        headers={"Authorization": "Bearer " + case.configuration.operator_token},
    )
    assert response.status_code == 200, response.text
    with case.database.scope():
        for _ in range(100):
            if not run_once():
                break
        else:
            raise AssertionError("Essential work did not finish")
    return payload


def head(case):
    response = case.client.get("/api/queue/window?include_opening_evidence=true")
    assert response.status_code == 200, response.text
    assert response.json()["cards"], response.text
    return response.json()["cards"][0]


def review_payload(card, **changes):
    return {
        "outcome": "correct",
        "guided": False,
        "queue_entry_id": card["queue_entry_id"],
        "expected_revision": card["revision"],
        "attempt_id": uuid.uuid4().hex,
        **changes,
    }


def submit(case, card, payload, operation=None, suffix=""):
    return case.client.post(
        f"/api/cards/{card['id']}/review" + suffix,
        json=payload,
        headers={"Idempotency-Key": operation or uuid.uuid4().hex},
    )


def test_runtime_ready_authorized_and_ordinary_introduction(case):
    assert case.client.get("/health/ready").status_code == 200
    activate(case)
    card = head(case)
    assert card["content_type"] == "opening"
    assert card["opening_decision_manifest"]["card_id"] == card["id"]
    result = submit(case, card, review_payload(card))
    assert result.status_code == 200, result.text
    assert result.json()["persisted"] is True
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


def test_lost_response_and_logical_retry_are_immutable(case):
    activate(case)
    card = head(case)
    payload = review_payload(card)
    operation = uuid.uuid4().hex
    saved = submit(case, card, payload, operation)
    assert saved.status_code == 200, saved.text
    assert submit(case, card, payload, operation).json() == saved.json()
    assert submit(case, card, payload).json() == saved.json()
    changed = {**payload, "outcome": "again"}
    assert submit(case, card, changed, operation).status_code == 409
    assert submit(case, card, changed).status_code == 409
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


def test_failed_queued_intent_defers_original_and_next_study_command_progresses(case, monkeypatch):
    activate(case)
    card = head(case)
    poison_id, next_id = uuid.uuid4().hex, uuid.uuid4().hex
    poison_payload = {'card_id': card['id'], 'review': review_payload(card)}
    next_payload = {'card_id': card['id'], 'review': review_payload(card)}
    registry = case.client.app.state.command_registry
    with case.database.scope(), gateway.registry_scope(registry):
        gateway.record_intent(poison_id, 'cards.review', poison_payload)
        gateway.record_intent(next_id, 'cards.review', next_payload)
    case.query("UPDATE learning.operation_receipts SET created_at=NOW()-interval '2 seconds' WHERE operation_id=%s", (poison_id,))
    case.query("UPDATE learning.operation_receipts SET created_at=NOW()-interval '1 second' WHERE operation_id=%s", (next_id,))

    def receipt(operation):
        return tuple(case.query('SELECT operation_id,command_name,request_hash,payload_json,state,response_json,error_json,next_retry_at FROM learning.operation_receipts WHERE operation_id=%s', (operation,))[0])

    before = receipt(poison_id)
    next_before = receipt(next_id)
    assert before[0:2] == (poison_id, 'cards.review')
    assert json.loads(before[3]) == poison_payload and before[4:] == ('queued', None, None, None)
    assert next_before[:2] == (next_id, 'cards.review')
    assert json.loads(next_before[3]) == next_payload and next_before[4:] == ('queued', None, None, None)
    reviews_before = case.query('SELECT COUNT(*) FROM learning.reviews')[0][0]
    queue_before = [tuple(row) for row in case.query('SELECT id,card_id,status,attempt_state FROM learning.daily_queue ORDER BY id')]
    actual_execute = gateway.execute
    calls = []
    def fail_only_original(operation_id, **options):
        calls.append(operation_id)
        if operation_id == poison_id:
            raise RuntimeError('synthetic retained operation failure')
        return actual_execute(operation_id, **options)
    monkeypatch.setattr(gateway, 'execute', fail_only_original)

    with case.database.scope(), gateway.registry_scope(registry):
        assert run_once()
    deferred = receipt(poison_id)
    assert deferred[:7] == before[:7] and deferred[7] is not None
    assert case.query('SELECT COUNT(*) FROM learning.reviews')[0][0] == reviews_before
    assert [tuple(row) for row in case.query('SELECT id,card_id,status,attempt_state FROM learning.daily_queue ORDER BY id')] == queue_before
    # Preserve the observed real defer; extend only this fixture's due time so
    # host scheduling cannot consume the one-second window before the next call.
    case.query("UPDATE learning.operation_receipts SET next_retry_at=NOW()+interval '1 hour' WHERE operation_id=%s", (poison_id,))
    with case.database.scope(), gateway.registry_scope(registry):
        assert run_once()
    assert calls == [poison_id, next_id]
    assert receipt(poison_id)[:7] == before[:7]
    next_after = receipt(next_id)
    assert next_after[:4] == next_before[:4] and next_after[4] == 'complete'
    assert json.loads(next_after[5])['persisted'] is True
    assert case.query("SELECT COUNT(*) FROM learning.reviews WHERE source_kind='study' AND source_ref=%s",
                      ('attempt:' + next_payload['review']['attempt_id'],))[0][0] == 1
    assert case.query('SELECT COUNT(*) FROM learning.reviews')[0][0] == reviews_before + 1


def test_scope_origin_and_role_denials(case):
    assert (
        case.client.get(
            "/api/queue/window", headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    assert (
        case.client.get(
            "/api/queue/window", headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert (
        case.client.get(
            "/api/queue/window", headers={"X-Tempo-Workspace": "other"}
        ).status_code
        == 403
    )
    for url in [case.configuration.database_url, case.configuration.reader_url]:
        with psycopg.connect(url) as database:
            database.execute("SET TRANSACTION READ WRITE")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                database.execute("CREATE TABLE learning.unauthorized(id int)")
    with psycopg.connect(case.configuration.reader_url) as database:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            database.execute("DELETE FROM learning.cards")
    with psycopg.connect(case.configuration.database_url) as database:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            database.execute("UPDATE learning.instance SET workspace_id='other'")


def test_authoritative_concurrent_first_enqueue_and_creator_rollback(case):
    barrier = Barrier(2)

    def enqueue(rollback=False):
        with case.database.scope(), connection() as database:
            barrier.wait(timeout=5)
            task = enqueue_task_in_transaction(
                database, "daily_queue", "concurrent", {"queue_date": "2026-10-15"}
            )
            if rollback:
                raise ValueError("creator rollback")
            return task

    with ThreadPoolExecutor(2) as executor:
        results = [
            future.result()
            for future in [executor.submit(enqueue), executor.submit(enqueue)]
        ]
    assert results[0]["id"] == results[1]["id"]
    assert sorted(result["generation"] for result in results) == [1, 2]
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.background_task_events e LEFT JOIN learning.background_tasks t ON t.id=e.task_id WHERE t.id IS NULL"
        )[0][0]
        == 0
    )
    # A rolled-back speculative creator must not retain its ID or event.
    with case.database.scope():
        with pytest.raises(ValueError):
            with connection() as database:
                abandoned = enqueue_task_in_transaction(
                    database, "daily_queue", "rollback", {"queue_date": "2026-10-15"}
                )
                raise ValueError("rollback")
        with connection() as database:
            survivor = enqueue_task_in_transaction(
                database, "daily_queue", "rollback", {"queue_date": "2026-10-15"}
            )
    assert survivor["id"] != abandoned["id"]
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.background_task_events WHERE task_id=%s",
            (abandoned["id"],),
        )[0][0]
        == 0
    )
