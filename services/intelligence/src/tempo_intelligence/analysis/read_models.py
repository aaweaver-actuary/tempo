"""Versioned, packaged read contracts; evidence/user/training states stay separate."""
from typing import Literal
from pydantic import Field, model_validator
from ..models import StrictModel,Identifier,Digest,Revision,UciMove
from .models import Lineage

class Representative(StrictModel):
    game_id: Identifier | None
    learner_id: Identifier | None = None
    provider: Identifier | None = None
    provider_game_id: Identifier | None = None
    source_revision: Revision
    source_digest: str | None = None
    ply: int | None = Field(default=None,ge=0,le=1000)
    fen: str = Field(max_length=120)
    actual_move: UciMove | None
    learner_color: Literal['white','black']
    starting_fen: str | None = None
    history: list[UciMove] = Field(default_factory=list,max_length=1000)
    played_at: str | None = None
    position_id: Identifier | None = None
    workspace_id: Identifier | None = None

class Action(StrictModel):
    available: bool
    reason: str | None = None

class Finding(StrictModel):
    finding_id: Digest
    finding_revision: Revision
    evidence_revision: Digest
    category: Literal['repertoire_miss','practical_gap','engine_opportunity']
    policy_version: Literal['c-analysis-v1']
    workspace_id: Identifier
    repertoire_id: Identifier | None
    policy_id: Identifier | None
    learner_color: Literal['white','black']
    lineage: Lineage | None
    representative: Representative
    incident_id: Digest
    supporting_games: list[Identifier] = Field(max_length=1000)
    supporting_game_count: int = Field(ge=0)
    eligible_encounter_games: int | None = Field(default=None,ge=0)
    actual_frequency: float | None = Field(default=None,ge=0,le=1)
    evidence_ids: list[Identifier] = Field(max_length=4096)
    evidence_status: Literal['current','stale','partial','unknown','unavailable']
    reason_codes: list[str]
    uncertainty: list[str]
    expected_moves: list[UciMove]
    card_id: Identifier | None
    decision_id: Identifier | None
    engine: dict | None
    gap_kind: Literal['observed','proactive'] | None
    source_evidence: list[dict]
    related_findings: list[dict] = Field(default_factory=list)
    delivery: dict | None = None
    delivery_status: str
    learning_settlement: Literal['unknown']
    activation_status: Literal['activated','unknown','retired']
    proposal_status: Literal['not_created']
    dismissal_state: Literal['open','dismissed']
    proposal_cost: int | None = Field(default=None,ge=0)
    actions: dict[str,Action]
    opponent_fen: str | None = None
    opponent_move: UciMove | None = None
    opponent_ply: int | None = Field(default=None,ge=0)
    route_ids: list[Identifier] | None = None
    scope_revision: Revision | None = None
    proposal_anchor: dict | None = None
    response_proposal: dict | None = None
    input_id: Identifier
    input_revision: Revision

    @model_validator(mode="after")
    def supported_scope(self):
        identities = (self.repertoire_id, self.policy_id, self.lineage)
        if self.category != 'engine_opportunity' and any(value is None for value in identities):
            raise ValueError('source_finding_lineage_required')
        return self

class ReportPage(StrictModel):
    schema_version: Literal[1]
    workspace_id: Identifier
    run_id: Identifier
    revision: Revision | None = None
    generation: Revision | None
    status: Literal['pending','current','stale']
    input_id: Identifier | None = None
    input_revision: Revision | None = None
    input_producer: str | None = None
    source_provenance: dict | None = None
    scope: dict | None = None
    source_completeness: Literal['complete','partial','unavailable'] | None = None
    reasons: list[str] = Field(default_factory=list)
    unknown_coverage: list[dict] = Field(default_factory=list,max_length=100)
    unknown_coverage_count: int | None = Field(default=None,ge=0)
    engine_scan_complete: bool = False
    unprocessed_count: int | None = Field(default=None,ge=0)
    engine_availability: str | None = None
    insufficient_quality_count: int | None = Field(default=None,ge=0)
    finding_count: int | None = Field(default=None,ge=0)
    complete: bool = False
    derivation_version: Literal[1,2] = 1
    ordering: str = "category-source-rank-semantic-v1"
    policy_version: Literal['c-analysis-v1'] = 'c-analysis-v1'
    items: list[Finding] = Field(max_length=100)
    next_cursor: str | None
