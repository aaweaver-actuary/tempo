from dataclasses import asdict
from datetime import datetime, timezone
import json, uuid
import chess, pytest
from tempo_learning_runtime.database import connection
from tempo_learning_runtime.threat_detection import defensive_fork_geometry
from tempo_learning_runtime.threat_models import (
    ExerciseAnchor,
    PositionContext,
    ThreatSeed,
    SourceLine,
    ThreatPolicy,
)
from tempo_learning_runtime.threat_validation import (
    AnalysisLine,
    AnalysisReport,
    EngineScore,
    make_validation_plan,
)
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


def defense_definition():
    fen = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"
    position = chess.Board(fen)
    position.push_uci("a2a3")
    geometry = defensive_fork_geometry(position, "b4c2")
    seed = ThreatSeed(
        "seed-a",
        "game-a",
        1,
        SourceLine("engine", 1, ("b4c2", "e1d2", "c2a1", "d2c1")),
        0,
        geometry,
    )
    anchor = ExerciseAnchor("seed-a", 0, PositionContext(fen, (), "white"), "a2a3", 0)
    policy = ThreatPolicy()
    plan = make_validation_plan(
        anchor, engine_version="sf19", network_version="nnue", policy=policy
    )
    best = AnalysisReport(
        plan.best_request,
        (
            AnalysisLine("e1f2", ("e1f2", "e8e7"), EngineScore(cp=0), 14),
            AnalysisLine("e1d2", ("e1d2", "e8e7"), EngineScore(cp=-20), 14),
        ),
        True,
    )
    historical = AnalysisReport(
        plan.historical_request,
        (
            AnalysisLine(
                "a2a3",
                ("a2a3", "b4c2", "e1d2", "c2a1", "d2c1"),
                EngineScore(cp=-180),
                14,
            ),
        ),
        True,
    )
    return {
        "candidate_id": "candidate-a",
        "finding_id": "finding-a",
        "game_id": "game-a",
        "exercise_revision": 1,
        "anchor": asdict(anchor),
        "seed": asdict(seed),
        "policy": asdict(policy),
        "reports": [asdict(best), asdict(historical)],
        "refutation_uci": list(historical.lines[0].pv_uci),
        "validation_state": "engine_supported",
        "source_game_url": "https://example.invalid/game-a",
    }


def test_defense_uses_retained_rubric_and_durably_requests_uncovered_move(case):
    definition = defense_definition()
    payload = bundle(
        [
            (
                "defense",
                {
                    "type": "move_line",
                    "accepted_lines": [["e1f2"]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
        ]
    )
    payload["publication"]["definitions"][0]["starting_fen"] = definition["anchor"][
        "position"
    ]["start_fen"]
    payload["facts"][0]["defense"] = definition
    activate(case, payload)
    card = head(case)
    assert case.client.get("/api/defense-exercises/candidate-a").status_code == 200
    assert card["source_ref"] == "candidate-a", "The production Study adapter needs the accepted candidate identity"
    recognition = {
        "attempt_id": uuid.uuid4().hex,
        "exercise_revision": 1,
        "rubric_version": 3,
        "queue_entry_id": card["queue_entry_id"],
        "dangerous_piece_square": "b4",
        "destination_square": "c2",
        "king_square": "e1",
        "major_square": "a1",
        "consequence": "checking_fork",
    }
    recognized = case.client.post(
        "/api/defense-exercises/candidate-a/recognition", json=recognition
    )
    assert recognized.status_code == 200, recognized.text
    assert recognized.json()["recognition_correct"] is True
    uncovered = {
        "attempt_id": uuid.uuid4().hex,
        "exercise_revision": 1,
        "queue_entry_id": card["queue_entry_id"],
        "move_uci": "a2a4",
        "recognition_attempt_id": recognition["attempt_id"],
    }
    needed = case.client.post(
        "/api/defense-exercises/candidate-a/attempt", json=uncovered
    )
    assert needed.status_code == 200, needed.text
    assert needed.json()["status"] == "needs_analysis"
    assert case.query("SELECT COUNT(*) FROM learning.analysis_obligations")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0
    covered = {**uncovered, "attempt_id": uuid.uuid4().hex, "move_uci": "e1f2"}
    result = case.client.post(
        "/api/defense-exercises/candidate-a/attempt", json=covered
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "correct"
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    assert (
        case.client.post(
            "/api/defense-exercises/candidate-a/attempt", json=covered
        ).json()
        == result.json()
    )
