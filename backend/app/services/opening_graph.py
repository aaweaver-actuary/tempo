"""Canonical one-learner-decision opening graph construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import sqlite3

import chess
from fsrs import Card, State

from .cards import card_id


@dataclass(frozen=True)
class DecisionSegment:
    card_id: str
    decision_index: int
    starting_fen: str
    moves: tuple[str, ...]
    parent_card_id: str | None
    trained_color: str


@dataclass(frozen=True)
class GraphStep:
    repertoire_id: str
    line_id: str
    decision_index: int
    card_id: str
    parent_card_id: str | None
    decision_fen_key: str
    starting_fen: str
    moves: tuple[str, ...]
    trained_color: str


@dataclass(frozen=True)
class GraphInput:
    repertoire_id: str
    lines: tuple[dict, ...]
    default_depth: int


def build_graph(graph_input: GraphInput) -> tuple[GraphStep, ...]:
    steps: list[GraphStep] = []
    for line in graph_input.lines:
        maximum_decisions = int(line.get("learner_decision_count") or graph_input.default_depth)
        segments = decision_segments(
            line["start_fen"],
            tuple(json.loads(line["moves_json"])),
            line["trained_color"],
            maximum_decisions,
        )
        for segment in segments:
            decision_board = chess.Board(segment.starting_fen)
            for setup_move in segment.moves[:-1]:
                decision_board.push_uci(setup_move)
            steps.append(GraphStep(
                repertoire_id=graph_input.repertoire_id,
                line_id=line["id"],
                decision_index=segment.decision_index,
                card_id=segment.card_id,
                parent_card_id=segment.parent_card_id,
                decision_fen_key=" ".join(decision_board.fen().split()[:4]),
                starting_fen=segment.starting_fen,
                moves=segment.moves,
                trained_color=segment.trained_color,
            ))
    return tuple(steps)


def decision_segments(
    starting_fen: str,
    moves_uci: list[str] | tuple[str, ...],
    trained_color: str,
    maximum_decisions: int,
) -> tuple[DecisionSegment, ...]:
    """Split a legal route into contextual cards testing one learner move each."""

    if trained_color not in {"white", "black"}:
        raise ValueError("trained_color must be white or black")
    if maximum_decisions < 0:
        raise ValueError("maximum_decisions must be nonnegative")

    board = chess.Board(starting_fen)
    trained_chess_color = chess.WHITE if trained_color == "white" else chess.BLACK
    segment_starting_fen = board.fen()
    segment_moves: list[str] = []
    segments: list[DecisionSegment] = []
    parent_card_id: str | None = None

    for move_uci in moves_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal repertoire move {move_uci}")
        moving_color = board.turn
        segment_moves.append(move_uci)
        board.push(move)
        if moving_color != trained_chess_color:
            continue

        identifier = card_id(segment_starting_fen, segment_moves)
        segments.append(
            DecisionSegment(
                card_id=identifier,
                decision_index=len(segments),
                starting_fen=segment_starting_fen,
                moves=tuple(segment_moves),
                parent_card_id=parent_card_id,
                trained_color=trained_color,
            )
        )
        parent_card_id = identifier
        segment_starting_fen = board.fen()
        segment_moves = []
        if len(segments) >= maximum_decisions:
            break

    return tuple(segments)


def enqueue_opening_graph_rebuild(
    repertoire_id: str, *, foreground: bool = True
) -> dict:
    from .durable_tasks import enqueue_task

    return enqueue_task(
        "opening_graph_rebuild",
        repertoire_id,
        {"repertoire_id": repertoire_id},
        priority=40,
        foreground=foreground,
    )


def load_graph_input(repertoire_id: str) -> GraphInput:
    from ..database import read_connection

    with read_connection() as database:
        lines = tuple(
            dict(row)
            for row in database.execute(
                """SELECT line.*,depth.learner_decision_count
                   FROM repertoire_lines line
                   LEFT JOIN repertoire_line_training_depths depth ON depth.line_id=line.id
                   WHERE line.repertoire_id=? ORDER BY line.id""",
                (repertoire_id,),
            )
        )
        settings = database.execute(
            "SELECT initial_depth FROM settings WHERE id=1"
        ).fetchone()
    return GraphInput(repertoire_id, lines, int(settings[0]))


def _load_legacy_snapshot(repertoire_id: str) -> tuple[tuple[dict, ...], dict[str, list[dict]], set[str]]:
    from ..database import read_connection

    with read_connection() as database:
        cards = tuple(
            dict(row)
            for row in database.execute(
                """SELECT DISTINCT card.* FROM cards card
                   JOIN repertoire_cards link ON link.card_id=card.id
                   WHERE link.repertoire_id=? AND card.content_type='opening'
                     AND card.archived=0""",
                (repertoire_id,),
            )
        )
        reviews: dict[str, list[dict]] = {}
        for row in database.execute(
            """SELECT review.* FROM reviews review
               JOIN repertoire_cards link ON link.card_id=review.card_id
               WHERE link.repertoire_id=? ORDER BY review.reviewed_at DESC,review.id DESC""",
            (repertoire_id,),
        ):
            reviews.setdefault(row["card_id"], []).append(dict(row))
        existing_card_ids = {
            row[0]
            for row in database.execute(
                """SELECT card.id FROM cards card
                   WHERE EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=card.id)
                      OR EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=card.id)"""
            )
        }
    return cards, reviews, existing_card_ids


def _trained_decisions(card: dict) -> set[tuple[str, str]]:
    try:
        board = chess.Board(card["start_fen"])
        moves = json.loads(card["moves_json"])
        trained_color = chess.WHITE if card["trained_color"] == "white" else chess.BLACK
    except (ValueError, TypeError, json.JSONDecodeError):
        return set()
    decisions: set[tuple[str, str]] = set()
    for move_uci in moves:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            return set()
        if board.turn == trained_color:
            decisions.add((" ".join(board.fen().split()[:4]), move_uci))
        board.push(move)
    return decisions


def _legacy_mappings(
    graph_steps: tuple[GraphStep, ...], legacy_cards: tuple[dict, ...]
) -> tuple[tuple[str, str], ...]:
    decisions_by_card = {card["id"]: _trained_decisions(card) for card in legacy_cards}
    return tuple(
        sorted(
            {
                (legacy_card_id, step.card_id)
                for legacy_card_id, decisions in decisions_by_card.items()
                for step in graph_steps
                if (step.decision_fen_key, step.moves[-1]) in decisions
            }
        )
    )


def _seed_values(
    graph_steps: tuple[GraphStep, ...],
    legacy_cards: tuple[dict, ...],
    reviews_by_card: dict[str, list[dict]],
    existing_card_ids: set[str],
) -> dict[str, dict]:
    legacy_by_id = {card["id"]: card for card in legacy_cards}
    mappings = _legacy_mappings(graph_steps, legacy_cards)
    candidates_by_decision: dict[str, list[dict]] = {}
    for legacy_card_id, decision_card_id in mappings:
        successful_reviews = [
            review
            for review in reviews_by_card.get(legacy_card_id, [])
            if review["rating"] == "correct"
        ]
        if successful_reviews:
            candidates_by_decision.setdefault(decision_card_id, []).append(
                {"card": legacy_by_id[legacy_card_id], "reviews": successful_reviews}
            )

    today = date.today()
    latest_verification = today + timedelta(days=7)
    seeds: dict[str, dict] = {}
    for decision_card_id, candidates in candidates_by_decision.items():
        if decision_card_id in existing_card_ids:
            continue
        mature_candidates = [
            candidate for candidate in candidates if candidate["card"]["state"] == "mature"
        ]
        qualifying = mature_candidates or candidates
        chosen = min(
            qualifying,
            key=lambda candidate: (
                float(candidate["card"]["stability"] or 0),
                candidate["card"]["due_date"],
                candidate["card"]["id"],
            ),
        )
        source = chosen["card"]
        source_reviews = chosen["reviews"]
        successful_days = len({review["reviewed_at"][:10] for review in source_reviews})
        recent_clean = min(2, len(source_reviews))
        verification_due = min(date.fromisoformat(source["due_date"]), latest_verification)
        stability = min(14.0, float(source["stability"] or 0))
        if source["fsrs_card_json"]:
            fsrs_card = Card.from_json(source["fsrs_card_json"])
        else:
            fsrs_card = Card()
        fsrs_card.state = State.Review if source["state"] == "mature" else State.Learning
        fsrs_card.stability = stability or fsrs_card.stability
        fsrs_card.due = datetime.combine(verification_due, time(hour=12), timezone.utc)
        seeds[decision_card_id] = {
            "source_card_id": source["id"],
            "state": "mature" if source["state"] == "mature" else "learning",
            "due_date": verification_due.isoformat(),
            "interval_days": min(7, max(0, int(source["interval_days"] or 0))),
            "stability": stability,
            "fsrs_card_json": fsrs_card.to_json(),
            "first_correct_at": source["first_correct_at"] or source_reviews[-1]["reviewed_at"],
            "introduced_at": source["introduced_at"] or today.isoformat(),
            "baseline_successful_days": min(3, successful_days),
            "baseline_recent_clean": recent_clean,
            "verification_due": verification_due.isoformat(),
        }
    return seeds


def _chunks(values: tuple[GraphStep, ...], size: int = 250):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def execute_opening_graph_rebuild(task: dict) -> None:
    """Compute without SQLite, stage bounded slices, then publish one generation."""

    from .database_executor import submit_background_write

    repertoire_id = task["payload"]["repertoire_id"]
    generation = int(task["generation"])
    graph_steps = build_graph(load_graph_input(repertoire_id))
    legacy_cards, reviews_by_card, existing_card_ids = _load_legacy_snapshot(repertoire_id)
    legacy_mappings = _legacy_mappings(graph_steps, legacy_cards)
    schedule_seeds = _seed_values(
        graph_steps, legacy_cards, reviews_by_card, existing_card_ids
    )

    def clear_staging(database: sqlite3.Connection) -> None:
        database.execute(
            "DELETE FROM opening_graph_steps WHERE repertoire_id=? AND generation=?",
            (repertoire_id, generation),
        )

    submit_background_write(
        clear_staging,
        label=f"opening-graph-clear:{repertoire_id}:{generation}",
    )
    for graph_step_chunk in _chunks(graph_steps):
        def stage(
            database: sqlite3.Connection,
            values: tuple[GraphStep, ...] = graph_step_chunk,
        ) -> None:
            database.executemany(
                """INSERT INTO opening_graph_steps(
                       repertoire_id,generation,line_id,decision_index,card_id,
                       parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        step.repertoire_id,
                        generation,
                        step.line_id,
                        step.decision_index,
                        step.card_id,
                        step.parent_card_id,
                        step.decision_fen_key,
                        step.starting_fen,
                        json.dumps(step.moves),
                        step.trained_color,
                    )
                    for step in values
                ],
            )
            database.executemany(
                """INSERT OR IGNORE INTO cards(
                       id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                       content_type,trained_color,pending_validation
                   ) VALUES(?,?,'response',?,?,?,?,'opening',?,0)""",
                [
                    (
                        step.card_id,
                        repertoire_id,
                        step.starting_fen,
                        json.dumps(step.moves),
                        "new" if step.parent_card_id is None else "locked",
                        date.today().isoformat(),
                        step.trained_color,
                    )
                    for step in values
                ],
            )

        submit_background_write(
            stage,
            label=f"opening-graph-stage:{repertoire_id}:{generation}",
        )

    for mapping_offset in range(0, len(legacy_mappings), 500):
        mapping_chunk = legacy_mappings[mapping_offset : mapping_offset + 500]

        def stage_mappings(database: sqlite3.Connection, values=mapping_chunk) -> None:
            database.executemany(
                """INSERT OR IGNORE INTO opening_graph_legacy_mappings(
                       repertoire_id,generation,legacy_card_id,decision_card_id
                   ) VALUES(?,?,?,?)""",
                [
                    (repertoire_id, generation, legacy_card_id, decision_card_id)
                    for legacy_card_id, decision_card_id in values
                ],
            )

        submit_background_write(
            stage_mappings,
            label=f"opening-graph-map:{repertoire_id}:{generation}",
        )

    seed_items = tuple(schedule_seeds.items())
    for seed_offset in range(0, len(seed_items), 100):
        seed_chunk = seed_items[seed_offset : seed_offset + 100]

        def apply_seeds(database: sqlite3.Connection, values=seed_chunk) -> None:
            now = datetime.now(timezone.utc).isoformat()
            for decision_card_id, seed in values:
                database.execute(
                    """UPDATE cards SET state=?,due_date=?,interval_days=?,stability=?,
                           fsrs_card_json=?,first_correct_at=?,reinforcement_pending=0,
                           introduced_at=?
                       WHERE id=? AND NOT EXISTS(
                           SELECT 1 FROM reviews WHERE card_id=cards.id
                       )""",
                    (
                        seed["state"], seed["due_date"], seed["interval_days"],
                        seed["stability"], seed["fsrs_card_json"],
                        seed["first_correct_at"], seed["introduced_at"],
                        decision_card_id,
                    ),
                )
                database.execute(
                    """INSERT OR IGNORE INTO opening_card_schedule_seeds(
                           card_id,source_card_id,baseline_successful_days,
                           baseline_recent_clean,verification_due,created_at
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        decision_card_id, seed["source_card_id"],
                        seed["baseline_successful_days"], seed["baseline_recent_clean"],
                        seed["verification_due"], now,
                    ),
                )

        submit_background_write(
            apply_seeds,
            label=f"opening-graph-seed:{repertoire_id}:{generation}",
        )

    def publish(database: sqlite3.Connection) -> None:
        current = database.execute(
            "SELECT generation FROM background_tasks WHERE id=? AND lease_token=?",
            (task["id"], task["lease_token"]),
        ).fetchone()
        if not current or int(current["generation"]) != generation:
            return
        now = datetime.now(timezone.utc).isoformat()
        database.execute(
            """INSERT INTO opening_graph_publications(
                   repertoire_id,generation,state,last_error,published_at
               ) VALUES(?,?,'ready',NULL,?)
               ON CONFLICT(repertoire_id) DO UPDATE SET
                   generation=excluded.generation,state='ready',last_error=NULL,
                   published_at=excluded.published_at""",
            (repertoire_id, generation, now),
        )
        database.execute(
            """INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id)
               SELECT repertoire_id,card_id FROM opening_graph_steps
               WHERE repertoire_id=? AND generation=?""",
            (repertoire_id, generation),
        )
        database.execute(
            """UPDATE daily_queue SET status='superseded'
               WHERE status='queued' AND card_id IN (
                   SELECT link.card_id FROM repertoire_cards link
                   JOIN cards legacy ON legacy.id=link.card_id
                   WHERE link.repertoire_id=? AND legacy.content_type='opening'
                     AND NOT EXISTS(
                         SELECT 1 FROM opening_graph_steps step
                         WHERE step.repertoire_id=? AND step.generation=?
                           AND step.card_id=legacy.id
                     )
               )""",
            (repertoire_id, repertoire_id, generation),
        )
        database.execute(
            """DELETE FROM repertoire_cards
               WHERE repertoire_id=? AND card_id IN (
                   SELECT card.id FROM cards card WHERE card.content_type='opening'
                     AND NOT EXISTS(
                         SELECT 1 FROM opening_graph_steps step
                         WHERE step.repertoire_id=? AND step.generation=?
                           AND step.card_id=card.id
                     )
               )""",
            (repertoire_id, repertoire_id, generation),
        )
        database.execute(
            """UPDATE cards SET repertoire_id=(
                   SELECT MIN(link.repertoire_id) FROM repertoire_cards link
                   WHERE link.card_id=cards.id
               )
               WHERE repertoire_id=? AND EXISTS(
                   SELECT 1 FROM repertoire_cards link WHERE link.card_id=cards.id
               ) AND NOT EXISTS(
                   SELECT 1 FROM repertoire_cards former
                   WHERE former.card_id=cards.id AND former.repertoire_id=?
               )""",
            (repertoire_id, repertoire_id),
        )
        database.execute(
            """UPDATE cards SET archived=1
               WHERE content_type='opening' AND archived=0
                 AND NOT EXISTS(
                     SELECT 1 FROM repertoire_cards link WHERE link.card_id=cards.id
                 )"""
        )
        database.execute(
            "DELETE FROM repertoire_integrity_card_blocks WHERE repertoire_id=?",
            (repertoire_id,),
        )
        database.execute(
            """INSERT OR IGNORE INTO repertoire_integrity_card_blocks(
                   repertoire_id,card_id,issue_id,scan_generation,published_at
               )
               SELECT ?,step.card_id,issue.id,?,?
               FROM opening_graph_steps step
               JOIN repertoire_integrity_issues issue
                 ON issue.repertoire_id=step.repertoire_id
                AND issue.fen_key=step.decision_fen_key
               WHERE step.repertoire_id=? AND step.generation=?""",
            (repertoire_id, f"graph:{generation}", now, repertoire_id, generation),
        )
        database.execute(
            """UPDATE cards SET pending_validation=CASE WHEN EXISTS(
                   SELECT 1 FROM repertoire_integrity_card_blocks block
                   WHERE block.card_id=cards.id
               ) THEN 1 ELSE 0 END
               WHERE id IN (
                   SELECT card_id FROM opening_graph_steps
                   WHERE repertoire_id=? AND generation=?
               )""",
            (repertoire_id, generation),
        )

    submit_background_write(
        publish,
        label=f"opening-graph-publish:{repertoire_id}:{generation}",
    )
    from .durable_tasks import enqueue_task

    enqueue_task(
        "daily_queue",
        date.today().isoformat(),
        {"queue_date": date.today().isoformat()},
        priority=10,
        foreground=False,
    )
