"""Legacy authored Study keeps one contract identity with its owning grader."""

import json
from pathlib import Path

import pytest

from app.contracts import studies as legacy
from pydantic import TypeAdapter, ValidationError
from app.contracts import study_exercises as owned


EXECUTABLE_CONTRACTS = (
    "ExerciseText",
    "MoveExercise",
    "SquareExercise",
    "KnightExercise",
    "ChoiceOption",
    "ChoiceExercise",
    "ExplanationExercise",
    "ExerciseSpecification",
    "MoveAnswer",
    "SquareAnswer",
    "KnightAnswer",
    "ChoiceAnswer",
    "ExplanationAnswer",
    "ExerciseAnswer",
)
REQUEST_ENVELOPES = (
    "StrictModel",
    "StudyCreate",
    "ChapterCreate",
    "StudyImportPreviewRequest",
    "StudyImportCommitRequest",
    "ExerciseCreate",
    "ExerciseRevisionRequest",
    "StudyAttemptRequest",
    "StudySelfAssessmentRequest",
    "StudyLinkCreate",
    "StudyBundleImportRequest",
)
CORPORA = sorted(
    (Path(__file__).resolve().parents[2] / "public/data/studies").glob(
        "stalemate-swindles-*.tempo-study.json"
    )
)


def test_legacy_study_contracts_share_learning_grader_model_identity():
    assert CORPORA, "The real checked-in portable corpora must remain selected"
    mismatches = [
        name
        for name in EXECUTABLE_CONTRACTS
        if getattr(legacy, name) is not getattr(owned, name)
    ]
    assert not mismatches, (
        f"Legacy consumers construct contracts the Learning grader cannot recognize: {mismatches}"
    )
    assert all(
        getattr(legacy, name).__module__ == "app.contracts.studies"
        for name in REQUEST_ENVELOPES
    ), "Legacy endpoint envelopes retain their existing owner and fields"


@pytest.mark.parametrize("corpus", CORPORA, ids=lambda path: path.name)
def test_checked_in_stalemate_corpus_validates_with_legacy_learning_contract_boundary(
    corpus,
):
    bundle = json.loads(corpus.read_text())
    revisions = bundle["tables"]["study_exercise_revisions"]
    assert revisions
    adapter = TypeAdapter(owned.ExerciseSpecification)
    for revision in revisions:
        specification = json.loads(revision["specification_json"])
        assert adapter.validate_python(specification) == TypeAdapter(legacy.ExerciseSpecification).validate_python(specification)


def test_study_envelopes_keep_defaults_and_reject_extra_fields():
    assert legacy.StudyCreate(title="Study").model_dump() == {"title": "Study", "description": "", "source": {}}
    assert legacy.StudyImportCommitRequest(chapter_id="c", raw_pgn="p", preview_digest="d", selected_records=[]).mode == "append"
    for name in REQUEST_ENVELOPES:
        model = getattr(legacy, name)
        assert model.model_config["extra"] == "forbid"
        assert model.model_json_schema()
    with pytest.raises(ValidationError):
        legacy.StudyCreate(title="Study", unexpected=True)
