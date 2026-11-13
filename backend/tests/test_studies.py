"""Named cross-runtime regressions for the retained authored Study grader."""
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError
from app.contracts.study_exercises import ExerciseAnswer, ExerciseSpecification
from app.study.policy.study_grading import evaluate_answer, validate_exercise

CASES = json.loads((Path(__file__).parents[2] / "tests/fixtures/study-grading.json").read_text())

@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_study_python_grader_matches_original_golden_cases(case):
    try:
        specification = TypeAdapter(ExerciseSpecification).validate_python(case["specification"])
    except ValidationError:
        assert case["expected_validation_stage"] == "specification_schema"
        return
    try:
        validate_exercise(specification, case["fen"])
    except ValueError:
        assert case["expected_validation_stage"] == "specification_semantic"
        return
    try:
        answer = TypeAdapter(ExerciseAnswer).validate_python(case["answer"])
    except ValidationError:
        assert case["expected_validation_stage"] == "answer_schema"
        return
    assert case["expected_validation_stage"] == "graded"
    assert evaluate_answer(specification, answer, case["fen"]) == case["expected_semantic_result"]
