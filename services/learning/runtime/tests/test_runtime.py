from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Event
from zoneinfo import ZoneInfo
import json, time, uuid
import chess, psycopg, pytest
from tempo_chess_core import card_id
from tempo_learning_runtime.database import connection
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
