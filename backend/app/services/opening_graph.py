"""Canonical one-learner-decision opening graph construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import sqlite3

import chess

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
        steps.extend(
            GraphStep(
                repertoire_id=graph_input.repertoire_id,
                line_id=line["id"],
                decision_index=segment.decision_index,
                card_id=segment.card_id,
                parent_card_id=segment.parent_card_id,
                starting_fen=segment.starting_fen,
                moves=segment.moves,
                trained_color=segment.trained_color,
            )
            for segment in segments
        )
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
    if not lines:
        raise KeyError("Repertoire has no lines")
    return GraphInput(repertoire_id, lines, int(settings[0]))


def _chunks(values: tuple[GraphStep, ...], size: int = 250):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def execute_opening_graph_rebuild(task: dict) -> None:
    """Compute without SQLite, stage bounded slices, then publish one generation."""

    from .database_executor import submit_background_write

    repertoire_id = task["payload"]["repertoire_id"]
    generation = int(task["generation"])
    graph_steps = build_graph(load_graph_input(repertoire_id))

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
                       parent_card_id,starting_fen,moves_json,trained_color
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        step.repertoire_id,
                        generation,
                        step.line_id,
                        step.decision_index,
                        step.card_id,
                        step.parent_card_id,
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

    submit_background_write(
        publish,
        label=f"opening-graph-publish:{repertoire_id}:{generation}",
    )
