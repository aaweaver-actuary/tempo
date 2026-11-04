from datetime import datetime, timezone
import json, uuid
import chess, pytest
from tempo_learning_database.connections import connection
from test_runtime import activate, bundle, head, review_payload, submit

SPECIFICATIONS = [
    (
        {
            "type": "move_line",
            "accepted_lines": [["e2e4"]],
            "mode": "single",
            "grading_policy": "reference",
        },
        {"type": "move_line", "moves": ["e2e4"]},
    ),
    (
        {
            "type": "square_set",
            "required": ["e2"],
            "optional": [],
            "criterion": "Select e2",
        },
        {"type": "square_set", "squares": ["e2"]},
    ),
    (
        {
            "type": "knight_path",
            "start_square": "b1",
            "target_squares": ["c3"],
            "minimum_hops": 1,
            "maximum_hops": 1,
            "hop_rule": "exact",
            "occupancy_rule": "static_non_capturing",
        },
        {"type": "knight_path", "reachable": True, "path": ["b1", "c3"]},
    ),
    (
        {
            "type": "choice",
            "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
            "correct_option_ids": ["a"],
        },
        {"type": "choice", "option_ids": ["a"]},
    ),
    (
        {"type": "explanation", "rubric": "Explain the position"},
        {"type": "explanation", "text": "A reasoned explanation", "ready": True},
    ),
]


@pytest.mark.parametrize(
    "specification,answer",
    SPECIFICATIONS,
    ids=[item[0]["type"] for item in SPECIFICATIONS],
)
def test_each_typed_family_review_self_assessment_and_practice(
    case, specification, answer
):
    activate(case, bundle([("study_exercise", specification)]))
    card = head(case)
    presented = case.client.get("/api/studies/study-a/exercises/exercise-0/present")
    assert presented.status_code == 200, presented.text
    assert presented.json()["type"] == specification["type"]
    assert "correct_option_ids" not in presented.json()
    attempt_id = uuid.uuid4().hex
    payload = {
        "attempt_id": attempt_id,
        "revision": 1,
        "answer": answer,
        "context": "review",
        "queue_entry_id": card["queue_entry_id"],
        "queue_cycle": card["cycle"],
        "card_id": card["id"],
    }
    endpoint = "/api/studies/study-a/exercises/exercise-0/attempts"
    response = case.client.post(endpoint, json=payload)
    assert response.status_code == 200, response.text
    assert case.client.post(endpoint, json=payload).json() == response.json()
    if specification["type"] == "explanation":
        assert response.json()["pending_self_assessment"]
        assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0
        assessment = case.client.post(
            endpoint + "/" + attempt_id + "/self-assess", json={"rating": "correct"}
        )
        assert assessment.status_code == 200, assessment.text
        assert assessment.json()["persisted"]
        assert (
            case.client.post(
                endpoint + "/" + attempt_id + "/self-assess", json={"rating": "correct"}
            ).json()
            == assessment.json()
        )
        assert (
            case.client.post(
                endpoint + "/" + attempt_id + "/self-assess",
                json={"rating": "again"},
                headers={"Idempotency-Key": uuid.uuid4().hex},
            ).status_code
            == 409
        )
    else:
        assert response.json()["persisted"]
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    practice = {
        "attempt_id": uuid.uuid4().hex,
        "revision": 1,
        "answer": answer,
        "context": "practice",
    }
    practiced = case.client.post(endpoint, json=practice)
    assert practiced.status_code == 200, practiced.text
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    assert case.client.get(endpoint + "/" + attempt_id + "/feedback").status_code == 200
    wrong = {
        **payload,
        "answer": {**answer, "text": "different"}
        if specification["type"] == "explanation"
        else answer,
        "revision": 2,
    }
    assert (
        case.client.post(
            endpoint, json=wrong, headers={"Idempotency-Key": uuid.uuid4().hex}
        ).status_code
        == 409
    )


def test_tactical_discovery_attempt_binding_and_clean_retry(case):
    payload = {
        "attempt_id": uuid.uuid4().hex,
        "puzzle_id": "puzzle-a",
        "deck_id": "pack-a",
        "correct": False,
        "clean": False,
        "source_fen": chess.STARTING_FEN,
        "moves": ["e2e4", "e7e5"],
    }
    endpoint = "/api/tactics/attempt"
    response = case.client.post(endpoint, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "normal"
    assert case.client.post(endpoint, json=payload).json() == response.json()
    assert (
        case.client.post(
            endpoint,
            json={**payload, "correct": True},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        ).status_code
        == 409
    )
    assert (
        case.query("SELECT COUNT(*) FROM learning.tactic_discovery_attempts")[0][0] == 1
    )
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 1


def test_retired_defense_routes_are_absent(case):
    root = "/api/defense-exercises/candidate-a"
    assert case.client.get(root).status_code == 404
    for suffix in ("recognition", "attempt"):
        assert case.client.post(f"{root}/{suffix}", json={}).status_code == 404


def test_tactic_retained_progress_and_original_attempt_replay(case):
    """F09 subset: retained rows stay authoritative across retry and a new solve."""
    from tempo_chess_core import card_id
    from tempo_learning_runtime.puzzles import validate_puzzle_record

    payload = {
        "attempt_id": "original-attempt-f09", "puzzle_id": "original-puzzle-f09",
        "deck_id": "original-pack-f09", "correct": True, "clean": True,
        "source_fen": chess.STARTING_FEN, "moves": ["e2e4", "e7e5"],
    }
    training_fen, solution = validate_puzzle_record(
        {"FEN": payload["source_fen"], "Moves": payload["moves"]}
    )
    original_card = card_id(training_fen, solution)
    original_result = {"card_id": original_card, "mode": "light", "next_due": "2026-01-08"}
    case.query(
        "INSERT INTO learning.tactic_progress(puzzle_id,deck_id,card_id,clean_pass_at,admitted_at,admission_mode) VALUES(%s,%s,%s,%s,%s,%s)",
        (payload["puzzle_id"], payload["deck_id"], original_card, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "light"),
    )
    case.query(
        "INSERT INTO learning.tactic_discovery_attempts(id,deck_id,puzzle_id,clean,result_json) VALUES(%s,%s,%s,%s,%s)",
        (payload["attempt_id"], payload["deck_id"], payload["puzzle_id"], 1, json.dumps(original_result)),
    )
    endpoint = "/api/tactics/attempt"
    for _ in range(2):
        response = case.client.post(endpoint, json=payload)
        assert response.status_code == 200, response.text
        assert response.json() == original_result
    assert case.query("SELECT COUNT(*) FROM learning.tactic_discovery_attempts")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0
    response = case.client.post(endpoint, json={**payload, "attempt_id": "new-attempt-f09"})
    assert response.status_code == 200, response.text
    assert response.json()["card_id"] == original_card
    assert [tuple(row) for row in case.query("SELECT puzzle_id,deck_id,card_id,clean_pass_at,admitted_at FROM learning.tactic_progress")] == [
        (payload["puzzle_id"], payload["deck_id"], original_card, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")
    ]
    assert case.query("SELECT COUNT(*) FROM learning.tactic_discovery_attempts")[0][0] == 2
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 1
