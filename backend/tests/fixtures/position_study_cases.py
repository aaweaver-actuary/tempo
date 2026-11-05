"""Synthetic, legal position-owned Study cases; never seed runtime or user history.

Routes contain exact UCI inputs. Derived FEN and normalized identity values are
frozen at module construction using Tempo's existing opening_position_key.
Fixture labels below are local test handles, never persistent product IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import chess
from tempo_evidence_math import opening_position_key


@dataclass(frozen=True)
class Route:
    label: str
    repertoire: str
    moves: tuple[str, ...]
    start_fen: str = chess.STARTING_FEN
    learner_color: str = "white"


@dataclass(frozen=True)
class Frame:
    route: str
    ply: int
    fen: str
    position: str
    side_to_move: str


@dataclass(frozen=True)
class Decision:
    route: str
    ply: int
    repertoire: str
    position: str
    expected_uci: str
    policy_version: str = "synthetic-policy-v1"
    variant: str = "standard"


@dataclass(frozen=True)
class EvidenceCase:
    label: str
    position_ref: str
    due_at_issuance: bool
    responses: tuple[str, ...]
    expected_grades: tuple[str, ...]
    saved: tuple[bool, ...]
    terminal_complete: tuple[bool, ...]
    duplicate_receipt: bool = False
    note: str = ""


def _route(
    label: str, repertoire: str, san: str, start_fen: str = chess.STARTING_FEN
) -> Route:
    board = chess.Board(start_fen)
    moves = []
    for token in san.split():
        moves.append(board.parse_san(token).uci())
        board.push_uci(moves[-1])
    return Route(label, repertoire, tuple(moves), start_fen=start_fen)


ROUTES = (
    # Italian shared trunk, with both legal opponent orders after 3.Bc4.
    _route("italian-knight-attack", "rep-italian", "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5"),
    _route("italian-knight-d6", "rep-italian", "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d6 d3"),
    _route("italian-giuoco", "rep-italian", "e4 e5 Nf3 Nc6 Bc4 Bc5 c3"),
    _route("italian-shared-trunk-other-order", "rep-italian", "e4 e5 Nf3 Nc6 Bc4"),
    # London move orders transpose to the same exact position after 4...e6.
    _route("london-order-a", "rep-london", "d4 d5 Bf4 Nf6 e3 e6 Nf3"),
    _route("london-order-b", "rep-london", "d4 Nf6 Bf4 d5 e3 e6 Nf3"),
    # Shared position in two repertoires is distinct scheduling scope.
    _route("same-position-other-repertoire", "rep-other", "e4 e5 Nf3 Nc6 Bc4"),
    # Same piece placement and turn; one route retains legal en-passant, the
    # other returns both knights after that right expires.
    _route("en-passant-available", "rep-ep", "e4 a6 e5 d5"),
    _route("en-passant-expired", "rep-ep", "e4 a6 e5 d5 Nf3 Nf6 Ng1 Ng8"),
    # King returns to e1 after moving; board placement matches but K-side right is gone.
    _route(
        "castling-rights-present",
        "rep-castle",
        "",
        start_fen="4k2r/8/8/8/8/8/8/4K2R w Kk - 0 1",
    ),
    _route(
        "castling-rights-lost",
        "rep-castle",
        "Kf1 Kf7 Ke1 Ke8",
        start_fen="4k2r/8/8/8/8/8/8/4K2R w Kk - 0 1",
    ),
    # Same position after reversible knight moves, but move counters differ.
    _route("clock-short", "rep-clocks", "Nf3 Nf6 Ng1 Ng8"),
    _route("clock-long", "rep-clocks", "Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8"),
    # A true selected-response conflict at the initial position.
    _route("conflict-king-pawn", "rep-conflict", "e4 e5"),
    _route("conflict-queen-pawn", "rep-conflict", "d4 d5"),
)


def _replay(route: Route) -> tuple[Frame, ...]:
    board = chess.Board(route.start_fen)
    frames = [
        Frame(
            route.label,
            0,
            board.fen(en_passant="fen"),
            opening_position_key(board.fen()),
            "white" if board.turn else "black",
        )
    ]
    for ply, uci in enumerate(route.moves, 1):
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"illegal fixture move {route.label}:{ply}:{uci}")
        board.push(move)
        fen = board.fen(en_passant="fen")
        frames.append(
            Frame(
                route.label,
                ply,
                fen,
                opening_position_key(fen),
                "white" if board.turn else "black",
            )
        )
    return tuple(frames)


FRAMES: Mapping[str, tuple[Frame, ...]] = MappingProxyType(
    {r.label: _replay(r) for r in ROUTES}
)
ROUTE_BY_LABEL: Mapping[str, Route] = MappingProxyType({r.label: r for r in ROUTES})

# Decision expectations are fixture-local and explicitly response-versioned;
# their scheduling scope remains (repertoire, standard, normalized position).
DECISIONS = tuple(
    Decision(r.label, frame.ply, r.repertoire, frame.position, r.moves[frame.ply])
    for r in ROUTES
    for frame in FRAMES[r.label]
    if frame.side_to_move == r.learner_color and frame.ply < len(r.moves)
)

EXPECTED_DECISION_COUNTS = MappingProxyType(
    {
        "italian-knight-attack": 5,
        "italian-knight-d6": 5,
        "italian-giuoco": 4,
        "italian-shared-trunk-other-order": 3,
        "london-order-a": 4,
        "london-order-b": 4,
        "same-position-other-repertoire": 3,
        "en-passant-available": 2,
        "en-passant-expired": 4,
        "castling-rights-present": 0,
        "castling-rights-lost": 2,
        "clock-short": 2,
        "clock-long": 4,
        "conflict-king-pawn": 1,
        "conflict-queen-pawn": 1,
    }
)
STABLE_DECISIONS = tuple(
    sorted(
        DECISIONS,
        key=lambda item: (
            item.repertoire,
            item.position,
            item.expected_uci,
            item.route,
            item.ply,
        ),
    )
)


def _frame(route: str, ply: int) -> Frame:
    return FRAMES[route][ply]


RELATIONSHIPS = MappingProxyType(
    {
        "italian-shared-3bc4": (
            _frame("italian-knight-attack", 5),
            _frame("italian-giuoco", 5),
            "equal",
        ),
        "italian-opponent-order": (
            _frame("italian-knight-attack", 6),
            _frame("italian-giuoco", 6),
            "different",
        ),
        "london-transposition": (
            _frame("london-order-a", 7),
            _frame("london-order-b", 7),
            "equal",
        ),
        "repertoire-scope": (
            _frame("italian-shared-trunk-other-order", 5),
            _frame("same-position-other-repertoire", 5),
            "position-equal-scope-different",
        ),
        "legal-en-passant": (
            _frame("en-passant-available", 4),
            _frame("en-passant-expired", 8),
            "different",
        ),
        "castling-rights": (
            _frame("castling-rights-present", 0),
            _frame("castling-rights-lost", 4),
            "different",
        ),
        "clocks-ignored": (_frame("clock-short", 4), _frame("clock-long", 8), "equal"),
        "initial-scope": (
            _frame("italian-shared-trunk-other-order", 0),
            _frame("same-position-other-repertoire", 0),
            "position-equal-scope-different",
        ),
    }
)

EVIDENCE_CASES = (
    EvidenceCase(
        "due-first-clean",
        "initial-position",
        True,
        ("e2e4",),
        ("good",),
        (True,),
        (True,),
        note="First actual clean response on a due issued position.",
    ),
    EvidenceCase(
        "nondue-clean-observation",
        "initial-position",
        False,
        ("e2e4",),
        ("observe",),
        (True,),
        (True,),
        note="Nondue correctness is retained as observation, without positive grade.",
    ),
    EvidenceCase(
        "partial-first-response-error",
        "initial-position",
        True,
        ("d2d4",),
        ("again",),
        (True,),
        (False,),
        note="A durable first wrong response remains meaningful before terminal completion.",
    ),
    EvidenceCase(
        "good-then-fresh-wrong",
        "initial-position",
        True,
        ("e2e4", "d2d4"),
        ("good", "again"),
        (True, True),
        (True, False),
        note="A later genuine mistake is not erased by earlier same-day Good.",
    ),
    EvidenceCase(
        "duplicate-offline-receipt",
        "initial-position",
        True,
        ("d2d4", "d2d4"),
        ("again", "duplicate-no-grade"),
        (True, True),
        (False, False),
        True,
        "Same immutable first-response receipt replays without another penalty.",
    ),
)

POSITION_REFS = MappingProxyType(
    {"initial-position": _frame("italian-shared-trunk-other-order", 0)}
)

# A source conflict is deliberately represented as two legal selected answers
# at one exact repertoire-position. Invalid source is a separate diagnostic case.
SOURCE_CASES = MappingProxyType(
    {
        "selected-response-conflict": ("conflict-king-pawn", "conflict-queen-pawn"),
        "invalid-source": ("fixture-local-invalid-uci", "e2e5", "illegal_move"),
    }
)

__all__ = [
    "ROUTES",
    "FRAMES",
    "ROUTE_BY_LABEL",
    "DECISIONS",
    "EXPECTED_DECISION_COUNTS",
    "STABLE_DECISIONS",
    "RELATIONSHIPS",
    "EVIDENCE_CASES",
    "SOURCE_CASES",
    "POSITION_REFS",
    "Route",
    "Frame",
    "Decision",
    "EvidenceCase",
]
