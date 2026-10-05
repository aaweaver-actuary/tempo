"""Per-line PostgreSQL opening-graph preparation for durable rebuild slices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from ..database import background_read_connection
from .. import postgres_store
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, lock_current_slice,
)
from .opening_graph import GraphInput, GraphStep, build_graph
from .redis_admission_gate import background_lease


_STEP_BATCH_SIZE = 8
_CLEANUP_BATCH_SIZE = 2


def request_graph_rebuild_in_transaction(
    database: postgres_store.PostgresConnection, repertoire_id: str, local_day: str,
) -> dict[str, Any]:
    """Enqueue a new graph generation after all imported and staged generations."""

    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:opening-graph:{repertoire_id}",),
    )
    latest_staged = database.execute_native(
        "SELECT generation FROM opening_graph_steps WHERE repertoire_id=%s "
        "ORDER BY generation DESC LIMIT 1", (repertoire_id,),
    ).fetchone()
    latest_published = database.execute_native(
        "SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s",
        (repertoire_id,),
    ).fetchone()
    minimum_generation = max(
        int(latest_staged[0]) if latest_staged else 0,
        int(latest_published[0]) if latest_published else 0,
    )
    return enqueue_task_in_transaction(
        database, "opening_graph_rebuild", repertoire_id,
        {"repertoire_id": repertoire_id, "local_day": local_day,
         "after_line_id": "", "step_offset": 0, "after_card_id": ""},
        priority=40, minimum_generation=minimum_generation,
    )


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
            "due_date,content_type,trained_color,pending_validation,canonical_route_source) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,'opening',%s,0,0) ON CONFLICT(id) DO NOTHING",
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
    repertoire_id: str, generation: int, after_card_id: str, *, limit: int = 8,
) -> tuple[str, ...]:
    """Read a bounded set of distinct staged cards under the admission gate."""

    with background_read_connection() as database:
        cards = database.execute_native(
            "SELECT card_id FROM opening_graph_steps WHERE repertoire_id=%s "
            "AND generation=%s AND card_id>%s GROUP BY card_id ORDER BY card_id LIMIT %s",
            (repertoire_id, generation, after_card_id, limit),
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
            "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) "
            "SELECT %s,%s,0 WHERE EXISTS(SELECT 1 FROM opening_graph_steps "
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


def publish_graph_in_transaction(
    database: postgres_store.PostgresConnection, task: dict[str, Any],
) -> bool:
    """Change the visible generation only after every staged card is linked."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    repertoire_id = str(payload["repertoire_id"])
    generation = int(task["generation"])
    missing_link = database.execute_native(
        "SELECT 1 FROM opening_graph_steps step WHERE step.repertoire_id=%s "
        "AND step.generation=%s AND NOT EXISTS("
        "SELECT 1 FROM repertoire_cards link WHERE link.repertoire_id=step.repertoire_id "
        "AND link.card_id=step.card_id) LIMIT 1",
        (repertoire_id, generation),
    ).fetchone()
    if missing_link:
        raise RuntimeError("Opening graph generation has unlinked cards")
    database.execute_native(
        "INSERT INTO opening_graph_publications(repertoire_id,generation,state,last_error,published_at) "
        "VALUES(%s,%s,'ready',NULL,%s) ON CONFLICT(repertoire_id) DO UPDATE SET "
        "generation=excluded.generation,state='ready',last_error=NULL,"
        "published_at=excluded.published_at",
        (repertoire_id, generation, datetime.now(timezone.utc).isoformat()),
    )
    return advance_task_slice_in_transaction(
        database, task, next_phase="classify",
        next_payload={**payload, "after_card_id": ""},
    )


def execute_graph_publish_slice(task: dict[str, Any]) -> bool:
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return publish_graph_in_transaction(database, task)


def classify_graph_cards_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    card_ids: tuple[str, ...],
) -> bool:
    """Refresh at most eight cards against the newly published graph."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if not card_ids:
        return advance_task_slice_in_transaction(
            database, task, next_phase="integrity",
            next_payload={**payload, "after_card_id": ""},
        )
    study_day = str(payload["local_day"])
    for card_id in card_ids:
        classification = database.execute_native(
            "SELECT BOOL_OR(step.segment_kind='prefix'),"
            "BOOL_OR(step.parent_card_id IS NULL) "
            "FROM opening_graph_steps step "
            "JOIN opening_graph_publications publication "
            "ON publication.repertoire_id=step.repertoire_id "
            "AND publication.generation=step.generation "
            "WHERE step.card_id=%s",
            (card_id,),
        ).fetchone()
        if classification is None or classification[0] is None:
            raise RuntimeError("Published graph card disappeared before classification")
        kind = "prefix" if classification[0] else "response"
        next_state = "new" if classification[1] else "locked"
        database.execute_native(
            "UPDATE cards SET archived=0,kind=%s,"
            "state=CASE WHEN state IN ('new','locked') AND introduced_at IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM reviews WHERE card_id=cards.id) "
            "THEN %s ELSE state END,"
            "due_date=CASE WHEN %s='new' AND state='locked' "
            "AND introduced_at IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM reviews WHERE card_id=cards.id) "
            "THEN %s ELSE due_date END WHERE id=%s AND canonical_route_source=0",
            (kind, next_state, next_state, study_day, card_id),
        )
    return advance_task_slice_in_transaction(
        database, task, next_phase="classify",
        next_payload={**payload, "after_card_id": card_ids[-1]},
    )


def execute_graph_classify_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    card_ids = prepare_next_graph_cards(
        str(payload["repertoire_id"]), int(task["generation"]),
        str(payload.get("after_card_id", "")),
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return classify_graph_cards_in_transaction(database, task, card_ids)


def refresh_graph_integrity_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    card_ids: tuple[str, ...],
) -> bool:
    """Rebuild integrity blocks for two published cards and update validation."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if not card_ids:
        return advance_task_slice_in_transaction(
            database, task, next_phase="cleanup",
            next_payload={**payload, "after_card_id": ""},
        )
    repertoire_id = str(payload["repertoire_id"])
    generation = int(task["generation"])
    now = datetime.now(timezone.utc).isoformat()
    for card_id in card_ids:
        database.execute_native(
            "DELETE FROM repertoire_integrity_card_blocks "
            "WHERE repertoire_id=%s AND card_id=%s", (repertoire_id, card_id),
        )
        database.execute_native(
            "INSERT INTO repertoire_integrity_card_blocks("
            "repertoire_id,card_id,issue_id,scan_generation,published_at) "
            "SELECT DISTINCT %s,%s,issue.id,%s,%s FROM opening_graph_steps step "
            "CROSS JOIN LATERAL jsonb_array_elements_text("
            "step.decision_fen_keys_json::jsonb) position(fen_key) "
            "JOIN repertoire_integrity_issues issue "
            "ON issue.repertoire_id=step.repertoire_id AND issue.fen_key=position.fen_key "
            "WHERE step.repertoire_id=%s AND step.generation=%s AND step.card_id=%s "
            "ON CONFLICT(repertoire_id,card_id,issue_id) DO NOTHING",
            (repertoire_id, card_id, f"graph:{generation}", now,
             repertoire_id, generation, card_id),
        )
        database.execute_native(
            "UPDATE cards SET pending_validation=CASE WHEN EXISTS("
            "SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.card_id=cards.id) THEN 1 ELSE 0 END WHERE id=%s",
            (card_id,),
        )
    return advance_task_slice_in_transaction(
        database, task, next_phase="integrity",
        next_payload={**payload, "after_card_id": card_ids[-1]},
    )


def execute_graph_integrity_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    card_ids = prepare_next_graph_cards(
        str(payload["repertoire_id"]), int(task["generation"]),
        str(payload.get("after_card_id", "")), limit=2,
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return refresh_graph_integrity_in_transaction(database, task, card_ids)


def prepare_obsolete_graph_cards(
    repertoire_id: str, generation: int, after_card_id: str,
) -> tuple[str, ...]:
    """Find at most two old opening cards absent from the published generation."""

    with background_read_connection() as database:
        rows = database.execute_native(
            "SELECT link.card_id FROM repertoire_cards link "
            "JOIN cards card ON card.id=link.card_id "
            "WHERE link.repertoire_id=%s AND link.card_id>%s "
            "AND card.content_type='opening' AND link.canonical_route_source=0 "
            "AND NOT EXISTS(SELECT 1 FROM opening_graph_steps step "
            "WHERE step.repertoire_id=%s AND step.generation=%s "
            "AND step.card_id=link.card_id) "
            "ORDER BY link.card_id LIMIT %s",
            (repertoire_id, after_card_id, repertoire_id, generation, _CLEANUP_BATCH_SIZE),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def cleanup_graph_cards_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    card_ids: tuple[str, ...],
) -> bool:
    """Remove at most two old links, preserving cards shared by other repertoires."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if not card_ids:
        return advance_task_slice_in_transaction(
            database, task, next_phase="finalize", next_payload=payload,
        )
    repertoire_id = str(payload["repertoire_id"])
    generation = int(task["generation"])
    for card_id in card_ids:
        # Selection happens before this write slice. An intervening foreground
        # adoption must protect the now-authored membership and its queue.
        membership = database.execute_native(
            "SELECT card.canonical_route_source,card.repertoire_id FROM repertoire_cards link "
            "JOIN cards card ON card.id=link.card_id "
            "WHERE link.repertoire_id=%s AND link.card_id=%s "
            "AND link.canonical_route_source=0 FOR UPDATE OF link,card",
            (repertoire_id, card_id),
        ).fetchone()
        if membership is None:
            continue
        still_current = database.execute_native(
            "SELECT 1 FROM opening_graph_steps WHERE repertoire_id=%s "
            "AND generation=%s AND card_id=%s LIMIT 1",
            (repertoire_id, generation, card_id),
        ).fetchone()
        if still_current:
            continue
        if not membership[0] or membership[1] == repertoire_id:
            database.execute_native(
                "UPDATE daily_queue SET status='superseded' "
                "WHERE card_id=%s AND status='queued' "
                "AND NOT EXISTS(SELECT 1 FROM repertoire_cards retained "
                "WHERE retained.card_id=%s AND retained.repertoire_id<>%s)",
                (card_id, card_id, repertoire_id),
            )
        # A globally authored card with only this generated owner association
        # has no effective authored scope. Retire it before deleting that link;
        # keep its row and history without activating unlinked owner fallback.
        database.execute_native(
            "UPDATE cards SET archived=1 WHERE id=%s AND repertoire_id=%s "
            "AND NOT EXISTS(SELECT 1 FROM repertoire_cards retained "
            "WHERE retained.card_id=cards.id AND retained.repertoire_id<>%s)",
            (card_id, repertoire_id, repertoire_id),
        )
        # Move a retained card's owner while the explicit generated membership
        # still suppresses fallback in the departing repertoire.
        database.execute_native(
            "UPDATE cards SET repertoire_id=("
            "SELECT MIN(retained.repertoire_id) FROM repertoire_cards retained "
            "WHERE retained.card_id=cards.id AND retained.repertoire_id<>%s) "
            "WHERE id=%s AND repertoire_id=%s "
            "AND EXISTS(SELECT 1 FROM repertoire_cards retained "
            "WHERE retained.card_id=cards.id AND retained.repertoire_id<>%s)",
            (repertoire_id, card_id, repertoire_id, repertoire_id),
        )
        database.execute_native(
            "DELETE FROM repertoire_cards WHERE repertoire_id=%s AND card_id=%s",
            (repertoire_id, card_id),
        )
        database.execute_native(
            "DELETE FROM repertoire_integrity_card_blocks "
            "WHERE repertoire_id=%s AND card_id=%s", (repertoire_id, card_id),
        )
        database.execute_native(
            "UPDATE cards SET archived=1 WHERE id=%s AND canonical_route_source=0 "
            "AND NOT EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=cards.id)",
            (card_id,),
        )
    return advance_task_slice_in_transaction(
        database, task, next_phase="cleanup",
        next_payload={**payload, "after_card_id": card_ids[-1]},
    )


def execute_graph_cleanup_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    card_ids = prepare_obsolete_graph_cards(
        str(payload["repertoire_id"]), int(task["generation"]),
        str(payload.get("after_card_id", "")),
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return cleanup_graph_cards_in_transaction(database, task, card_ids)


def finalize_graph_in_transaction(
    database: postgres_store.PostgresConnection, task: dict[str, Any],
) -> bool:
    """Queue the foreground-visible refresh and complete this graph generation."""

    if not lock_current_slice(database, task):
        return False
    from .postgres_integrity import request_integrity_scan_in_transaction

    request_integrity_scan_in_transaction(
        database, str(task["payload"]["repertoire_id"]),
        int(task["generation"]), str(task["payload"]["local_day"]),
    )
    if not complete_task_slice_in_transaction(database, task):
        raise RuntimeError("Opening graph lease changed before finalization")
    return False


def execute_graph_finalize_slice(task: dict[str, Any]) -> bool:
    from .durable_tasks import warm_completion_sql

    warm_completion_sql()
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return finalize_graph_in_transaction(database, task)


def execute_postgres_opening_graph_slice(task: dict[str, Any]) -> bool:
    """Run exactly one durable graph phase, yielding to foreground afterward."""

    phase = task.get("phase", "queued")
    handlers = {
        "queued": execute_graph_stage_slice,
        "stage": execute_graph_stage_slice,
        "link": execute_graph_link_slice,
        "publish": execute_graph_publish_slice,
        "classify": execute_graph_classify_slice,
        "integrity": execute_graph_integrity_slice,
        "cleanup": execute_graph_cleanup_slice,
        "finalize": execute_graph_finalize_slice,
    }
    handler = handlers.get(phase)
    if handler is None:
        raise ValueError(f"Unknown PostgreSQL opening graph phase: {phase}")
    return handler(task)
