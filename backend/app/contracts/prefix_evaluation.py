"""Version-one diagnostic contract; it grants no mutation authority."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

Depth = Annotated[StrictInt, Field(ge=1, le=20)]
Count = Annotated[int, Field(ge=0)]


class PrefixEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    snapshot_id: str = Field(min_length=1, max_length=128)
    selected_line_ids: list[str] = Field(max_length=2000)
    candidate_depths: dict[str, Depth] | None = Field(default=None, max_length=2000)


class DepthDistribution(BaseModel):
    depth: int
    line_count: Count


class SourceRoute(BaseModel):
    id: str
    name: str
    start_fen: str
    moves: list[str]
    trained_color: Literal['white', 'black']
    saved_depth: int


class SourceResponse(BaseModel):
    version: Literal[1]
    preview_only: Literal[True]
    estimate_basis: str
    repertoire_id: str
    graph_generation: int
    snapshot_id: str
    lines: list[SourceRoute]
    current_depth_distribution: list[DepthDistribution]


class StructuralMetrics(BaseModel):
    distinct_cards: Count
    prefix_cards: Count
    descendant_decision_cards: Count
    learner_decision_occurrences: Count
    distinct_learner_decisions: Count
    repeated_decisions_across_cards: Count
    repeated_decisions_within_cards: Count
    board_starts: Count


class StructuralDelta(BaseModel):
    distinct_cards: int
    prefix_cards: int
    descendant_decision_cards: int
    learner_decision_occurrences: int
    distinct_learner_decisions: int
    repeated_decisions_across_cards: int
    repeated_decisions_within_cards: int
    board_starts: int


class StructuralCard(BaseModel):
    card_id: str
    starting_fen: str
    moves: list[str]
    trained_color: Literal['white', 'black']
    roles: list[Literal['prefix', 'decision']]
    line_ids: list[str]
    decision_ids: list[str]


class StructuralStep(BaseModel):
    repertoire_id: str
    line_id: str
    decision_index: Count
    segment_kind: Literal['prefix', 'decision']
    first_decision_index: Count
    last_decision_index: Count
    decision_fen_keys: list[str]
    card_id: str
    parent_card_id: str | None
    decision_fen_key: str
    starting_fen: str
    moves: list[str]
    trained_color: Literal['white', 'black']


class StructuralPresentation(BaseModel):
    metrics: StructuralMetrics
    cards: list[StructuralCard]
    steps: list[StructuralStep]


class StructuralComparison(BaseModel):
    current: StructuralPresentation
    proposed: StructuralPresentation
    delta: StructuralDelta
    additional_starts: Count
    reduced_starts: Count
    unchanged_card_ids: list[str]
    added_card_ids: list[str]
    removed_card_ids: list[str]


class LineDepthComparison(BaseModel):
    line_id: str
    current_depth: int
    requested_depth: int
    current_effective_depth: Count
    proposed_effective_depth: Count


class PrefixEvaluationResponse(BaseModel):
    version: Literal[1]
    preview_only: Literal[True]
    estimate_basis: str
    repertoire_id: str
    graph_generation: int
    snapshot_id: str
    selected_line_ids: list[str]
    selected_line_count: Count
    status: Literal['empty_selection', 'changed', 'no_change']
    depth_configuration_changed: bool
    structure_changed: bool
    current_depth_distribution: list[DepthDistribution]
    line_depths: list[LineDepthComparison]
    selected: StructuralComparison
    whole_repertoire: StructuralComparison
