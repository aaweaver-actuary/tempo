"""Pure command registration contracts; execution and transactions stay with the gateway."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

Transaction = TypeVar("Transaction")
Original = TypeVar("Original")
Prepared = TypeVar("Prepared")
Result = TypeVar("Result")




@dataclass(frozen=True)
class CommandDescriptor(Generic[Transaction, Original, Prepared, Result]):
    """Correlate one command's original decoding, preparation, and application."""

    name: str
    decode: Callable[[dict[str, Any]], Original]
    prepare: Callable[[Original], Prepared]
    apply: Callable[[Transaction, Prepared], Result]
    after_commit: Callable[[Result], None] | None = None


class CommandRegistry:
    """Own one heterogeneous collection; erasure is limited to this boundary."""

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.preparers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._after_commit: dict[str, Callable[[Any], None]] = {}

    def register(
        self, descriptor: CommandDescriptor[Transaction, Original, Prepared, Result]
    ) -> None:
        if descriptor.name in self.handlers:
            raise ValueError(f"Duplicate command: {descriptor.name}")

        def prepare(payload: dict[str, Any]) -> Prepared:
            return descriptor.prepare(descriptor.decode(payload))

        def apply(transaction: Any, prepared: Any) -> Result:
            return descriptor.apply(
                cast(Transaction, transaction), cast(Prepared, prepared)
            )

        self.handlers[descriptor.name] = apply
        self.preparers[descriptor.name] = prepare
        if descriptor.after_commit is not None:
            callback = descriptor.after_commit

            def after_commit(result: Any) -> None:
                callback(cast(Result, result))

            self._after_commit[descriptor.name] = after_commit
        else:
            self._after_commit.pop(descriptor.name, None)

    def clear_after_commit(self, name: str) -> None:
        self._after_commit.pop(name, None)

    def after_commit(self, name: str, result: Any) -> None:
        callback = self._after_commit.get(name)
        if callback is not None:
            callback(result)


# Immutable historical names: these envelopes may still be in the broker or
# receipt table. Unknown and mixed commands are deliberately not classified.
RETIRED_COMMANDS = frozenset(
    {
        "repertoire.canonical_prefix.preview",
        "repertoire.canonical_prefix.save",
        "repertoire.prefix_transition.apply",
        "defense.recognition.submit",
        "defense.attempt.submit",
        "defense.candidate.change",
        "endgames.template.create",
        "endgames.attempt.create",
        "game_findings.curate",
        "game_findings.decide",
        "game_findings.card.save",
        "activity.progress",
        "defensive.audit",
        "defensive.backfill",
        "coverage.refresh.request",
        "discovery.accept",
        "coverage.maia.claim",
        "coverage.maia.heartbeat",
        "coverage.maia.release",
        "coverage.maia.failure",
        "coverage.maia.submit",
        "threat.analysis.claim",
        "threat.analysis.report",
        "threat.analysis.failure",
        "threat.analysis.release",
        "threat.analysis.retry",
        "games.defensive_threats.refresh",
        "games.analysis.claim",
        "games.analysis.position.claim",
        "games.analysis.position.report",
        "games.analysis.position.release",
        "games.analysis.failure",
        "games.analysis.heartbeat",
        "games.analysis.release",
        "games.analysis.retry",
        "games.analysis.repair_timeout",
        "games.analysis.repair_provenance",
        "games.analysis.finalize.admit",
        "games.analysis.manual.admit",
        "opening.segmentation.refresh",
        "opening.segmentation.preference",
        "studies.chapters.create",
        "studies.chapters.reorder",
        "studies.chapters.rename",
    }
)
RETIRED_TASK_KINDS = frozenset(
    {
        "prefix_transition_application",
        "canonical_prefix_preview",
        "opening_segmentation",
        "game_derivation_compare",
        "game_derivation_events",
        "game_derivation_features",
        "game_derivation_findings",
        "game_derivation_misses",
        "game_derivation_positions",
        "game_derivation_priorities",
        "repertoire_game_refresh",
        "defensive_rubric_audit",
        "defensive_admission",
        "defensive_threat_report_audit",
        "defensive_threat_scan",
        "defensive_threat_backfill",
        "defensive_threat_validate",
        "next_opponent_profile",
        "discovery_recommendation",
        "discovery_admission",
        "coverage_seed",
        "coverage_explorer",
        "game_analysis_publish",
        "game_analysis_followup",
    }
)


def is_retired_command(name: str) -> bool:
    return name in RETIRED_COMMANDS
