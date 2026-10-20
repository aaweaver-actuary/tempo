"""Ordered route traversal extracted from Game Evidence comparison.compare_game.

Classification, historical eligibility and physical-card mapping stay with callers.
"""
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class TraversalRoute:
    position_keys: tuple[str, ...]
    moves: tuple[str, ...]
    reentry_plies: tuple[int, ...] = ()

    def __post_init__(self):
        if len(self.position_keys) != len(self.moves) + 1:
            raise ValueError("route_position_count_mismatch")
        # Legacy GE permits a binding at an endpoint. Slim authored adapters can
        # impose their stricter move-anchor contract before constructing routes.
        if any(not 0 <= ply < len(self.position_keys) for ply in self.reentry_plies):
            raise ValueError("reentry_outside_route")


class RouteFrontier:
    """One caller-owned traversal; only explicit anchors can restore departed routes."""

    def __init__(self, routes: Iterable[TraversalRoute], starting_key: str):
        self.routes = tuple(routes)
        self.active = {(index, 0) for index, route in enumerate(self.routes)
                       if route.position_keys[0] == starting_key}
        anchors = {}
        for index, route in enumerate(self.routes):
            for ply in route.reentry_plies:
                anchors.setdefault(route.position_keys[ply], set()).add((index, ply))
        self._reentry_anchors = anchors

    def candidates(self, position_key: str) -> set[tuple[int, int]]:
        candidates = self.active | self._reentry_anchors.get(position_key, set())
        return {(index, ply) for index, ply in candidates
                if self.routes[index].position_keys[ply] == position_key}

    def advance(self, candidates: Iterable[tuple[int, int]], actual_move: str) -> None:
        self.active = {(index, ply + 1) for index, ply in candidates
                       if ply < len(self.routes[index].moves)
                       and self.routes[index].moves[ply] == actual_move}
