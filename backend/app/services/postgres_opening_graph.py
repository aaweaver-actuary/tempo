"""Per-line PostgreSQL opening-graph preparation for durable rebuild slices."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from ..database import background_read_connection
from .. import postgres_store
from .durable_tasks import advance_task_slice_in_transaction, lock_current_slice
from .opening_graph import GraphInput, GraphStep, build_graph
from .redis_admission_gate import background_lease


_STEP_BATCH_SIZE = 8


@dataclass(frozen=True)
class PreparedGraphLine:
    """One line's graph, calculated after its read transaction has closed."""

    line_id: str
    steps: tuple[GraphStep, ...]


def prepare_next_graph_line(repertoire_id: str, after_line_id: str) -> PreparedGraphLine | None:
    """Read one source line, close PostgreSQL, then traverse its moves."""

    with background_read_connection() as database:
        line = database.execute_native(
            "SELECT line.*,depth.learner_decision_count "
            "FROM repertoire_lines line "
            "LEFT JOIN repertoire_line_training_depths depth ON depth.line_id=line.id "
            "WHERE line.repertoire_id=%s AND line.id>%s ORDER BY line.id LIMIT 1",
            (repertoire_id, after_line_id),
        ).fetchone()
        if line is None:
            return None
        default_depth = database.execute_native(
            "SELECT initial_depth FROM settings WHERE id=1"
        ).fetchone()[0]
        prefix_overrides = tuple(dict(row) for row in database.execute_native(
            "SELECT split.source_card_id,split.shortened_card_id,"
            "shortened.start_fen shortened_start_fen,"
            "shortened.moves_json shortened_moves_json,"
            "split.continuation_card_id,"
            "continuation.start_fen continuation_start_fen,"
            "continuation.moves_json continuation_moves_json "
            "FROM prefix_splits split "
            "JOIN cards shortened ON shortened.id=split.shortened_card_id "
            "JOIN cards continuation ON continuation.id=split.continuation_card_id"
        ))
        source_line = dict(line)
    graph_input = GraphInput(repertoire_id, (source_line,), int(default_depth), prefix_overrides)
    return PreparedGraphLine(source_line["id"], build_graph(graph_input))


def stage_graph_line_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    prepared: PreparedGraphLine | None,
) -> bool:
    """Stage at most eight steps and atomically advance the durable cursor."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if prepared is None:
        return advance_task_slice_in_transaction(
            database, task, next_phase="link", next_payload={**payload, "step_offset": 0},
        )
    offset = int(payload.get("step_offset", 0))
    if offset < 0 or offset >= max(1, len(prepared.steps)):
        raise ValueError("Opening graph slice cursor is outside its source line")
    batch = prepared.steps[offset:offset + _STEP_BATCH_SIZE]
    generation = int(task["generation"])
    study_day = str(payload["local_day"])
    with database.raw.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO opening_graph_steps("
            "repertoire_id,generation,line_id,decision_index,segment_kind,"
            "first_decision_index,last_decision_index,decision_fen_keys_json,"
            "card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color"
            ") VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(repertoire_id,generation,line_id,decision_index) DO NOTHING",
            [
                (step.repertoire_id, generation, step.line_id, step.decision_index,
                 step.segment_kind, step.first_decision_index, step.last_decision_index,
                 json.dumps(step.decision_fen_keys), step.card_id, step.parent_card_id,
                 step.decision_fen_key, step.starting_fen, json.dumps(step.moves),
                 step.trained_color)
                for step in batch
            ],
        )
        cursor.executemany(
            "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,"
            "due_date,content_type,trained_color,pending_validation) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,'opening',%s,0) ON CONFLICT(id) DO NOTHING",
            [
                (step.card_id, step.repertoire_id,
                 "prefix" if step.segment_kind == "prefix" else "response",
                 step.starting_fen, json.dumps(step.moves),
                 "new" if step.parent_card_id is None else "locked",
                 study_day, step.trained_color)
                for step in batch
            ],
        )
    next_offset = offset + len(batch)
    next_payload = {
        **payload,
        "after_line_id": prepared.line_id if next_offset == len(prepared.steps)
        else payload.get("after_line_id", ""),
        "step_offset": 0 if next_offset == len(prepared.steps) else next_offset,
    }
    return advance_task_slice_in_transaction(
        database, task, next_phase="stage", next_payload=next_payload,
    )


def execute_graph_stage_slice(task: dict[str, Any]) -> bool:
    """Prepare one bounded line batch without a connection, then checkpoint it."""

    payload = task["payload"]
    prepared = prepare_next_graph_line(
        str(payload["repertoire_id"]), str(payload.get("after_line_id", "")),
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return stage_graph_line_in_transaction(database, task, prepared)


def prepare_next_graph_cards(
    repertoire_id: str, generation: int, after_card_id: str,
) -> tuple[str, ...]:
    """Read at most eight distinct staged cards under the admission gate."""

    with background_read_connection() as database:
        cards = database.execute_native(
            "SELECT card_id FROM opening_graph_steps WHERE repertoire_id=%s "
            "AND generation=%s AND card_id>%s GROUP BY card_id ORDER BY card_id LIMIT 8",
            (repertoire_id, generation, after_card_id),
        ).fetchall()
    return tuple(str(card[0]) for card in cards)


def link_graph_cards_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    card_ids: tuple[str, ...],
) -> bool:
    """Link at most eight staged cards, then checkpoint the sorted cursor."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if not card_ids:
        return advance_task_slice_in_transaction(
            database, task, next_phase="publish", next_payload=payload,
        )
    repertoire_id = str(payload["repertoire_id"])
    generation = int(task["generation"])
    with database.raw.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO repertoire_cards(repertoire_id,card_id) "
            "SELECT %s,%s WHERE EXISTS(SELECT 1 FROM opening_graph_steps "
            "WHERE repertoire_id=%s AND generation=%s AND card_id=%s) "
            "ON CONFLICT(repertoire_id,card_id) DO NOTHING",
            [(repertoire_id, card_id, repertoire_id, generation, card_id)
             for card_id in card_ids],
        )
    return advance_task_slice_in_transaction(
        database, task, next_phase="link",
        next_payload={**payload, "after_card_id": card_ids[-1]},
    )


def execute_graph_link_slice(task: dict[str, Any]) -> bool:
    """Link eight staged cards without delaying foreground transactions."""

    payload = task["payload"]
    card_ids = prepare_next_graph_cards(
        str(payload["repertoire_id"]), int(task["generation"]),
        str(payload.get("after_card_id", "")),
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return link_graph_cards_in_transaction(database, task, card_ids)
