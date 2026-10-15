"""Typed evidence for game-derived defensive threats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Color = Literal["white", "black"]
LineOrigin = Literal["played", "engine"]


@dataclass(frozen=True)
class GameSnapshot:
    game_id: str
    analysis_version: int
    start_fen: str
    moves_uci: tuple[str, ...]
    learner_color: Color


@dataclass(frozen=True)
class SourceLine:
    origin: LineOrigin
    start_ply: int
    moves_uci: tuple[str, ...]


@dataclass(frozen=True)
class ForkTarget:
    square: str
    piece: Literal["king", "rook", "queen"]


@dataclass(frozen=True)
class ForkGeometry:
    attacker_color: Color
    knight_from: str
    knight_to: str
    king: ForkTarget
    major: ForkTarget
    other_majors: tuple[ForkTarget, ...] = ()


@dataclass(frozen=True)
class ThreatSeed:
    seed_id: str
    game_id: str
    analysis_version: int
    source_line: SourceLine
    fork_line_index: int
    geometry: ForkGeometry

    @property
    def fork_ply(self) -> int:
        return self.source_line.start_ply + self.fork_line_index


@dataclass(frozen=True)
class KnightHop:
    ply: int
    from_square: str
    to_square: str
    origin: LineOrigin


@dataclass(frozen=True)
class PositionContext:
    start_fen: str
    prefix_uci: tuple[str, ...]
    learner_color: Color


@dataclass(frozen=True)
class ExerciseAnchor:
    seed_id: str
    player_ply: int
    position: PositionContext
    historical_move_uci: str
    decisions_before_event: int


@dataclass(frozen=True)
class ThreatPolicy:
    max_prior_decisions: int = 3
    max_knight_hops: int = 3
    minimum_loss_cp: int = 100
    correct_tolerance_cp: int = 30
    incorrect_loss_cp: int = 100
    minimum_depth: int = 14
