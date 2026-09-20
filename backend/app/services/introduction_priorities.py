"""Evidence-backed admission ordering for new opening cards.

This module deliberately separates *line prevalence* from the moves a card
happens to contain.  A shared opening trunk is a prerequisite for its
descendant lines, not three independent reasons to schedule every London
card before its later branches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3

import chess

from .repertoire_comparison import canonical_fen
from .repertoire_coverage import blend_probabilities


SCORING_VERSION = 1
PERSONAL_PRIOR_GAMES = 20.0
RECENCY_HALF_LIFE_DAYS = 90.0
FRONTIER_WEIGHTS = (1.0, 0.25, 0.0625)
SUPPORTED_PERSONAL_SPEEDS = ("blitz", "rapid", "classical")


@dataclass(frozen=True)
class IntendedLine:
    """A line intended to be learned, including its unique identifier, starting position, trained color, moves, and route signature."""

    identifier: str
    start_fen: str
    trained_color: str
    moves: tuple[str, ...]
    signature: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CardRoute:
    """The route a card takes through a line, including its end ply and decisions."""

    card_id: str
    end_ply: int
    decisions: tuple[dict, ...]


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _route_signature(
    start_fen: str, moves: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    """Return the route signature for a sequence of moves from a starting position.

    The route signature is a tuple of (canonical FEN, move UCI) pairs for each move in the sequence.
    If any move is illegal, an empty tuple is returned.
    """

    board = chess.Board(start_fen)
    signature: list[tuple[str, str]] = []
    for move_uci in moves:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            return tuple()
        signature.append((canonical_fen(board.fen()), move_uci))
        board.push(move)
    return tuple(signature)


def _maximal_intended_lines(rows: list[sqlite3.Row]) -> list[IntendedLine]:
    candidates: list[
        tuple[str, str, str, tuple[str, ...], tuple[tuple[str, str], ...]]
    ] = []
    for row in rows:
        try:
            moves = tuple(json.loads(row["moves_json"]))
            signature = _route_signature(row["start_fen"], moves)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if moves and signature:
            candidates.append(
                (row["id"], row["start_fen"], row["trained_color"], moves, signature)
            )

    maximal: list[
        tuple[str, str, str, tuple[str, ...], tuple[tuple[str, str], ...]]
    ] = []
    for candidate in candidates:
        _, _, color, _, signature = candidate
        is_strict_prefix = any(
            other[2] == color
            and len(signature) < len(other[4])
            and other[4][: len(signature)] == signature
            for other in candidates
        )
        if not is_strict_prefix:
            maximal.append(candidate)

    unique: dict[tuple[tuple[str, str], ...], IntendedLine] = {}
    for identifier, start_fen, color, moves, signature in maximal:
        unique.setdefault(
            signature,
            IntendedLine(identifier, start_fen, color, moves, signature),
        )
    return list(unique.values())


def _line_card_route(
    card: sqlite3.Row,
    line: IntendedLine,
    edge_probabilities: dict[tuple[str, str], tuple[float, dict]],
) -> CardRoute | None:
    """Return how a card advances a complete line, including response cards."""

    try:
        card_moves = tuple(json.loads(card["moves_json"]))
        card_start = canonical_fen(card["start_fen"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not card_moves:
        return None

    board = chess.Board(line.start_fen)
    trained_color = chess.WHITE if line.trained_color == "white" else chess.BLACK
    positions = [canonical_fen(board.fen())]
    for move_uci in line.moves:
        board.push_uci(move_uci)
        positions.append(canonical_fen(board.fen()))
    for start_ply, position in enumerate(positions[:-1]):
        if position != card_start:
            continue
        end_ply = start_ply + len(card_moves)
        if end_ply > len(line.moves) or line.moves[start_ply:end_ply] != card_moves:
            continue
        route_board = chess.Board(line.start_fen)
        reach_probability = 1.0
        decisions: list[dict] = []
        for ply, move_uci in enumerate(line.moves[:end_ply]):
            current_key = canonical_fen(route_board.fen())
            if route_board.turn == trained_color:
                decisions.append(
                    {
                        "fen_key": current_key,
                        "move_uci": move_uci,
                        "reach_probability": reach_probability,
                        "ply": ply,
                    }
                )
            elif (current_key, move_uci) in edge_probabilities:
                reach_probability *= edge_probabilities[(current_key, move_uci)][0]
            route_board.push_uci(move_uci)
        return CardRoute(card["id"], end_ply, tuple(decisions))
    return None


def _repertoire_reply_moves(lines: list[IntendedLine]) -> dict[str, set[str]]:
    replies: dict[str, set[str]] = {}
    for line in lines:
        board = chess.Board(line.start_fen)
        trained_color = chess.WHITE if line.trained_color == "white" else chess.BLACK
        for move_uci in line.moves:
            key = canonical_fen(board.fen())
            if board.turn != trained_color:
                replies.setdefault(key, set()).add(move_uci)
            board.push_uci(move_uci)
    return replies


def _coverage_evidence(
    database: sqlite3.Connection, repertoire_id: str
) -> dict[str, dict]:
    run = database.execute(
        """SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (repertoire_id,),
    ).fetchone()
    if not run:
        return {}
    nodes = database.execute(
        """SELECT * FROM repertoire_coverage_nodes WHERE run_id=?""", (run["id"],)
    ).fetchall()
    evidence: dict[str, dict] = {}
    for node in nodes:
        candidates = database.execute(
            """SELECT move_uci,explorer_probability,maia_probability
               FROM repertoire_coverage_candidates WHERE node_id=?""",
            (node["id"],),
        ).fetchall()
        evidence[node["fen_key"]] = {
            "explorer_status": node["explorer_status"],
            "explorer_games": int(node["explorer_games"] or 0),
            "maia_status": node["maia_status"],
            "moves": {row["move_uci"]: dict(row) for row in candidates},
        }
    return evidence


def _personal_evidence(
    database: sqlite3.Connection, fen_keys: set[str], trained_color: str
) -> dict[str, dict[str, float]]:
    if not fen_keys:
        return {}
    placeholders = ",".join("?" for _ in fen_keys)
    rows = database.execute(
        f"""SELECT p.fen_key,p.move_uci,g.played_at
            FROM game_position_occurrences p JOIN imported_games g ON g.id=p.game_id
            WHERE p.fen_key IN ({placeholders}) AND p.move_uci IS NOT NULL
              AND g.color=? AND g.adaptive_excluded=0
              AND g.speed IN ({",".join("?" for _ in SUPPORTED_PERSONAL_SPEEDS)})""",
        (*sorted(fen_keys), trained_color, *SUPPORTED_PERSONAL_SPEEDS),
    ).fetchall()
    now = datetime.now(timezone.utc)
    evidence: dict[str, dict[str, float]] = {}
    for row in rows:
        try:
            played_at = datetime.fromisoformat(row["played_at"])
            if played_at.tzinfo is None:
                played_at = played_at.replace(tzinfo=timezone.utc)
            age_days = max(
                0.0, (now - played_at.astimezone(timezone.utc)).total_seconds() / 86400
            )
        except (TypeError, ValueError):
            age_days = RECENCY_HALF_LIFE_DAYS
        weight = 2 ** (-age_days / RECENCY_HALF_LIFE_DAYS)
        position = evidence.setdefault(row["fen_key"], {"total": 0.0})
        position["total"] += weight
        position[row["move_uci"]] = position.get(row["move_uci"], 0.0) + weight
    return evidence


def _move_probability(
    move_uci: str,
    fen_key: str,
    *,
    public: dict[str, dict],
    personal: dict[str, dict[str, float]],
    reply_moves: dict[str, set[str]],
    path_floor: float,
) -> tuple[float, dict]:
    """Return the probability and related information for a given move in a specific position."""
    node = public.get(fen_key)
    public_probability: float | None = None
    explorer_state = "unknown"
    maia_state = "unknown"
    if node:
        item = node["moves"].get(move_uci, {})
        explorer_probability = None
        if node["explorer_status"] == "complete" and node["explorer_games"] > 0:
            explorer_probability = item.get("explorer_probability")
            if explorer_probability is None:
                explorer_probability = 0.0
            explorer_state = "positive" if explorer_probability > 0 else "zero"
        elif node["explorer_status"] == "failed":
            explorer_state = "failed"
        elif node["explorer_status"] in {"queued", "running"}:
            explorer_state = "pending"
        maia_probability = None
        if node["maia_status"] == "complete":
            maia_probability = item.get("maia_probability")
            if maia_probability is None:
                maia_probability = 0.0
            maia_state = "positive" if maia_probability > 0 else "zero"
        elif node["maia_status"] == "failed":
            maia_state = "failed"
        elif node["maia_status"] in {"queued", "leased"}:
            maia_state = "pending"
        if explorer_probability is not None or maia_probability is not None:
            public_probability = blend_probabilities(
                explorer_probability,
                maia_probability,
                explorer_games=node["explorer_games"],
            )

    personal_position = personal.get(fen_key, {})
    personal_total = float(personal_position.get("total", 0.0))
    personal_move = float(personal_position.get(move_uci, 0.0))
    personal_state = (
        "positive" if personal_move > 0 else "zero" if personal_total > 0 else "unknown"
    )
    if public_probability is not None and personal_total > 0:
        probability = (personal_move + PERSONAL_PRIOR_GAMES * public_probability) / (
            personal_total + PERSONAL_PRIOR_GAMES
        )
    elif public_probability is not None:
        probability = public_probability
    elif personal_total > 0:
        probability = personal_move / personal_total
    else:
        options = reply_moves.get(fen_key, set())
        probability = 1 / len(options) if options else path_floor
    return max(path_floor, float(probability)), {
        "personal": personal_state,
        "explorer": explorer_state,
        "maia": maia_state,
        "personal_total": personal_total,
    }


def _line_probabilities(
    database: sqlite3.Connection,
    lines: list[IntendedLine],
    repertoire_id: str,
    horizon_fullmoves: int,
    path_floor: float,
) -> tuple[dict[str, float], dict[tuple[str, str], tuple[float, dict]], dict]:
    reply_moves = _repertoire_reply_moves(lines)
    public = _coverage_evidence(database, repertoire_id)
    personal = (
        _personal_evidence(database, set(reply_moves), lines[0].trained_color)
        if lines
        else {}
    )
    edge_evidence: dict[tuple[str, str], tuple[float, dict]] = {}
    line_probabilities: dict[str, float] = {}
    aggregate = {"personal_games": 0.0, "explorer": "unknown", "maia": "unknown"}
    maximum_plies = horizon_fullmoves * 2
    for line in lines:
        board = chess.Board(line.start_fen)
        trained_color = chess.WHITE if line.trained_color == "white" else chess.BLACK
        probability = 1.0
        for ply, move_uci in enumerate(line.moves[:maximum_plies]):
            key = canonical_fen(board.fen())
            if board.turn != trained_color:
                edge_key = (key, move_uci)
                if edge_key not in edge_evidence:
                    edge_evidence[edge_key] = _move_probability(
                        move_uci,
                        key,
                        public=public,
                        personal=personal,
                        reply_moves=reply_moves,
                        path_floor=path_floor,
                    )
                move_probability, provenance = edge_evidence[edge_key]
                probability *= move_probability
                aggregate["personal_games"] = max(
                    aggregate["personal_games"], provenance["personal_total"]
                )
                if provenance["explorer"] in {"positive", "zero"}:
                    aggregate["explorer"] = "ready"
                elif aggregate["explorer"] == "unknown":
                    aggregate["explorer"] = provenance["explorer"]
                if provenance["maia"] in {"positive", "zero"}:
                    aggregate["maia"] = "ready"
                elif aggregate["maia"] == "unknown":
                    aggregate["maia"] = provenance["maia"]
            board.push_uci(move_uci)
        line_probabilities[line.identifier] = probability
    return line_probabilities, edge_evidence, aggregate


def rebuild_introduction_priorities(
    database: sqlite3.Connection, repertoire_id: str
) -> None:
    """Recompute derived priorities without touching card history or queues."""

    line_rows = database.execute(
        "SELECT * FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
        (repertoire_id,),
    ).fetchall()
    lines = _maximal_intended_lines(line_rows)
    cards = database.execute(
        """SELECT DISTINCT c.* FROM cards c JOIN repertoire_cards rc ON rc.card_id=c.id
           WHERE rc.repertoire_id=? AND c.content_type='opening' AND c.archived=0""",
        (repertoire_id,),
    ).fetchall()
    database.execute(
        "DELETE FROM repertoire_card_introduction_priorities WHERE repertoire_id=?",
        (repertoire_id,),
    )
    if not cards:
        return
    settings = database.execute(
        "SELECT coverage_horizon_fullmoves,coverage_path_floor FROM settings WHERE id=1"
    ).fetchone()
    line_probabilities, edge_evidence, aggregate = _line_probabilities(
        database,
        lines,
        repertoire_id,
        int(settings["coverage_horizon_fullmoves"]),
        float(settings["coverage_path_floor"]),
    )
    routes_by_card: dict[str, list[tuple[IntendedLine, CardRoute]]] = {
        card["id"]: [] for card in cards
    }
    for line in lines:
        for card in cards:
            route = _line_card_route(card, line, edge_evidence)
            if route:
                routes_by_card[card["id"]].append((line, route))

    for card in cards:
        card_routes = routes_by_card[card["id"]]
        assigned_lines: list[IntendedLine] = []
        for line, route in card_routes:
            # A line contributes to its earliest currently-unintroduced card.
            matching = [
                candidate_route
                for candidate_card in cards
                if candidate_card["state"] == "new"
                and candidate_card["introduced_at"] is None
                for candidate_line, candidate_route in routes_by_card[
                    candidate_card["id"]
                ]
                if candidate_line.identifier == line.identifier
            ]
            if matching and min(item.end_ply for item in matching) == route.end_ply:
                assigned_lines.append(line)
        completion_mass = min(
            1.0,
            sum(
                line_probabilities[line.identifier]
                for line in {line.identifier: line for line in assigned_lines}.values()
            ),
        )
        all_decisions = [
            decision for _, route in card_routes for decision in route.decisions
        ]
        unique_decisions = {
            (item["fen_key"], item["move_uci"], item["ply"]): item
            for item in all_decisions
        }
        newest = sorted(
            unique_decisions.values(), key=lambda item: item["ply"], reverse=True
        )[: len(FRONTIER_WEIGHTS)]
        weighted_values: list[tuple[float, float, dict]] = []
        for weight, decision in zip(FRONTIER_WEIGHTS, newest):
            value = float(decision["reach_probability"])
            weighted_values.append((weight, value, decision))
        frontier_reach = (
            sum(weight * value for weight, value, _ in weighted_values)
            / sum(weight for weight, _, _ in weighted_values)
            if weighted_values
            else 0.0
        )
        priority_score = 0.7 * completion_mass + 0.3 * frontier_reach
        evidence_json = {
            **aggregate,
            "edge_states": {
                f"{fen_key}:{move_uci}": provenance
                for (fen_key, move_uci), (_, provenance) in edge_evidence.items()
            },
        }
        database.execute(
            """INSERT INTO repertoire_card_introduction_priorities(
                   repertoire_id,card_id,scoring_version,completed_line_ids_json,
                   completion_mass,frontier_decisions_json,frontier_reach,
                   priority_score,evidence_json,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                repertoire_id,
                card["id"],
                SCORING_VERSION,
                json.dumps(sorted({line.identifier for line in assigned_lines})),
                completion_mass,
                json.dumps(weighted_values),
                frontier_reach,
                priority_score,
                json.dumps(evidence_json),
                _now(),
            ),
        )


def rebuild_priorities_for_repertoire(repertoire_id: str) -> None:
    from ..database import connection

    with connection() as database:
        rebuild_introduction_priorities(database, repertoire_id)


def rebuild_priorities_for_game(game_id: str) -> None:
    """Refresh only repertoires whose trained side can use this game's evidence."""

    from ..database import connection

    with connection(background=True) as database:
        game = database.execute(
            "SELECT color FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        if not game:
            return
        repertoire_ids = [
            row["repertoire_id"]
            for row in database.execute(
                "SELECT DISTINCT repertoire_id FROM repertoire_lines WHERE trained_color=?",
                (game["color"],),
            )
        ]
        for repertoire_id in repertoire_ids:
            rebuild_introduction_priorities(database, repertoire_id)


def priority_status(database: sqlite3.Connection, repertoire_id: str) -> dict:
    row = database.execute(
        """SELECT evidence_json,updated_at FROM repertoire_card_introduction_priorities
           WHERE repertoire_id=? ORDER BY updated_at DESC LIMIT 1""",
        (repertoire_id,),
    ).fetchone()
    if not row:
        return {
            "state": "fallback",
            "personal_games": 0,
            "explorer": "unknown",
            "maia": "unknown",
            "updated_at": None,
            "error": None,
        }
    evidence = json.loads(row["evidence_json"])
    explorer = evidence.get("explorer", "unknown")
    maia = evidence.get("maia", "unknown")
    state = (
        "ready"
        if explorer == "ready" and maia == "ready"
        else "partial"
        if explorer == "ready" or maia == "ready" or evidence.get("personal_games", 0)
        else "fallback"
    )
    coverage_error = database.execute(
        """SELECT last_error FROM repertoire_coverage_runs WHERE repertoire_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (repertoire_id,),
    ).fetchone()
    return {
        "state": state,
        "personal_games": round(float(evidence.get("personal_games", 0)), 2),
        "explorer": explorer,
        "maia": maia,
        "updated_at": row["updated_at"],
        "error": coverage_error["last_error"] if coverage_error else None,
    }
