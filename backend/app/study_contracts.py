"""Validated, versioned contracts for authored study exercises."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Executable exercise/answer identity belongs to the Learning grader. Keep the
# legacy endpoint envelopes and their existing fields below this compatibility boundary.
from tempo_learning_runtime import study_contracts as _exercise_contracts

ExerciseText = _exercise_contracts.ExerciseText
MoveExercise = _exercise_contracts.MoveExercise
SquareExercise = _exercise_contracts.SquareExercise
KnightExercise = _exercise_contracts.KnightExercise
ChoiceOption = _exercise_contracts.ChoiceOption
ChoiceExercise = _exercise_contracts.ChoiceExercise
ExplanationExercise = _exercise_contracts.ExplanationExercise
ExerciseSpecification = _exercise_contracts.ExerciseSpecification
MoveAnswer = _exercise_contracts.MoveAnswer
SquareAnswer = _exercise_contracts.SquareAnswer
KnightAnswer = _exercise_contracts.KnightAnswer
ChoiceAnswer = _exercise_contracts.ChoiceAnswer
ExplanationAnswer = _exercise_contracts.ExplanationAnswer
ExerciseAnswer = _exercise_contracts.ExerciseAnswer


class StudyCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    source: dict[str, str] = Field(default_factory=dict)


class ChapterCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)


class StudyImportPreviewRequest(StrictModel):
    chapter_id: str
    raw_pgn: str = Field(min_length=1, max_length=2_000_000)
    filename: str = Field(default="Imported PGN", max_length=255)
    source_group_id: str | None = None


class StudyImportCommitRequest(StudyImportPreviewRequest):
    preview_digest: str
    selected_records: list[int]
    mode: Literal["append", "update", "copy"] = "append"


class ExerciseCreate(StrictModel):
    position_id: str
    specification: ExerciseSpecification
    source: dict[str, str] = Field(default_factory=dict)
    sibling_group: str | None = None
    point_value: float | None = Field(default=None, ge=0)


class ExerciseRevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    specification: ExerciseSpecification
    schedule_decision: Literal["reset", "preserve"] = "reset"
    source: dict[str, str] | None = None
    sibling_group: str | None = None
    point_value: float | None = Field(default=None, ge=0)


class StudyAttemptRequest(StrictModel):
    attempt_id: str
    revision: int = Field(ge=1)
    answer: ExerciseAnswer
    context: Literal["practice", "review"]
    queue_entry_id: int | None = None
    queue_cycle: int | None = None
    card_id: str | None = None
    expected_review_id: int | None = None
    started_at: str | None = None
    hint_seen: bool = False
    solution_seen_before_answer: bool = False


class StudySelfAssessmentRequest(StrictModel):
    rating: Literal["correct", "again"]


class StudyLinkCreate(StrictModel):
    source_position_id: str
    target_position_id: str | None = None
    target_exercise_id: str | None = None
    relation: Literal["illustrates", "contrasts", "follow_up"]


class StudyBundleImportRequest(StrictModel):
    bundle: dict
    mode: Literal["preserve", "copy"] = "preserve"
