"""Immutable v1 dry-run contract; no field grants permission to mutate."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .prefix_evaluation_contracts import Depth
from .services.opening_graph import GraphStep


class FrozenRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class PrefixTransitionRequest(FrozenRecord):
    snapshot_id: str = Field(min_length=1, max_length=128)
    selected_line_ids: tuple[str, ...] = Field(max_length=2000)
    candidate_depths: dict[str, Depth] = Field(max_length=2000)


class DepthChange(FrozenRecord):
    line_id: str
    before: int
    after: int


class Blocker(FrozenRecord):
    code: str
    object_id: str
    reason: str


class MembershipDisposition(FrozenRecord):
    repertoire_id: str
    card_id: str
    action: Literal['preserve', 'add_generated', 'obsolete', 'blocked']
    canonical_route_source: int


class CardDisposition(FrozenRecord):
    card_id: str
    classification: Literal['unchanged', 'reuse_existing', 'new_replacement',
                            'retained_shared', 'retired', 'conflicting']
    expected_revision: int | None
    lifecycle: Literal['preserve', 'create', 'archive', 'blocked']
    owner_before: str | None
    owner_after: str | None
    kind_after: str | None
    state_handling: Literal['preserve', 'fresh', 'blocked']
    # JSON strings keep arbitrary existing schedule/seed data deeply immutable.
    schedule_json: str | None
    schedule_seed_json: str | None
    review_ids: tuple[int, ...]
    history_handling: Literal['retain_on_original_identity'] = 'retain_on_original_identity'
    new_review_count: Literal[0] = 0
    new_decision_observation_count: Literal[0] = 0


class AttemptDisposition(FrozenRecord):
    kind: Literal['queue', 'origin', 'opening_attempt', 'study_attempt', 'pending_command', 'receipt']
    object_id: str
    card_id: str
    action: Literal['preserve', 'supersede_projection', 'retain_for_recovery',
                    'retire_with_conflict', 'replay_receipt', 'blocked']


class SubmissionDisposition(FrozenRecord):
    card_id: str
    expected_revision: int | None
    completed_receipt: Literal['replay_original_result'] = 'replay_original_result'
    unresolved_submission: Literal['existing_identity_recovery', 'archived_identity_conflict', 'blocked']
    replacement_credit: Literal[False] = False


class PrefixTransitionPlan(FrozenRecord):
    version: Literal[1] = 1
    dry_run: Literal[True] = True
    repertoire_id: str
    snapshot_id: str
    transition_snapshot_id: str
    graph_generation: int
    graph_policy_version: int
    position_version: int
    structural_version: int
    study_day: str
    plan_id: str
    status: Literal['no_op', 'ready', 'blocked']
    selected_line_ids: tuple[str, ...]
    depth_changes: tuple[DepthChange, ...]
    current_steps: tuple[GraphStep, ...]
    proposed_steps: tuple[GraphStep, ...]
    cards: tuple[CardDisposition, ...]
    memberships: tuple[MembershipDisposition, ...]
    attempts: tuple[AttemptDisposition, ...]
    submissions: tuple[SubmissionDisposition, ...]
    blockers: tuple[Blocker, ...]
    offline_visibility: Literal['client_local_attempts_not_enumerable'] = 'client_local_attempts_not_enumerable'


class PrefixTransitionApplyRequest(PrefixTransitionRequest):
    plan_id: str = Field(min_length=1, max_length=128)
    transition_snapshot_id: str = Field(min_length=1, max_length=128)
    graph_generation: int = Field(ge=1, strict=True)
    study_day: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')


class PrefixTransitionApplicationResult(FrozenRecord):
    status: Literal['complete']
    operation_id: str
    plan_id: str
    repertoire_id: str
    graph_generation: int
    no_op: bool
    queue_date: str | None = None


class PrefixTransitionPendingResponse(FrozenRecord):
    operation_id: str
    state: str
    message: str | None = None
