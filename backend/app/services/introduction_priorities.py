"""Evidence-backed admission ordering for new opening cards.

This module deliberately separates *line prevalence* from the moves a card
happens to contain.  A shared opening trunk is a prerequisite for its
descendant lines, not three independent reasons to schedule every London
card before its later branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import time

import chess

from .repertoire_comparison import canonical_fen
from .repertoire_coverage import blend_probabilities
from .activity_gate import activity_gate


SCORING_VERSION = 2
REAL_GAME_MISS_WEIGHT = 1.0
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


@dataclass(frozen=True)
class PriorityCalculationInput:
    repertoire_id: str
    lines: tuple[IntendedLine, ...]
    cards: tuple[dict, ...]
    coverage_evidence: dict[str, dict]
    personal_evidence: dict[str, dict[str, float]]
    horizon_fullmoves: int
    path_floor: float
    real_game_misses: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class PriorityRecord:
    repertoire_id: str
    card_id: str
    completed_line_ids_json: str
    completion_mass: float
    frontier_decisions_json: str
    frontier_reach: float
    priority_score: float
    evidence_json: str


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _board_fen_key(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


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
        signature.append((_board_fen_key(board), move_uci))
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

    strict_prefixes_by_color: dict[
        str, set[tuple[tuple[str, str], ...]]
    ] = {}
    for _, _, color, _, signature in candidates:
        strict_prefixes = strict_prefixes_by_color.setdefault(color, set())
        strict_prefixes.update(
            signature[:prefix_length]
            for prefix_length in range(1, len(signature))
        )
    maximal = [
        candidate
        for candidate in candidates
        if candidate[4] not in strict_prefixes_by_color.get(candidate[2], set())
    ]

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
    positions = [_board_fen_key(board)]
    for move_uci in line.moves:
        board.push_uci(move_uci)
        positions.append(_board_fen_key(board))
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
            current_key = _board_fen_key(route_board)
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
            key = _board_fen_key(board)
            if board.turn != trained_color:
                replies.setdefault(key, set()).add(move_uci)
            board.push_uci(move_uci)
    return replies


def _coverage_evidence(
    database: sqlite3.Connection, repertoire_id: str
) -> dict[str, dict]:
    run = database.execute(
        """SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=?
           ORDER BY CASE WHEN status='complete' THEN 0 ELSE 1 END,
                    created_at DESC LIMIT 1""",
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
    return _personal_evidence_from_rows([dict(row) for row in rows])


def _personal_evidence_from_rows(
    rows: list[dict],
) -> dict[str, dict[str, float]]:
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


def _real_game_miss_evidence(database: sqlite3.Connection, repertoire_id: str) -> dict[str, dict]:
    """Select one unstudied, non-excluded miss per card for a fixed bonus."""
    rows = database.execute(
        """SELECT event.id,event.card_id,event.game_id,event.played_at
           FROM repertoire_decision_events event
           JOIN imported_games game ON game.id=event.game_id
           WHERE event.repertoire_id=? AND event.outcome='miss'
             AND event.card_id IS NOT NULL AND game.adaptive_excluded=0
             AND NOT EXISTS(
                 SELECT 1 FROM reviews review
                 WHERE review.card_id=event.card_id AND review.source_kind='study'
                   AND julianday(review.reviewed_at)>julianday(event.played_at)
             )
           ORDER BY event.played_at DESC,event.id DESC""",
        (repertoire_id,),
    )
    misses: dict[str, dict] = {}
    for row in rows:
        misses.setdefault(row["card_id"], {
            "kind": "real_game_repertoire_miss",
            "weight": REAL_GAME_MISS_WEIGHT,
            "event_id": row["id"],
            "game_id": row["game_id"],
            "played_at": row["played_at"],
        })
    return misses


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
    lines: list[IntendedLine],
    public: dict[str, dict],
    personal: dict[str, dict[str, float]],
    horizon_fullmoves: int,
    path_floor: float,
) -> tuple[dict[str, float], dict[tuple[str, str], tuple[float, dict]], dict]:
    reply_moves = _repertoire_reply_moves(lines)
    edge_evidence: dict[tuple[str, str], tuple[float, dict]] = {}
    line_probabilities: dict[str, float] = {}
    aggregate = {"personal_games": 0.0, "explorer": "unknown", "maia": "unknown"}
    maximum_plies = horizon_fullmoves * 2
    for line in lines:
        board = chess.Board(line.start_fen)
        trained_color = chess.WHITE if line.trained_color == "white" else chess.BLACK
        probability = 1.0
        for ply, move_uci in enumerate(line.moves[:maximum_plies]):
            key = _board_fen_key(board)
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


def _card_routes(
    cards: tuple[dict, ...],
    lines: tuple[IntendedLine, ...],
    edge_probabilities: dict[tuple[str, str], tuple[float, dict]],
) -> dict[str, list[tuple[IntendedLine, CardRoute]]]:
    card_tries_by_start: dict[str, dict] = {}
    for card in cards:
        try:
            card_moves = tuple(json.loads(card["moves_json"]))
            card_start = canonical_fen(card["start_fen"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not card_moves:
            continue
        trie_node = card_tries_by_start.setdefault(card_start, {})
        for move_uci in card_moves:
            trie_node = trie_node.setdefault(move_uci, {})
        trie_node.setdefault("__cards__", []).append(card)

    routes_by_card: dict[str, list[tuple[IntendedLine, CardRoute]]] = {
        card["id"]: [] for card in cards
    }
    for line in lines:
        board = chess.Board(line.start_fen)
        trained_color = chess.WHITE if line.trained_color == "white" else chess.BLACK
        reach_probability = 1.0
        decisions: list[dict] = []
        positions: list[str] = []
        decisions_through_ply: list[tuple[dict, ...]] = []
        for ply, move_uci in enumerate(line.moves):
            position = _board_fen_key(board)
            positions.append(position)
            if board.turn == trained_color:
                decisions.append(
                    {
                        "fen_key": position,
                        "move_uci": move_uci,
                        "reach_probability": reach_probability,
                        "ply": ply,
                    }
                )
            elif (position, move_uci) in edge_probabilities:
                reach_probability *= edge_probabilities[(position, move_uci)][0]
            board.push_uci(move_uci)
            decisions_through_ply.append(tuple(decisions))

        for start_ply, position in enumerate(positions):
            trie_node = card_tries_by_start.get(position)
            if trie_node is None:
                continue
            for end_ply in range(start_ply + 1, len(line.moves) + 1):
                trie_node = trie_node.get(line.moves[end_ply - 1])
                if trie_node is None:
                    break
                for card in trie_node.get("__cards__", []):
                    routes_by_card[card["id"]].append(
                        (
                            line,
                            CardRoute(
                                card["id"],
                                end_ply,
                                decisions_through_ply[end_ply - 1],
                            ),
                        )
                    )
    return routes_by_card


def calculate_priority_records(
    calculation_input: PriorityCalculationInput,
) -> list[PriorityRecord]:
    cards = calculation_input.cards
    lines = calculation_input.lines
    if not cards:
        return []
    line_probabilities, edge_evidence, aggregate = _line_probabilities(
        list(lines),
        calculation_input.coverage_evidence,
        calculation_input.personal_evidence,
        calculation_input.horizon_fullmoves,
        calculation_input.path_floor,
    )
    routes_by_card = _card_routes(cards, lines, edge_evidence)
    earliest_new_end_by_line: dict[str, int] = {}
    for card in cards:
        if card["state"] != "new" or card["introduced_at"] is not None:
            continue
        for line, route in routes_by_card[card["id"]]:
            earliest_new_end_by_line[line.identifier] = min(
                earliest_new_end_by_line.get(line.identifier, route.end_ply),
                route.end_ply,
            )

    shared_evidence = {
            **aggregate,
            "edge_states": {
                f"{fen_key}:{move_uci}": provenance
                for (fen_key, move_uci), (_, provenance) in edge_evidence.items()
            },
        }
    records: list[PriorityRecord] = []
    for card in cards:
        card_routes = routes_by_card[card["id"]]
        assigned_lines = {
            line.identifier: line
            for line, route in card_routes
            if earliest_new_end_by_line.get(line.identifier) == route.end_ply
        }
        completion_mass = min(
            1.0,
            sum(line_probabilities[line_id] for line_id in assigned_lines),
        )
        unique_decisions = {
            (decision["fen_key"], decision["move_uci"], decision["ply"]): decision
            for _, route in card_routes
            for decision in route.decisions
        }
        newest = sorted(
            unique_decisions.values(), key=lambda item: item["ply"], reverse=True
        )[: len(FRONTIER_WEIGHTS)]
        weighted_values = [
            (weight, float(decision["reach_probability"]), decision)
            for weight, decision in zip(FRONTIER_WEIGHTS, newest)
        ]
        frontier_reach = (
            sum(weight * value for weight, value, _ in weighted_values)
            / sum(weight for weight, _, _ in weighted_values)
            if weighted_values
            else 0.0
        )
        records.append(
            PriorityRecord(
                calculation_input.repertoire_id,
                card["id"],
                json.dumps(sorted(assigned_lines)),
                completion_mass,
                json.dumps(weighted_values),
                frontier_reach,
                0.7 * completion_mass + 0.3 * frontier_reach
                + (REAL_GAME_MISS_WEIGHT if card["id"] in calculation_input.real_game_misses else 0.0),
                json.dumps({
                    **shared_evidence,
                    "real_game_repertoire_miss": calculation_input.real_game_misses.get(card["id"]),
                }),
            )
        )
    return records


def _load_priority_calculation_input(
    repertoire_id: str, *, background: bool
) -> PriorityCalculationInput:
    from ..database import connection

    with connection(background=background) as database:
        line_rows = [
            dict(row)
            for row in database.execute(
                "SELECT * FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
                (repertoire_id,),
            )
        ]
        cards = tuple(
            dict(row)
            for row in database.execute(
                """SELECT DISTINCT c.* FROM cards c
                   JOIN repertoire_cards rc ON rc.card_id=c.id
                   WHERE rc.repertoire_id=? AND c.content_type='opening' AND c.archived=0""",
                (repertoire_id,),
            )
        )
        settings = dict(
            database.execute(
                "SELECT coverage_horizon_fullmoves,coverage_path_floor FROM settings WHERE id=1"
            ).fetchone()
        )
        coverage_evidence = _coverage_evidence(database, repertoire_id)
        real_game_misses = _real_game_miss_evidence(database, repertoire_id)
    lines = tuple(_maximal_intended_lines(line_rows))
    reply_moves = _repertoire_reply_moves(list(lines))
    personal_rows: list[dict] = []
    if lines and reply_moves:
        fen_keys = sorted(reply_moves)
        placeholders = ",".join("?" for _ in fen_keys)
        with connection(background=background) as database:
            personal_rows = [
                dict(row)
                for row in database.execute(
                    f"""SELECT p.fen_key,p.move_uci,g.played_at
                        FROM game_position_occurrences p
                        JOIN imported_games g ON g.id=p.game_id
                        WHERE p.fen_key IN ({placeholders}) AND p.move_uci IS NOT NULL
                          AND g.color=? AND g.adaptive_excluded=0
                          AND g.speed IN ({','.join('?' for _ in SUPPORTED_PERSONAL_SPEEDS)})""",
                    (*fen_keys, lines[0].trained_color, *SUPPORTED_PERSONAL_SPEEDS),
                )
            ]
    return PriorityCalculationInput(
        repertoire_id,
        lines,
        cards,
        coverage_evidence,
        _personal_evidence_from_rows(personal_rows),
        int(settings["coverage_horizon_fullmoves"]),
        float(settings["coverage_path_floor"]),
        real_game_misses,
    )


def _replace_priority_records(
    database: sqlite3.Connection,
    repertoire_id: str,
    records: list[PriorityRecord],
) -> None:
    database.execute(
        "DELETE FROM repertoire_card_introduction_priorities WHERE repertoire_id=?",
        (repertoire_id,),
    )
    updated_at = _now()
    database.executemany(
        """INSERT INTO repertoire_card_introduction_priorities(
               repertoire_id,card_id,scoring_version,completed_line_ids_json,
               completion_mass,frontier_decisions_json,frontier_reach,
               priority_score,evidence_json,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                record.repertoire_id,
                record.card_id,
                SCORING_VERSION,
                record.completed_line_ids_json,
                record.completion_mass,
                record.frontier_decisions_json,
                record.frontier_reach,
                record.priority_score,
                record.evidence_json,
                updated_at,
            )
            for record in records
        ],
    )


def rebuild_introduction_priorities(
    database: sqlite3.Connection, repertoire_id: str
) -> None:
    """Synchronous compatibility path used by focused parity tests."""

    line_rows = [
        dict(row)
        for row in database.execute(
            "SELECT * FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
            (repertoire_id,),
        )
    ]
    lines = tuple(_maximal_intended_lines(line_rows))
    cards = tuple(
        dict(row)
        for row in database.execute(
            """SELECT DISTINCT c.* FROM cards c JOIN repertoire_cards rc ON rc.card_id=c.id
               WHERE rc.repertoire_id=? AND c.content_type='opening' AND c.archived=0""",
            (repertoire_id,),
        )
    )
    settings = database.execute(
        "SELECT coverage_horizon_fullmoves,coverage_path_floor FROM settings WHERE id=1"
    ).fetchone()
    reply_moves = _repertoire_reply_moves(list(lines))
    calculation_input = PriorityCalculationInput(
        repertoire_id,
        lines,
        cards,
        _coverage_evidence(database, repertoire_id),
        _personal_evidence(
            database,
            set(reply_moves),
            lines[0].trained_color if lines else "white",
        ),
        int(settings["coverage_horizon_fullmoves"]),
        float(settings["coverage_path_floor"]),
        _real_game_miss_evidence(database, repertoire_id),
    )
    _replace_priority_records(
        database,
        repertoire_id,
        calculate_priority_records(calculation_input),
    )


def enqueue_priority_refresh(
    repertoire_id: str, *, background: bool = False, quiet_seconds: int = 5
) -> int:
    from ..database import connection

    now = datetime.now(timezone.utc)
    next_attempt_at = (now + timedelta(seconds=quiet_seconds)).isoformat()
    with connection(background=background) as database:
        database.execute(
            """INSERT INTO repertoire_priority_jobs(
                   repertoire_id,generation,status,attempts,next_attempt_at,last_error,updated_at
               ) VALUES(?,1,'queued',0,?,NULL,?)
               ON CONFLICT(repertoire_id) DO UPDATE SET
                   generation=repertoire_priority_jobs.generation+1,
                   status='queued',attempts=0,next_attempt_at=excluded.next_attempt_at,
                   last_error=NULL,updated_at=excluded.updated_at""",
            (repertoire_id, next_attempt_at, now.isoformat()),
        )
        return int(
            database.execute(
                "SELECT generation FROM repertoire_priority_jobs WHERE repertoire_id=?",
                (repertoire_id,),
            ).fetchone()[0]
        )


def enqueue_priority_refreshes_for_game(
    game_id: str, *, background: bool = False
) -> None:
    from ..database import connection

    with connection(background=background) as database:
        game = database.execute(
            "SELECT color FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        if not game:
            return
        repertoire_ids = [
            row[0]
            for row in database.execute(
                "SELECT DISTINCT repertoire_id FROM repertoire_lines WHERE trained_color=?",
                (game["color"],),
            )
        ]
    for repertoire_id in repertoire_ids:
        enqueue_priority_refresh(repertoire_id, background=background)


def claim_priority_refresh() -> dict | None:
    from ..database import connection
    from .background_activity import claimable, control_order

    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute("BEGIN IMMEDIATE")
        job = database.execute(
            f"""SELECT * FROM repertoire_priority_jobs
               WHERE status='queued' AND next_attempt_at<=?
               AND {claimable('priority', 'repertoire_priority_jobs.repertoire_id')}
               ORDER BY {control_order('priority', 'repertoire_priority_jobs.repertoire_id')}next_attempt_at,updated_at LIMIT 1""",
            (_now(),),
        ).fetchone()
        if not job:
            return None
        changed = database.execute(
            """UPDATE repertoire_priority_jobs
               SET status='running',attempts=attempts+1,updated_at=?
               WHERE repertoire_id=? AND generation=? AND status='queued'""",
            (_now(), job["repertoire_id"], job["generation"]),
        ).rowcount
        claimed_job = dict(job) if changed else None
    if claimed_job:
        logging.getLogger("tempo.background").info(
            "priority refresh claimed repertoire_id=%s generation=%s attempts=%s",
            claimed_job["repertoire_id"],
            claimed_job["generation"],
            int(claimed_job["attempts"]) + 1,
        )
    return claimed_job


def execute_priority_refresh(job: dict) -> None:
    from ..database import connection

    repertoire_id = job["repertoire_id"]
    generation = int(job["generation"])
    started = time.perf_counter()
    with activity_gate.background_job("repertoire_priority", repertoire_id):
        try:
            read_started = time.perf_counter()
            calculation_input = _load_priority_calculation_input(
                repertoire_id, background=True
            )
            logging.getLogger("tempo.background").info(
                "priority refresh read repertoire_id=%s generation=%d duration=%.3fs",
                repertoire_id,
                generation,
                time.perf_counter() - read_started,
            )
            compute_started = time.perf_counter()
            records = calculate_priority_records(calculation_input)
            logging.getLogger("tempo.background").info(
                "priority refresh compute repertoire_id=%s generation=%d records=%d duration=%.3fs",
                repertoire_id,
                generation,
                len(records),
                time.perf_counter() - compute_started,
            )
            activity_gate.wait_for_foreground()
            commit_started = time.perf_counter()
            with connection(background=True) as database:
                database.execute("BEGIN IMMEDIATE")
                current = database.execute(
                    "SELECT generation,status FROM repertoire_priority_jobs WHERE repertoire_id=?",
                    (repertoire_id,),
                ).fetchone()
                if (
                    not current
                    or int(current["generation"]) != generation
                    or current["status"] != "running"
                ):
                    logging.getLogger("tempo.background").info(
                        "priority refresh discarded stale generation repertoire_id=%s generation=%d duration=%.3fs",
                        repertoire_id,
                        generation,
                        time.perf_counter() - commit_started,
                    )
                    return
                _replace_priority_records(database, repertoire_id, records)
                database.execute(
                    """UPDATE repertoire_priority_jobs
                       SET status='complete',last_error=NULL,updated_at=?
                       WHERE repertoire_id=? AND generation=?""",
                    (_now(), repertoire_id, generation),
                )
            logging.getLogger("tempo.background").info(
                "priority refresh commit repertoire_id=%s generation=%d duration=%.3fs",
                repertoire_id,
                generation,
                time.perf_counter() - commit_started,
            )
            logging.getLogger("tempo.background").info(
                "priority refresh complete repertoire_id=%s generation=%d records=%d duration=%.3fs",
                repertoire_id,
                generation,
                len(records),
                time.perf_counter() - started,
            )
        except Exception as error:
            retry_delay = min(60, 2 ** min(5, int(job.get("attempts", 0)) + 1))
            with connection(background=True) as database:
                current = database.execute(
                    "SELECT generation,attempts FROM repertoire_priority_jobs WHERE repertoire_id=?",
                    (repertoire_id,),
                ).fetchone()
                if not current or int(current["generation"]) != generation:
                    return
                failed = int(current["attempts"]) >= 5
                database.execute(
                    """UPDATE repertoire_priority_jobs
                       SET status=?,next_attempt_at=?,last_error=?,updated_at=?
                       WHERE repertoire_id=? AND generation=?""",
                    (
                        "failed" if failed else "queued",
                        (datetime.now(timezone.utc) + timedelta(seconds=retry_delay)).isoformat(),
                        str(error),
                        _now(),
                        repertoire_id,
                        generation,
                    ),
                )
                logging.getLogger("tempo.background").exception(
                "priority refresh failed repertoire_id=%s generation=%d retry_delay=%ds failed=%s",
                repertoire_id,
                generation,
                retry_delay,
                failed,
                )


def load_priority_calculation_input(job: dict) -> PriorityCalculationInput:
    """Copy immutable priority inputs while holding SQLite only for reads."""

    return _load_priority_calculation_input(job["repertoire_id"], background=True)


def publish_priority_records(job: dict, records: list[PriorityRecord]) -> bool:
    """Stage bounded chunks, then atomically publish the current generation."""

    from ..database import connection

    repertoire_id = job["repertoire_id"]
    generation = int(job["generation"])
    updated_at = _now()
    with connection(background=True) as database:
        database.execute(
            "DELETE FROM repertoire_card_priority_generations WHERE repertoire_id=? AND generation=?",
            (repertoire_id, generation),
        )
    for chunk_start in range(0, len(records), 100):
        chunk = records[chunk_start : chunk_start + 100]
        activity_gate.wait_for_foreground()
        with connection(background=True) as database:
            database.executemany(
                """INSERT INTO repertoire_card_priority_generations(
                       repertoire_id,generation,card_id,scoring_version,
                       completed_line_ids_json,completion_mass,frontier_decisions_json,
                       frontier_reach,priority_score,evidence_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        record.repertoire_id,
                        generation,
                        record.card_id,
                        SCORING_VERSION,
                        record.completed_line_ids_json,
                        record.completion_mass,
                        record.frontier_decisions_json,
                        record.frontier_reach,
                        record.priority_score,
                        record.evidence_json,
                        updated_at,
                    )
                    for record in chunk
                ],
            )
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        current = database.execute(
            "SELECT generation,status FROM repertoire_priority_jobs WHERE repertoire_id=?",
            (repertoire_id,),
        ).fetchone()
        if (
            not current
            or int(current["generation"]) != generation
            or current["status"] != "running"
        ):
            return False
        database.execute(
            """INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at)
               VALUES(?,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET
               generation=excluded.generation,updated_at=excluded.updated_at""",
            (repertoire_id, generation, updated_at),
        )
        database.execute(
            """UPDATE repertoire_priority_jobs SET status='complete',last_error=NULL,updated_at=?
               WHERE repertoire_id=? AND generation=?""",
            (_now(), repertoire_id, generation),
        )
    return True


def priority_status(database: sqlite3.Connection, repertoire_id: str) -> dict:
    row = database.execute(
        """SELECT evidence_json,updated_at FROM repertoire_card_priority_generations generated
           WHERE generated.repertoire_id=? AND generated.generation=(
               SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=?
           ) ORDER BY updated_at DESC LIMIT 1""",
        (repertoire_id, repertoire_id),
    ).fetchone()
    if not row:
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
