"""Validated, versioned contracts for authored study exercises."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExerciseText(StrictModel):
    prompt: str = Field(min_length=1, max_length=2000)
    hint: str = Field(default="", max_length=2000)
    explanation: str = Field(default="", max_length=10000)
    further_analysis: str = Field(default="", max_length=10000)


class MoveExercise(ExerciseText):
    type: Literal["move_line"]
    grading_policy: Literal["reference", "open_judgment"] = "reference"
    mode: Literal["single", "stepwise_line"] = "single"
    accepted_lines: list[list[str]] = Field(min_length=1, max_length=20)


class SquareExercise(ExerciseText):
    type: Literal["square_set"]
    required: list[str] = Field(default_factory=list, max_length=64)
    optional: list[str] = Field(default_factory=list, max_length=64)
    candidate_region: list[str] | None = None
    criterion: str = Field(min_length=1, max_length=2000)


class KnightExercise(ExerciseText):
    type: Literal["knight_path"]
    start_square: str
    target_squares: list[str] = Field(min_length=1, max_length=8)
    minimum_hops: int = Field(default=1, ge=0, le=3)
    maximum_hops: int = Field(default=2, ge=0, le=3)
    hop_rule: Literal["at_most", "exact"] = "at_most"
    occupancy_rule: Literal["static_non_capturing"] = "static_non_capturing"


class ChoiceOption(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)


class ChoiceExercise(ExerciseText):
    type: Literal["choice"]
    options: list[ChoiceOption] = Field(min_length=2, max_length=12)
    correct_option_ids: list[str] = Field(min_length=1)


class ExplanationExercise(ExerciseText):
    type: Literal["explanation"]
    rubric: str = Field(min_length=1, max_length=10000)


ExerciseSpecification = Annotated[
    Union[MoveExercise, SquareExercise, KnightExercise, ChoiceExercise, ExplanationExercise],
    Field(discriminator="type"),
]


class MoveAnswer(StrictModel):
    type: Literal["move_line"]
    moves: list[str] = Field(min_length=1, max_length=40)


class SquareAnswer(StrictModel):
    type: Literal["square_set"]
    squares: list[str] = Field(max_length=64)


class KnightAnswer(StrictModel):
    type: Literal["knight_path"]
    reachable: bool
    path: list[str] = Field(default_factory=list, max_length=4)


class ChoiceAnswer(StrictModel):
    type: Literal["choice"]
    option_ids: list[str] = Field(min_length=1, max_length=12)
    displayed_order: list[str] = Field(default_factory=list, max_length=12)


class ExplanationAnswer(StrictModel):
    type: Literal["explanation"]
    text: str = Field(default="", max_length=10000)
    ready: bool = True


ExerciseAnswer = Annotated[
    Union[MoveAnswer, SquareAnswer, KnightAnswer, ChoiceAnswer, ExplanationAnswer],
    Field(discriminator="type"),
]


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
