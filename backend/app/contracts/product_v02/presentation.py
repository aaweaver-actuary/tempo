"""Read-only screen projections. No UI state, replay, transport or mutation."""
from typing import Literal, Protocol

from pydantic import Field, model_validator

from . import Identifier, Revision, UciMove, UtcTimestamp, Value
from .games import Finding, FirstDepartureSelection, GameSummary
from .learning import EncounterReceipt, LearningReceipt


class GameDetail(GameSummary):
    """Original game history remains separate from its derived findings."""
    starting_fen: Identifier
    moves: tuple[UciMove, ...] = Field(max_length=1000)
    findings: tuple[Finding, ...]
    # Specific subtype first preserves its fields during nested serialization.
    receipts: tuple[EncounterReceipt | LearningReceipt, ...]
    first_departure: FirstDepartureSelection | None = None
    determined_at: UtcTimestamp | None = None
    comparison_revision: Identifier | None = None
    comparison_freshness: Literal["current", "stale", "unknown"] = "unknown"

    @model_validator(mode="after")
    def selection_belongs_to_original_history(self):
        selection = self.first_departure
        if selection is None:
            return self
        # The outer source_revision is provider provenance. Selection source
        # revision belongs to the canonical source owner and may differ.
        if (selection.source.provider, selection.source.game_id, selection.learner_color) != (
                self.provider, self.game_id, self.player_color):
            raise ValueError("first departure must identify this game and learner")
        if selection.ply is not None:
            if selection.ply > len(self.moves) or self.moves[selection.ply - 1] != selection.played_uci:
                raise ValueError("first departure must identify an original played move")
            turn = self.starting_fen.split()[1:2]
            if not turn or turn[0] not in {"w", "b"}:
                raise ValueError("known boundary requires the original starting side to move")
            white_moves = (turn[0] == "w") == (selection.ply % 2 == 1)
            actor = "learner" if white_moves == (self.player_color == "white") else "opponent"
            if selection.actor != actor:
                raise ValueError("first departure actor must match original game history")
        return self


class DecisionComponent(Value):
    decision_id: Identifier
    position_key: Identifier
    response_uci: UciMove
    resulting_position_key: Identifier


class OpponentCue(Value):
    position_key: Identifier
    move_uci: UciMove
    resulting_position_key: Identifier


class Presentation(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    presentation_id: Identifier
    revision: Revision
    repertoire_id: Identifier
    repertoire_revision: Revision
    variant: Identifier
    learner_color: Literal["white", "black"]
    route_id: Identifier
    components: tuple[DecisionComponent, ...] = Field(min_length=1)
    opponent_cues: tuple[OpponentCue, ...]
    clean_streak: int = Field(ge=0, le=2**53 - 1)
    introduced: bool
    lineage: tuple[Identifier, ...]
    recorded_parent_id: Identifier | None


class JoinPlan(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    plan_id: Identifier
    expected_study_revision: Revision
    left: Presentation
    right: Presentation
    connecting_cues: tuple[OpponentCue, ...]
    result: Presentation
    resulting_clean_streak: Literal[0] = 0
    introduction_slots: Literal[0] = 0
    unresolved_obligation_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def same_route_and_fresh_combination(self):
        def scope(presentation):
            return (presentation.repertoire_id, presentation.repertoire_revision, presentation.variant, presentation.learner_color, presentation.route_id)
        if scope(self.left) != scope(self.right) or scope(self.left) != scope(self.result):
            raise ValueError("join participants/result must retain exact route-local scope")
        if self.left.presentation_id == self.right.presentation_id or self.result.presentation_id in {self.left.presentation_id, self.right.presentation_id}:
            raise ValueError("join requires distinct participants and a fresh composite identity")
        if self.result.components != self.left.components + self.right.components or self.result.clean_streak != 0:
            raise ValueError("join preserves existing decisions and starts the combination at zero")
        if not (self.left.introduced and self.right.introduced and self.result.introduced):
            raise ValueError("joining existing approved content cannot introduce content")
        if not {self.left.presentation_id, self.right.presentation_id}.issubset(self.result.lineage):
            raise ValueError("result must retain participant lineage")
        if self.result.opponent_cues != self.left.opponent_cues + self.connecting_cues + self.right.opponent_cues:
            raise ValueError("result must preserve supplied exact opponent cues")
        position = self.left.components[-1].resulting_position_key
        for cue in self.connecting_cues:
            if cue.position_key != position:
                raise ValueError("connecting cue must start at the preceding exact position")
            position = cue.resulting_position_key
        if position != self.right.components[0].position_key:
            raise ValueError("connecting cues must reach the next existing decision exactly")
        return self


class PresentationChangeReceipt(Value):
    owner_receipt_id: Identifier
    plan_id: Identifier
    status: Literal["applied", "stale_revision", "active_attempt", "ineligible"]
    resulting_presentation_id: Identifier | None
    reason: Identifier | None


class PresentationChangePort(Protocol):
    """Study rechecks adjacency, streaks/revisions and no active attempt, then owns
    conservative scheduling, lineage, queue replacement and obligation remapping.
    Only distinct complete clean Study reviews earn streaks. A join is between
    attempts; result starts 0/3, retains obligations/deadlines, costs no new slot.
    """
    def apply_join(self, plan: JoinPlan) -> PresentationChangeReceipt: ...
