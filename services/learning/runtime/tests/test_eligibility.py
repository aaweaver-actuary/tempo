from concurrent.futures import ThreadPoolExecutor
import uuid
from test_runtime import activate, bundle, head, review_payload, submit
from tempo_learning_runtime.database import connection
from tempo_learning_runtime.maintenance import run_once


def drain(case):
    with case.database.scope():
        for _ in range(30):
            if not run_once():
                break
        else:
            raise AssertionError("Bounded finite work did not complete")


def test_parent_exposure_and_accepted_integrity_fence_introductions(case):
    specifications = [
        (
            "opening",
            {
                "type": "move_line",
                "accepted_lines": [[move]],
                "mode": "single",
                "grading_policy": "reference",
            },
        )
        for move in ("e2e4", "d2d4", "g1f3")
    ]
    payload = bundle(specifications)
    root = payload["facts"][0]["card_id"]
    payload["facts"][0]["route_order"] = 1
    payload["facts"][1].update(parent_card_id=root, route_order=0)
    payload["facts"][2]["integrity_allowed"] = False
    activate(case, payload)
    assert {
        row[0] for row in case.query("SELECT card_id FROM learning.daily_queue")
    } == {root}
    card = head(case)
    assert submit(case, card, review_payload(card)).status_code == 200
    drain(case)
    queued = {
        row[0]
        for row in case.query(
            "SELECT card_id FROM learning.daily_queue WHERE status='queued'"
        )
    }
    assert payload["facts"][1]["card_id"] in queued
    assert payload["facts"][2]["card_id"] not in queued


def test_competing_head_reads_retain_one_issued_origin(case):
    activate(case)
    with ThreadPoolExecutor(2) as executor:
        responses = list(
            executor.map(lambda _: case.client.get("/api/queue/window"), range(2))
        )
    assert [item.status_code for item in responses] == [200, 200]
    assert (
        responses[0].json()["cards"][0]["queue_entry_id"]
        == responses[1].json()["cards"][0]["queue_entry_id"]
    )
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.queue_attempt_origins WHERE issued_as_head=1"
        )[0][0]
        == 1
    )


def test_middlegame_review_uses_the_same_owned_scheduling(case):
    payload = bundle(
        [
            (
                "middlegame",
                {
                    "type": "move_line",
                    "accepted_lines": [["e2e4"]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
        ]
    )
    activate(case, payload)
    card = head(case)
    result = submit(case, card, review_payload(card))
    assert result.status_code == 200, result.text
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


def test_typed_guidance_downgrades_and_buries_exposed_siblings(case):
    specification = {
        "type": "choice",
        "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        "correct_option_ids": ["a"],
    }
    payload = bundle(
        [("study_exercise", specification), ("study_exercise", specification)]
    )
    for fact in payload["facts"]:
        fact["sibling_group"] = "siblings-a"
    activate(case, payload)
    card = head(case)
    request = {
        "attempt_id": uuid.uuid4().hex,
        "revision": 1,
        "answer": {"type": "choice", "option_ids": ["a"]},
        "context": "review",
        "queue_entry_id": card["queue_entry_id"],
        "queue_cycle": card["cycle"],
        "card_id": card["id"],
        "hint_seen": True,
    }
    result = case.client.post(
        f"/api/studies/study-a/exercises/{card['study_exercise_id']}/attempts",
        json=request,
    )
    assert result.status_code == 200, result.text
    assert result.json()["rating"] == "again"
    assert case.query("SELECT rating,guided FROM learning.reviews")[0][0] == "again"
    assert case.query("SELECT COUNT(*) FROM learning.study_sibling_burials")[0][0] == 1


def test_local_evidence_delivery_promotes_miss_and_required_review_settles(case):
    activate(case)
    card = head(case)
    assert submit(case, card, review_payload(card)).status_code == 200
    from test_evidence_revisions import delivery, drain
    payload = delivery(card["id"])
    headers = {"Authorization": "Bearer " + case.configuration.evidence_token}
    delivered = case.client.post("/internal/evidence", json=payload, headers=headers)
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["status"] == "completed"
    assert (
        case.client.post("/internal/evidence", json=payload, headers=headers).json()
        == delivered.json()
    )
    drain(case)
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.daily_queue WHERE status='queued' AND gameplay_priority_reason IS NOT NULL"
        )[0][0]
        == 1
    ), [(table,[tuple(row) for row in case.query("SELECT * FROM learning."+table)]) for table in ("remediation_cards","background_tasks","daily_queue")]
    promoted = head(case)
    saved = submit(case, promoted, review_payload(promoted))
    assert saved.status_code == 200, saved.text
    assert (
        case.query(
            "SELECT settled FROM learning.remediation_review_receipts WHERE settled"
        )[0][0]
        is True
    )
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.daily_queue WHERE gameplay_priority_reason IS NOT NULL"
        )[0][0]
        == 0
    )
