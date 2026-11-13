"""Named regressions for the retained authored Study grader."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from app.contracts.study_exercises import ExerciseAnswer, ExerciseSpecification
from app.study.policy.study_grading import evaluate_answer, validate_exercise


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
        result = evaluate_answer(specification, answer, case["fen"])
        assert case["expected_validation_stage"] == "graded", case["name"]
        assert result["outcome"] == case["outcome"], case["name"]
        assert all(
            result[key] == value
            for key, value in case["expected_semantic_result"].items()
        ), case["name"]
