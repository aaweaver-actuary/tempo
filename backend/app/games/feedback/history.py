"""Bounded retained-history preparation, independent of providers and Study SQL."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field, model_validator

from app.contracts.product_v02 import Identifier, UtcTimestamp, Value
from app.contracts.product_v02.games import Finding, GameWindow

if TYPE_CHECKING:
    from app.contracts.product_v02.learning import GameEncounter

MAX_MANIFEST_COMMANDS = 256
MAX_MANIFEST_BYTES = 1024 * 1024


def history_task_identity(workspace_id: str, run_id: str) -> str:
    return hashlib.sha256(json.dumps([workspace_id, run_id], separators=(",", ":")).encode()).hexdigest()


class HistorySelection(Value):
    workspace_id: Identifier
    provider: Literal["lichess", "chesscom"]
    account: Identifier
    window: GameWindow
    repertoire_ids: tuple[Identifier, ...] = Field(min_length=1)
    speeds: tuple[Literal["bullet", "blitz", "rapid", "classical"], ...] = Field(min_length=1, max_length=4)
    rated: bool | None
    maximum_games: int = Field(default=25, ge=1, le=100)
    maximum_positions: int = Field(default=10000, ge=1, le=10000)
    after_game_id: Identifier | None = None
    start_ply: int = Field(default=0, ge=0, le=1000)
    maximum_plies: int = Field(default=16, ge=1, le=16)

    @model_validator(mode="after")
    def explicit_scope(self):
        if self.account != self.account.strip() or len(set(self.repertoire_ids)) != len(self.repertoire_ids):
            raise ValueError("explicit_unique_history_scope_required")
        return self


@dataclass(frozen=True)
class RetainedHistoryGame:
    game_id: str
    excluded: bool
    positions: int | None
    diagnostic: str | None = None


@dataclass(frozen=True)
class RetainedHistoryPage:
    games: tuple[RetainedHistoryGame, ...]
    more_available: bool


@dataclass(frozen=True)
class HistoryComparison:
    positions: int
    findings: tuple[Finding, ...]
    encounters: tuple[GameEncounter, ...] = ()
    matches_complete: bool = False
    unresolved_positions: int = 0
    diagnostics: tuple[str, ...] = ()
    remaining_positions: int = 0


class HistoryReader(Protocol):
    def page(self, selection: HistorySelection) -> RetainedHistoryPage: ...


class HistoryComparer(Protocol):
    projection_available: bool
    def compare(self, selection: HistorySelection, game_id: str, repertoire_id: str,
                detected_at: str) -> HistoryComparison: ...


class HistoryDiagnostic(Value):
    game_id: Identifier
    repertoire_id: Identifier | None
    reason: Identifier


class _RunIdentity(Value):
    run_id: Identifier
    detected_at: UtcTimestamp
    policy_identity: Identifier | None


class HistoryPreview(Value):
    read_only: Literal[True] = True
    selected_games: int
    compared_games: int
    positions_compared: int
    matches: int
    differing_responses: int
    eligible_lapses: int
    unintroduced_encounters: int
    excluded_games: int
    unknown_authority_or_mapping_games: int
    unreported_positions: int
    matches_are_lower_bound: bool
    finding_counts: tuple[tuple[str, int], ...]
    more_available: bool
    next_game_id: str | None
    execution_ready: bool
    diagnostics: tuple[HistoryDiagnostic, ...]
    unresolved_positions: int = 0
    remaining_positions: int = 0
    next_start_ply: int | None = None


@dataclass(frozen=True)
class PreparedHistoryPlan:
    run_id: str
    detected_at: str
    policy_identity: str | None
    selection: HistorySelection
    preview: HistoryPreview
    encounters: tuple[GameEncounter, ...]
    version: int = 2

    def __post_init__(self):
        _RunIdentity(run_id=self.run_id, detected_at=self.detected_at, policy_identity=self.policy_identity)
        object.__setattr__(self, "encounters", tuple(self.encounters))

    @property
    def task_identity(self) -> str:
        return history_task_identity(self.selection.workspace_id, self.run_id)

    def payload(self) -> dict:
        selection = self.selection.model_dump(mode="json")
        preview = self.preview.model_dump(mode="json")
        if self.version == 1:
            # Existing durable manifests retain their original byte identity.
            for key in ("start_ply", "maximum_plies"):
                selection.pop(key)
            for key in ("unresolved_positions", "remaining_positions", "next_start_ply"):
                preview.pop(key)
        return dict(version=self.version, run_id=self.run_id, detected_at=self.detected_at,
            policy_identity=self.policy_identity, selection=selection, preview=preview,
            encounters=[command.model_dump(mode="json") for command in self.encounters])

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.payload(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()

    @classmethod
    def from_payload(cls, payload):
        from app.contracts.product_v02.learning import GameEncounter
        if payload.get("version") not in {1, 2}:
            raise ValueError("unsupported_retained_history_plan")
        return cls(payload["run_id"], payload["detected_at"], payload["policy_identity"],
            HistorySelection.model_validate_json(json.dumps(payload["selection"])),
            HistoryPreview.model_validate_json(json.dumps(payload["preview"])),
            tuple(GameEncounter.model_validate_json(json.dumps(item)) for item in payload["encounters"]),
            payload["version"])

    def require_execution(self, policy_identity: str):
        if not self.preview.execution_ready or self.policy_identity is None:
            raise ValueError("retained_encounter_projection_and_owner_policy_required")
        if self.policy_identity != policy_identity:
            raise ValueError("retained_history_owner_policy_changed")
        if len(self.encounters) > MAX_MANIFEST_COMMANDS:
            raise ValueError("retained_history_manifest_exceeds_bound")
        if len(json.dumps(self.payload(), separators=(",", ":")).encode()) > MAX_MANIFEST_BYTES:
            raise ValueError("retained_history_manifest_exceeds_bound")


def preview_history(selection: HistorySelection, *, reader: HistoryReader, comparer: HistoryComparer,
                    run_id: str, detected_at: str, policy_identity: str | None = None) -> PreparedHistoryPlan:
    """Known findings are facts; an absent finding is never called correct.

    positions_compared counts played-position/repertoire comparison slots. The
    existing comparer does not report every correct introduced occurrence, so its
    matches are explicitly a lower bound and all non-reportable slots are shown.
    """
    _RunIdentity(run_id=run_id, detected_at=detected_at, policy_identity=policy_identity)
    page = reader.page(selection)
    if len(page.games) > selection.maximum_games or len({item.game_id for item in page.games}) != len(page.games):
        raise ValueError("retained_history_reader_exceeded_bound_or_repeated_game")
    compared, unknown, diagnostics, commands = set(), set(), [], []
    counts = Counter()
    positions = matches = differing = eligible = unintroduced = excluded = unreported = 0
    unresolved_positions = remaining_positions = 0
    lower_bound, ready = False, comparer.projection_available and policy_identity is not None
    for game in page.games:
        if game.excluded:
            excluded += 1
            continue
        if game.diagnostic is not None or game.positions is None:
            unknown.add(game.game_id)
            diagnostics.append(HistoryDiagnostic(game_id=game.game_id, repertoire_id=None,
                                                  reason=game.diagnostic or "retained_history_unavailable"))
            continue
        for repertoire_id in selection.repertoire_ids:
            bounded_positions = (comparer.position_bound(selection, game.positions)
                if callable(getattr(comparer, "position_bound", None)) else game.positions)
            if positions + bounded_positions > selection.maximum_positions:
                unknown.add(game.game_id)
                ready = False
                diagnostics.append(HistoryDiagnostic(game_id=game.game_id, repertoire_id=repertoire_id,
                                                      reason="history_position_budget_exhausted"))
                continue
            try:
                result = comparer.compare(selection, game.game_id, repertoire_id, detected_at)
            except ValueError as error:
                unknown.add(game.game_id)
                reason = str(error)
                if not reason.replace("_", "").isalnum() or len(reason) > 120:
                    reason = "retained_authority_or_mapping_unavailable"
                diagnostics.append(HistoryDiagnostic(game_id=game.game_id, repertoire_id=repertoire_id, reason=reason))
                continue
            if result.positions < 0 or positions + result.positions > selection.maximum_positions:
                raise ValueError("retained_comparison_exceeded_position_bound")
            compared.add(game.game_id)
            positions += result.positions
            unresolved_positions += result.unresolved_positions
            remaining_positions += result.remaining_positions
            if result.unresolved_positions:
                unknown.add(game.game_id)
            for reason in sorted(set(result.diagnostics)):
                if not reason.replace("_", "").isalnum() or len(reason) > 120:
                    reason = "retained_authority_or_mapping_unavailable"
                diagnostics.append(HistoryDiagnostic(game_id=game.game_id,
                    repertoire_id=repertoire_id, reason=reason))
            lower_bound = lower_bound or not result.matches_complete
            observed = set()
            for finding in result.findings:
                if (finding.provider, finding.game_id, finding.repertoire_id) != (selection.provider, game.game_id, repertoire_id):
                    raise ValueError("retained_comparison_scope_mismatch")
                identity = finding.ply, finding.decision_id
                if identity in observed:
                    raise ValueError("repeated_retained_comparison_occurrence")
                observed.add(identity)
                counts[finding.kind] += 1
                if (finding.membership == "present" and len(finding.expected_uci) == 1
                        and finding.presentation_id is not None):
                    matched = finding.played_uci in finding.expected_uci
                    matches += int(matched)
                    differing += int(not matched)
                eligible += int(finding.lapse_eligible)
                unintroduced += int(finding.kind == "known_unintroduced")
                if finding.kind in {"known_unavailable", "ambiguous", "conflict"}:
                    unknown.add(game.game_id)
            unreported += max(0, result.positions - len({item.ply for item in result.findings}))
            commands.extend(result.encounters)
            if len(commands) > MAX_MANIFEST_COMMANDS:
                ready = False
                commands = commands[:MAX_MANIFEST_COMMANDS]
                diagnostics.append(HistoryDiagnostic(game_id=game.game_id, repertoire_id=repertoire_id,
                                                      reason="retained_encounter_manifest_exceeds_bound"))
    occurrences = {}
    for command in commands:
        identity = command.key.provider, command.key.game_id, command.key.occurrence_ply
        scopes = occurrences.setdefault(identity, set())
        scopes.add((command.key.decision_id, command.decision.repertoire_id))
        if len(scopes) > 1:
            ready = False
            diagnostics.append(HistoryDiagnostic(game_id=command.key.game_id,
                repertoire_id=command.decision.repertoire_id, reason="multiple_retained_decisions_at_occurrence"))
    preview = HistoryPreview(selected_games=len(page.games), compared_games=len(compared),
        positions_compared=positions, matches=matches, differing_responses=differing, eligible_lapses=eligible,
        unintroduced_encounters=unintroduced, excluded_games=excluded,
        unknown_authority_or_mapping_games=len(unknown), unreported_positions=unreported,
        matches_are_lower_bound=lower_bound or bool(unknown) or bool(unreported) or bool(excluded),
        finding_counts=tuple(sorted(counts.items())),
        more_available=page.more_available, next_game_id=page.games[-1].game_id if page.more_available and page.games else None,
        execution_ready=ready, diagnostics=tuple(diagnostics),
        unresolved_positions=unresolved_positions, remaining_positions=remaining_positions,
        next_start_ply=selection.start_ply+selection.maximum_plies if remaining_positions else None)
    return PreparedHistoryPlan(run_id, detected_at, policy_identity, selection, preview, tuple(commands))
