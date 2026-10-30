"""Provider acquisition and comparison facts. No provider or Study implementation."""
from typing import Literal, Protocol

from pydantic import Field, model_validator
from tempo_game_review_contracts import CanonicalGame  # Existing canonical identity owner.

from . import Digest, Identifier, Revision, UciMove, UtcTimestamp, Value
from .repertoire import DecisionPresentation, IntroductionGameScope

TimePrecision = Literal["unknown", "day", "hour", "minute", "second", "millisecond"]


class GameWindow(Value):
    start_at: UtcTimestamp
    end_at: UtcTimestamp
    precision: TimePrecision

    @model_validator(mode="after")
    def ordered_window(self):
        from datetime import datetime
        if datetime.fromisoformat(self.start_at.replace("Z", "+00:00")) >= datetime.fromisoformat(self.end_at.replace("Z", "+00:00")):
            raise ValueError("window is half-open [start_at, end_at) and must be nonempty")
        return self


class GameRecord(Value):
    source_game_id: Identifier
    source_revision: Revision
    source_digest: Digest
    pgn: str = Field(min_length=1)
    played_at: UtcTimestamp | None
    time_precision: TimePrecision

    @model_validator(mode="after")
    def explicit_unknown_time(self):
        if (self.played_at is None) != (self.time_precision == "unknown"):
            raise ValueError("unknown source time must remain null with unknown precision")
        return self


class GameRejection(Value):
    source_game_id: Identifier | None
    source_revision: Revision | None
    reason: Identifier


class GamePage(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    provider: Literal["lichess", "chesscom"]
    account: Identifier
    window: GameWindow
    records: tuple[GameRecord, ...]
    rejections: tuple[GameRejection, ...]
    continuation: str | None
    complete: bool
    saturated: bool
    filters: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def honest_completeness(self):
        if self.complete and (self.saturated or self.continuation is not None):
            raise ValueError("saturated or continuing page cannot be complete")
        ids = [item.source_game_id for item in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("a page cannot contain conflicting revisions of one source game")
        return self


class GameAcquisitionPort(Protocol):
    def fetch_page(self, *, account: str, window: GameWindow, continuation: str | None = None) -> GamePage: ...


class GameSummary(Value):
    """Read-only screen projection; canonical normalization keeps its existing owner."""
    provider: Literal["lichess", "chesscom"]
    game_id: Identifier
    source_revision: Revision
    played_at: UtcTimestamp | None
    time_precision: TimePrecision
    player_color: Literal["white", "black"]

    @model_validator(mode="after")
    def explicit_unknown_time(self):
        if (self.played_at is None) != (self.time_precision == "unknown"):
            raise ValueError("unknown source time remains null with unknown precision")
        return self


class ProviderSyncStatus(Value):
    provider: Literal["lichess", "chesscom"]
    status: Literal["idle", "pending", "complete", "failed"]
    operation_id: Identifier | None
    error: Identifier | None

    @model_validator(mode="after")
    def honest_operation_status(self):
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("failed provider status requires an explicit error")
        if self.status != "idle" and self.operation_id is None:
            raise ValueError("non-idle status requires operation identity")
        return self


class RecentGames(Value):
    games: tuple[GameSummary, ...]
    sync: tuple[ProviderSyncStatus, ...]


FindingKind = Literal["confirmed_miss", "opponent_gap", "missing_own_continuation", "known_unintroduced", "known_unavailable", "conflict", "ambiguous", "coverage_end"]


class Finding(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    kind: FindingKind
    provider: Literal["lichess", "chesscom"]
    game_id: Identifier
    source_revision: Revision
    source_digest: Digest
    ply: int = Field(ge=1, le=1000)
    position_key: Identifier
    expected_uci: tuple[UciMove, ...]
    played_uci: UciMove
    repertoire_id: Identifier
    repertoire_revision: Revision
    decision_id: Identifier | None
    presentation_id: Identifier | None
    presentation_revision: Revision | None
    introduced_before_game: bool | None
    membership: Literal["present", "absent", "conflict", "unknown"]
    lapse_eligible: bool
    diagnostic: Identifier | None
    reentry_authority_id: Identifier | None = None

    @model_validator(mode="after")
    def eligibility_requires_proof(self):
        if self.kind == "confirmed_miss":
            if not (self.decision_id and self.presentation_id and self.presentation_revision and self.introduced_before_game is True and self.membership == "present" and self.lapse_eligible):
                raise ValueError("confirmed miss requires an introduced, mapped current presentation")
            if len(self.expected_uci) != 1 or self.played_uci in self.expected_uci:
                raise ValueError("confirmed miss requires one selected own response different from played")
        elif self.lapse_eligible or self.diagnostic is None:
            raise ValueError("non-miss finding requires a diagnostic and cannot authorize a lapse")
        if (self.presentation_id is None) != (self.presentation_revision is None):
            raise ValueError("presentation identity and revision must be supplied together")
        return self


class FirstDepartureScope(Value):
    """One selected relevant repertoire, including unavailable/unknown scope.

    These are comparison facts. The Study owner must fence its actual source,
    configuration and publication authority before accepting an effect.
    """
    repertoire_id: Identifier
    learner_color: Literal["white", "black"] | None
    snapshot_id: Identifier | None
    source_revision: Identifier | None
    revision: Revision | None
    digest: Digest | None
    complete: bool
    available: bool

    @model_validator(mode="after")
    def available_scope_has_owner_identity(self):
        if self.available and any(value is None for value in (
                self.learner_color, self.snapshot_id, self.source_revision, self.revision, self.digest)):
            raise ValueError("available selected scope requires its actual snapshot identity")
        if self.complete and not self.available:
            raise ValueError("unavailable scope cannot claim completeness")
        return self


class FirstDepartureRoute(Value):
    repertoire_id: Identifier
    route_id: Identifier
    route_ply: int = Field(ge=0, le=1000)
    policy_id: Identifier
    source_revision: Identifier
    at_deliberate_end: bool


class FirstDepartureSelection(Value):
    """Pure global first-boundary result, never a producer grant to mutate Study.

    Findings/reentry remain diagnostic. Only an exact learner first departure
    over complete relevant scope can be prepared for authoritative Study intake.
    """
    policy_version: Literal["first_departure_v1"] = "first_departure_v1"
    workspace_id: Identifier
    source: IntroductionGameScope
    learner_color: Literal["white", "black"]
    selected_scope: tuple[FirstDepartureScope, ...] = Field(max_length=10000)
    status: Literal["covered", "own_actionable", "own_unmapped", "opponent_gap", "coverage_end", "missing_own_continuation", "unresolved"]
    ply: int | None = Field(ge=1, le=1000)
    actor: Literal["learner", "opponent"] | None
    position_key: Identifier | None
    played_uci: UciMove | None
    expected_uci: tuple[UciMove, ...]
    decision: DecisionPresentation | None
    surviving_routes: tuple[FirstDepartureRoute, ...]
    diagnostic: Identifier | None

    @model_validator(mode="after")
    def one_honest_global_boundary(self):
        scopes = {item.repertoire_id: item for item in self.selected_scope}
        if len(scopes) != len(self.selected_scope):
            raise ValueError("selected repertoire scope cannot contain duplicate identities")
        if any(item.learner_color not in {None, self.learner_color} for item in self.selected_scope):
            raise ValueError("selected relevant scope must retain the learner color")
        if len(set(self.expected_uci)) != len(self.expected_uci):
            raise ValueError("expected alternatives must be distinct")
        facts = (self.ply, self.actor, self.position_key, self.played_uci)
        if any(value is not None for value in facts) and not all(value is not None for value in facts):
            raise ValueError("a known boundary requires its complete pre-move occurrence")
        if self.status == "covered" and (any(value is not None for value in facts) or self.decision is not None or self.expected_uci):
            raise ValueError("covered played moves cannot claim a departure or target")
        if self.status == "covered" and not self.selected_scope:
            raise ValueError("absent relevant scope must remain unresolved, not covered")
        if self.status not in {"covered", "unresolved"} and self.ply is None:
            raise ValueError("a selected boundary must retain its occurrence")
        if self.status in {"own_actionable", "own_unmapped", "missing_own_continuation"} and self.actor != "learner":
            raise ValueError("learner boundary must identify the learner actor")
        if self.status == "opponent_gap" and self.actor != "opponent":
            raise ValueError("opponent gap cannot authorize a learner effect")
        if self.status in {"own_actionable", "own_unmapped", "opponent_gap"} and self.played_uci in self.expected_uci:
            raise ValueError("a departure cannot be a supported expected alternative")
        routes = {(item.repertoire_id, item.route_id, item.route_ply, item.policy_id) for item in self.surviving_routes}
        if len(routes) != len(self.surviving_routes):
            raise ValueError("surviving route occurrences must be distinct")
        if any(item.repertoire_id not in scopes or item.source_revision != scopes[item.repertoire_id].source_revision
               for item in self.surviving_routes):
            raise ValueError("route occurrence must bind the retained selected source revision")
        if self.status != "own_actionable" and self.decision is not None:
            raise ValueError("non-actionable boundary cannot claim an actionable decision")
        if self.status in {"own_unmapped", "missing_own_continuation", "unresolved"} and self.diagnostic is None:
            raise ValueError("unresolved/non-actionable own boundary needs a specific diagnostic")
        if self.status == "own_actionable":
            decision = self.decision
            if (decision is None or not decision.available or decision.route_id is None
                    or len(self.expected_uci) != 1 or self.played_uci in self.expected_uci
                    or self.expected_uci != (decision.selected_response_uci,)
                    or self.position_key != decision.position_key):
                raise ValueError("actionable first departure requires one exact available pre-move target")
            if not self.selected_scope or not all(item.complete and item.available for item in self.selected_scope):
                raise ValueError("incomplete selected scope cannot authorize a Study consequence")
            scope = scopes.get(decision.repertoire_id)
            if scope is None or decision.repertoire_revision != scope.revision:
                raise ValueError("actionable target must bind its selected repertoire revision")
            if (not self.surviving_routes or any(item.at_deliberate_end for item in self.surviving_routes)
                    or not any((item.repertoire_id, item.route_id, item.route_ply) ==
                               (decision.repertoire_id, decision.route_id, decision.route_ply)
                               for item in self.surviving_routes)):
                raise ValueError("actionable target needs its surviving authored occurrence without an ended competitor")
        return self


__all__ = ["CanonicalGame", "GameWindow", "GameRecord", "GameRejection", "GamePage", "GameAcquisitionPort", "Finding", "FindingKind", "GameSummary", "ProviderSyncStatus", "RecentGames", "FirstDepartureScope", "FirstDepartureRoute", "FirstDepartureSelection"]
