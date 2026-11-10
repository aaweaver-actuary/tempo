"""Named regressions for authored studies and the shared review queue."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from app.contracts.studies import ExerciseAnswer, ExerciseSpecification
from app.services.study_grading import evaluate_answer, validate_exercise
from app.studies.pgn import preview_pgn


FEN = "4k3/8/8/8/8/8/8/4K1N1 w - - 0 1"
ROOT_PGN = f'[Event "Original synthetic position"]\n[SetUp "1"]\n[FEN "{FEN}"]\n\n*\n'


def test_study_python_grader_matches_original_golden_cases():
    cases = json.loads(
        (Path(__file__).parents[2] / "tests/fixtures/study-grading.json").read_text()
    )
    specification_adapter = TypeAdapter(ExerciseSpecification)
    answer_adapter = TypeAdapter(ExerciseAnswer)
    for case in cases:
        specification = specification_adapter.validate_python(case["specification"])
        answer = answer_adapter.validate_python(case["answer"])
        validate_exercise(specification, case["fen"])
        assert (
            evaluate_answer(specification, answer, case["fen"])["outcome"]
            == case["outcome"]
        ), case["name"]


def test_study_pgn_preview_keeps_variations_annotations_and_invalid_record_diagnostics():
    pgn = (
        '[Event "Original branches"]\n[SetUp "1"]\n[FEN "' + FEN + '"]\n\n'
        "1. Nf3 {[%cal Gg1f3] first branch} (1. Nh3 $1 {second branch}) *\n"
    )
    preview = preview_pgn(pgn)
    record = preview["records"][0]
    assert record["valid"] and len(record["nodes"]) == 3
    assert record["nodes"][0]["history"] == []
    assert [node["move_uci"] for node in record["nodes"][1:]] == ["g1f3", "g1h3"]
    assert record["nodes"][1]["arrows"] == [
        {"from": "g1", "to": "f3", "color": "green"}
    ]
    assert record["nodes"][2]["nags"] == [1]
    invalid = preview_pgn('[Event "Invalid"]\n\n1. e4 e5 2. Qh5?? Nc6 3. Qh5 *\n')[
        "records"
    ][0]
    assert not invalid["valid"] and invalid["diagnostics"]
