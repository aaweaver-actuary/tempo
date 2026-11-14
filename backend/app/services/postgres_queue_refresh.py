"""Bounded PostgreSQL queue-refresh slices with durable phase and cursor state."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from itertools import islice
from typing import Any

from tempo_chess_core import card_id
from tempo_storage.connections import (
    background_read_connection,
    connection,
    read_connection,
)
from tempo_storage.queue_position_lock import (
    lock_queue_date_for_position,
)
from tempo_storage.queue_refresh import (
    admissions,
)
from tempo_storage.queue_refresh import foreground as refresh_requests
from tempo_storage.queue_refresh import (
    openings as openings_storage,
)
from tempo_storage.queue_refresh import (
    presentation as presentation_storage,
)
from tempo_storage.queue_refresh import reads as read_storage
from tempo_storage.tactic_admission import (
    count_daily_tactic_introductions,
    lock_daily_tactic_admission,
)

from app.chess.pgn import ends_on_trained_move

from . import frozen_opening_order, queue_application, queue_ordering
from .activity_gate import activity_gate
from .durable_tasks import (
    accepted_queue_day,
    advance_task_slice_in_transaction,
    complete_task_slice_in_transaction,
    defer_future_queue_slice_in_transaction,
    hold_unknown_queue_day_in_transaction,
    lock_current_slice,
)
from .puzzles import validate_puzzle_record
from .tactical_catalog import pack_records

_ELIGIBILITY_PHASES = (
    "unlock_opening",
    "block_opening",
    "block_defense",
    "restore_due",
    "restore_study",
)
_UNLOCK_BATCH_SIZE = 8
_QUARANTINE_READ_BATCH_SIZE = 32
_OPENING_CANDIDATE_READ_BATCH_SIZE = 16
_TACTICAL_CANDIDATE_READ_BATCH_SIZE = 16
_logger = logging.getLogger(__name__)

# Ordering is a published route occurrence, never an analysis score. The cursor
# advances over rejected occurrences as well as accepted ones.


def _study_opening_content_valid(planned_opening: dict) -> bool:
    """Pure validation of the authored card against its published occurrence."""

    start_fen, moves_json, trained_color, route_fen, route_moves_json = planned_opening[
        "study_route"
    ]["content"]
    try:
        card_moves = json.loads(moves_json)
        route_moves = json.loads(route_moves_json)
        return (
            isinstance(card_moves, list)
            and all(isinstance(move, str) for move in card_moves)
            and card_moves == route_moves
            and card_id(start_fen, card_moves)
            == planned_opening["card_id"]
            == card_id(route_fen, route_moves)
            and ends_on_trained_move(start_fen, card_moves, trained_color)
        )
    except (ValueError, TypeError, AttributeError):
        return False


def _study_opening_cursor(opening_occurrence) -> tuple:
    """Stable repertoire/line/decision order, with IDs only breaking ties."""

    values = tuple(opening_occurrence)
    return (*values[12:13], *values[:6]) if len(values) == 13 else values[:6]


def _study_opening_route_still_valid(database, planned: dict, queue_date: str) -> bool:
    """Recheck the exact current route and exposure at the existing writer."""

    planned_route = planned["study_route"]
    current_occurrence = openings_storage.route_occurrence(
        database,
        planned["repertoire_id"],
        planned_route["generation"],
        planned_route["line_id"],
        planned_route["decision_index"],
        planned["card_id"],
        queue_date,
    )
    if (
        current_occurrence is None
        or list(tuple(current_occurrence)[3:]) != planned_route["content"]
    ):
        return False
    if current_occurrence[1] != "new" or current_occurrence[2] > queue_date:
        return False
    return (
        current_occurrence[0] is None
        or openings_storage.parent_study_review(database, current_occurrence[0])
        is not None
    )


def _prepare_study_opening_page(queue_date: str, payload: dict) -> dict:
    """Read one finite page, then validate chess content with connections closed."""

    with background_read_connection() as frozen_database:
        frozen_token = frozen_opening_order.activation(frozen_database)
    frozen_active = frozen_token[0] is not None
    read_last_occurrences, read_occurrence_page = read_storage.study_opening_queries(
        frozen_active
    )
    has_opening_allowance = read_storage.has_opening_allowance(queue_date)[0][0]
    upper_occurrence_cursor = payload.get("study_opening_upper")
    if not has_opening_allowance:
        return {
            "items": [],
            "cursor": payload.get("study_opening_cursor", []),
            "upper": upper_occurrence_cursor or [],
            "has_more": False,
            "examined": 0,
            "stop_reason": "daily_allowances_exhausted",
        }
    if upper_occurrence_cursor is None:
        last_occurrences = read_last_occurrences()
        upper_occurrence_cursor = (
            (
                list((*tuple(last_occurrences[0])[6:], *tuple(last_occurrences[0])[:6]))
                if frozen_active
                else list(last_occurrences[0])
            )
            if last_occurrences
            else []
        )
    if not upper_occurrence_cursor:
        return {
            "items": [],
            "cursor": [],
            "upper": [],
            "has_more": False,
            "examined": 0,
            "stop_reason": "no_unintroduced_graph_candidates",
        }
    after_occurrence_cursor = payload.get("study_opening_cursor")

    # Each read and result is bounded. No priority publications, probabilities,
    # engine/network calls or repertoire-wide Python accumulation are involved.
    opening_occurrences = read_occurrence_page(
        after_occurrence_cursor,
        upper_occurrence_cursor,
        _OPENING_CANDIDATE_READ_BATCH_SIZE,
    )
    opening_occurrences.sort(key=_study_opening_cursor)
    prepared_openings = []
    selected_card_ids = set()
    planned_counts_by_repertoire = {}
    page_exclusions = {
        "route_ineligible": 0,
        "quota_exhausted": 0,
        "invalid_content": 0,
        "duplicate_occurrence": 0,
    }
    for opening_occurrence in opening_occurrences:
        planned_opening = {
            "repertoire_id": opening_occurrence[1],
            "card_id": opening_occurrence[5],
            "reason": None,
            "study_route": {
                "generation": opening_occurrence[6],
                "line_id": opening_occurrence[3],
                "decision_index": opening_occurrence[4],
                "content": list(tuple(opening_occurrence)[7:12]),
            },
        }
        if opening_occurrence[5] in selected_card_ids:
            page_exclusions["duplicate_occurrence"] += 1
            continue
        # Apply the same authoritative route predicate in a short read section.
        with background_read_connection() as database:
            planned_opening = frozen_opening_order.annotate(
                database, [planned_opening]
            )[0]
            if not frozen_opening_order.proposal_valid(database, planned_opening):
                page_exclusions["invalid_content"] += 1
                continue
            if not _study_opening_route_still_valid(
                database, planned_opening, queue_date
            ):
                page_exclusions["route_ineligible"] += 1
                continue
            remaining_allowance_row = openings_storage.remaining_allowance(
                database, queue_date, opening_occurrence[1]
            )
            if remaining_allowance_row is None or (
                remaining_allowance_row[0]
                <= planned_counts_by_repertoire.get(opening_occurrence[1], 0)
            ):
                page_exclusions["quota_exhausted"] += 1
                continue
        if not _study_opening_content_valid(planned_opening):
            page_exclusions["invalid_content"] += 1
            continue
        selected_card_ids.add(opening_occurrence[5])
        planned_counts_by_repertoire[opening_occurrence[1]] = (
            planned_counts_by_repertoire.get(opening_occurrence[1], 0) + 1
        )
        prepared_openings.append(planned_opening)
    return {
        "items": prepared_openings,
        "cursor": list(_study_opening_cursor(opening_occurrences[-1]))
        if opening_occurrences
        else after_occurrence_cursor or [],
        "upper": upper_occurrence_cursor,
        "has_more": len(opening_occurrences) == _OPENING_CANDIDATE_READ_BATCH_SIZE,
        "examined": len(opening_occurrences),
        "exclusions": page_exclusions,
        "stop_reason": None
        if len(opening_occurrences) == _OPENING_CANDIDATE_READ_BATCH_SIZE
        else "candidate_range_exhausted",
    }


def _prepare_tactical_introduction(queue_date: str) -> dict[str, Any] | None:
    """Choose one puzzle after closing the bounded PostgreSQL read section."""

    limit = read_storage.tactical_allowance()[0][0]
    reserved = read_storage.tactical_reserved(queue_date)[0][0]
    if reserved >= limit:
        return None
    active_packs = sorted(row[0] for row in read_storage.active_tactical_packs())
    if not active_packs:
        return None
    rotation_cursor = read_storage.tactical_rotation()[0][0]
    seen_puzzles = {row[0] for row in read_storage.admitted_tactical_puzzles()}
    ordered_packs = [pack for pack in active_packs if pack > rotation_cursor] + [
        pack for pack in active_packs if pack <= rotation_cursor
    ]
    for pack_id in ordered_packs:
        unseen_records = (
            record
            for record in pack_records(pack_id)
            if record["PuzzleId"] not in seen_puzzles
        )
        while candidate_records := list(
            islice(unseen_records, _TACTICAL_CANDIDATE_READ_BATCH_SIZE)
        ):
            prepared_candidates = []
            for record in candidate_records:
                training_fen, solution = validate_puzzle_record(record)
                prepared_candidates.append(
                    {
                        "pack_id": pack_id,
                        "puzzle_id": record["PuzzleId"],
                        "card_id": card_id(training_fen, solution),
                        "training_fen": training_fen,
                        "solution_json": json.dumps(solution),
                        "source_fen": record["FEN"],
                        "rotation_cursor": rotation_cursor,
                    }
                )
            candidate_ids = [candidate["card_id"] for candidate in prepared_candidates]
            deleted_ids = {
                row[0] for row in read_storage.deleted_candidates(candidate_ids)
            }
            for candidate in prepared_candidates:
                if candidate["card_id"] not in deleted_ids:
                    return candidate
    return None


def _publish_tactical_introduction(
    database, queue_date: str, prepared: dict[str, Any] | None
) -> bool:
    """Commit one reservation, or report that the phase can advance."""

    if prepared is None:
        return False
    admissions.lock_tactical_card_edit(database, prepared["card_id"])
    from ..card_deletion import is_card_deleted

    if is_card_deleted(database, prepared["card_id"]):
        return True
    lock_daily_tactic_admission(database, queue_date)
    limit = admissions.tactical_daily_limit(database)
    reserved = count_daily_tactic_introductions(database, queue_date)
    if reserved >= limit:
        return False
    cursor = admissions.locked_rotation_cursor(database)
    active = admissions.pack_activation(database, prepared["pack_id"])
    if cursor != prepared["rotation_cursor"] or active is None or not active[0]:
        return True
    progress = admissions.locked_puzzle_progress(database, prepared["puzzle_id"])
    if progress is not None and progress[0] is not None:
        return True
    if not admissions.insert_tactical_introduction(
        database, prepared["puzzle_id"], prepared["pack_id"], queue_date
    ):
        return True
    admissions.ensure_tactics_repertoire(database, queue_date)
    admissions.insert_tactical_card(
        database,
        prepared["card_id"],
        prepared["training_fen"],
        prepared["solution_json"],
        queue_date,
        prepared["puzzle_id"],
        prepared["source_fen"],
    )
    admissions.lock_tactical_card_row(database, prepared["card_id"])
    admissions.upsert_tactical_progress(
        database,
        prepared["puzzle_id"],
        prepared["pack_id"],
        prepared["card_id"],
        queue_date,
    )
    queued = admissions.queued_card(database, queue_date, prepared["card_id"])
    if queued is None:
        lock_queue_date_for_position(database, queue_date)
        queued_after_lock = admissions.queued_card(
            database, queue_date, prepared["card_id"]
        )
        if queued_after_lock is None:
            next_position = admissions.next_queue_position(database, queue_date)
            admissions.append_card(
                database, queue_date, prepared["card_id"], next_position
            )
    admissions.set_rotation_cursor(database, prepared["pack_id"])
    return True


def _prepare_unseen_reconciliation(
    queue_date: str, processed_ids: list[int], introduced_counts: dict[str, int]
) -> tuple[dict | None, int]:
    """Read one unprocessed queue entry and its repertoire's starting count."""

    with background_read_connection() as frozen_database:
        if frozen_opening_order.activation(frozen_database)[0] is not None:
            return None, 0
    rows = read_storage.unseen_candidate(queue_date, processed_ids)
    if not rows:
        return None, 0
    candidate = dict(rows[0])
    repertoire_id = candidate["repertoire_id"]
    if repertoire_id in introduced_counts:
        return candidate, introduced_counts[repertoire_id]
    # Eligibility changes must not refund a reviewed historical introduction.
    reviewed_count = read_storage.reviewed_introductions(repertoire_id, queue_date)[0][
        0
    ]
    return candidate, int(reviewed_count)


def _reconcile_one_unseen_entry(
    database, queue_date: str, candidate: dict, introduced_count: int
) -> bool:
    """Apply the original per-repertoire admission rule to one locked entry."""

    current = admissions.locked_unseen_entry(database, candidate["id"], queue_date)
    if current is None:
        return False
    limit = _current_opening_limit(database, candidate["repertoire_id"])
    if limit is None:
        return False
    if introduced_count < limit:
        admissions.keep_unseen_introduction(database, queue_date, candidate["card_id"])
        return True
    admissions.remove_unseen_entry(database, candidate["id"])
    admissions.reset_unseen_introduction(database, candidate["card_id"])
    return False


def _prepare_due_card(queue_date: str) -> str | None:
    rows = read_storage.due_candidate(queue_date)
    return str(rows[0][0]) if rows else None


def _admit_one_due_card(database, queue_date: str, card_id: str) -> bool:
    if admissions.locked_card(database, card_id) is None:
        return False
    lock_queue_date_for_position(database, queue_date)
    eligible = admissions.due_card_eligibility(database, card_id, queue_date)
    if eligible is None:
        return False
    next_position = admissions.next_queue_position(database, queue_date)
    admissions.append_card(database, queue_date, card_id, next_position)
    return True


def _current_opening_limit(database, repertoire_id: str) -> int | None:
    # The slice holds the task lease row. Settings changes enqueue a fresh
    # generation in their write transaction, invalidating any old checkpoint.
    # Do not lock settings rows here: foreground writes take them before the
    # task row, and reversing that order would deadlock the two workers.
    row = openings_storage.opening_limit(database, repertoire_id)
    return int(row[0]) if row else None


def _admit_one_prioritized_opening(database, queue_date: str, planned: dict) -> bool:
    """Publish one still eligible card with the task checkpoint in one transaction."""

    card_id = planned["card_id"]
    if not frozen_opening_order.proposal_valid(database, planned):
        return False
    card = openings_storage.locked_opening_card(database, card_id)
    if (
        card is None
        or card[1] is not None
        or card[2]
        or card[3]
        or card[0] not in ("new", "locked")
    ):
        return False
    if not frozen_opening_order.proposal_valid(database, planned):
        return False
    queued = admissions.queued_card(database, queue_date, card_id)
    if queued is not None:
        return False
    lock_queue_date_for_position(database, queue_date)
    if admissions.queued_card(database, queue_date, card_id):
        return False
    # Recheck inside publication: a foreground settings write may have changed
    # the limit after planning, and another slice may have used the allowance.
    daily_limit = _current_opening_limit(database, planned["repertoire_id"])
    admitted_count = openings_storage.admitted_count(
        database, queue_date, planned["repertoire_id"]
    )
    safe_route = openings_storage.safe_route(
        database, planned["repertoire_id"], card_id
    )
    if safe_route is None:
        return False
    if "study_route" in planned and not _study_opening_route_still_valid(
        database, planned, queue_date
    ):
        return False
    if card[0] != "new":
        return False
    if daily_limit is None or admitted_count >= daily_limit:
        return False
    position = admissions.next_queue_position(database, queue_date)
    openings_storage.append_opening(
        database,
        queue_date,
        card_id,
        position,
        planned["reason"],
        planned["repertoire_id"],
    )
    admissions.keep_unseen_introduction(database, queue_date, card_id)
    return True


def _prepare_study_admission(queue_date: str) -> str | None:
    """Read the allowance and oldest eligible exercise in bounded sections."""

    allowance = int(read_storage.study_allowance()[0][0])
    admitted = int(read_storage.study_admitted(queue_date)[0][0])
    if admitted >= allowance:
        return None
    rows = read_storage.study_candidate(queue_date)
    return str(rows[0][0]) if rows else None


def _admit_one_study_card(database, queue_date: str, study_card_id: str) -> bool:
    """Recheck the quota and candidate while the task and card are locked."""

    if admissions.locked_card(database, study_card_id) is None:
        return False
    lock_queue_date_for_position(database, queue_date)
    allowance = admissions.study_daily_allowance(database)
    admitted = admissions.admitted_study_count(database, queue_date)
    if admitted >= allowance:
        return False
    eligible = admissions.study_card_eligibility(database, study_card_id, queue_date)
    if eligible is None:
        return False
    position = admissions.next_queue_position(database, queue_date)
    admissions.append_study_card(database, queue_date, study_card_id, position)
    return True


def _annotate_frozen_pages(rows):
    """Read active frozen-order facts in bounded, closed sections."""
    annotated = []
    for offset in range(0, len(rows), 16):
        with background_read_connection() as database:
            annotated.extend(
                frozen_opening_order.annotate(database, rows[offset : offset + 16])
            )
    return annotated


def _prepare_queue_randomization(
    queue_date: str, *, preserve_through_entry_id: int | None = None
) -> dict[str, Any]:
    """Capture attempt authority, then compute presentation with connections closed."""
    with background_read_connection() as database:
        frozen_token = list(frozen_opening_order.activation(database))
        saved = presentation_storage.saved_plan(database, queue_date)
    rows = []
    after_entry_id = 0
    while True:
        page = queue_application.presentation_rows(
            None,
            queue_date,
            read=read_storage._bounded_read,
            after_entry_id=after_entry_id,
            limit=16,
        )
        rows.extend(page)
        if len(page) < 16:
            break
        after_entry_id = page[-1]["id"]
    raw_membership_hash = queue_ordering._queue_membership_hash(rows)
    membership_hash = frozen_opening_order.queue_hash(raw_membership_hash, frozen_token)
    captured = {
        "presentation_state": queue_ordering._queue_presentation_state(rows),
        "frozen_token": frozen_token,
        "raw_membership_hash": raw_membership_hash,
        "membership_hash": membership_hash,
    }
    if not rows or (saved and saved["membership_hash"] == membership_hash):
        return {**captured, "entries": None}
    if frozen_token[0] is not None:
        rows = _annotate_frozen_pages(rows)
    seed, _, ordered = queue_ordering._plan_daily_queue_order(
        rows,
        queue_date,
        saved,
        preserve_through_entry_id=preserve_through_entry_id,
    )
    positions = sorted(row["position"] for row in rows)
    return {
        **captured,
        "seed": seed,
        "entries": [
            {
                "id": row["id"],
                "position": positions[index],
                "bucket": row["content_type"] or "opening",
                "kind": row["admission_kind"],
            }
            for index, row in enumerate(ordered)
        ],
    }


def _publish_queue_randomization(database, queue_date: str, plan: dict) -> bool:
    """Atomically reorder the current membership, or request a new plan."""

    if plan.get("retry"):
        return False
    if plan.get("frozen_token", [None, 0]) != list(
        frozen_opening_order.activation(database, lock=True)
    ):
        return False
    lock_queue_date_for_position(database, queue_date)
    if plan.get("presentation_state") != queue_ordering._queue_presentation_state(
        queue_application.presentation_rows(database, queue_date)
    ):
        return False
    current_membership = presentation_storage.current_membership(database, queue_date)
    if queue_ordering._queue_membership_hash(current_membership) != plan.get(
        "raw_membership_hash", plan["membership_hash"]
    ):
        return False
    if plan["entries"] is None:
        return True
    saved = presentation_storage.locked_membership_hash(database, queue_date)
    if saved and saved[0] == plan["membership_hash"]:
        return True
    entries = plan["entries"]
    if plan.get("frozen_token", [None])[0] is not None:
        frozen_opening_order.record_queue_changes(database, queue_date, entries)
    matched_count = presentation_storage.apply_plan(
        database,
        [entry["id"] for entry in entries],
        [entry["position"] for entry in entries],
        [entry["bucket"] for entry in entries],
        [entry["kind"] for entry in entries],
        queue_date,
    )
    if matched_count != len(entries):
        raise RuntimeError("Queue membership changed during atomic randomization")
    presentation_storage.save_plan(
        database,
        queue_date,
        plan["seed"],
        plan["membership_hash"],
        datetime.now(UTC).isoformat(),
    )
    return True


def _prepare_opening_quarantine(queue_date: str, after_entry_id: int) -> dict[str, Any]:
    """Validate a bounded set of opening cards with no database connection open."""

    rows = read_storage.quarantine_page(
        queue_date, after_entry_id, _QUARANTINE_READ_BATCH_SIZE + 1
    )
    inspected = rows[:_QUARANTINE_READ_BATCH_SIZE]
    for candidate_index, candidate in enumerate(inspected):
        if candidate["trained_color"] not in {"white", "black"}:
            continue
        try:
            moves = json.loads(candidate["moves_json"])
        except json.JSONDecodeError:
            moves = []
        if not ends_on_trained_move(
            candidate["start_fen"], moves, candidate["trained_color"]
        ):
            return {
                "after_entry_id": int(candidate["queue_entry_id"]),
                "has_more": candidate_index < len(rows) - 1,
                "invalid": dict(candidate),
            }
    return {
        "after_entry_id": int(inspected[-1]["queue_entry_id"])
        if inspected
        else after_entry_id,
        "has_more": len(rows) > _QUARANTINE_READ_BATCH_SIZE,
        "invalid": None,
    }


def _quarantine_one_opening(database, queue_date: str, candidate: dict) -> bool:
    """Recheck the prepared card under row locks before skipping it."""

    current = presentation_storage.locked_quarantine_card(
        database, queue_date, candidate["queue_entry_id"], candidate["card_id"]
    )
    if current is None or any(
        current[column] != candidate[column]
        for column in ("start_fen", "moves_json", "trained_color")
    ):
        return False
    presentation_storage.lock_incomplete_card(database, candidate["card_id"])
    presentation_storage.skip_incomplete_entry(database, candidate["queue_entry_id"])
    presentation_storage.record_incomplete_diagnostic(
        database,
        queue_date,
        candidate["card_id"],
        "Skipped an incomplete opening card. Edit or re-import its line to study it.",
    )
    return True


def _prepare_projection_blocked_count(queue_date: str) -> int:
    return int(read_storage.projection_blocked_count(queue_date)[0][0])


def _queue_slice_current_day(database):
    """Lock settings first, matching settings.update's settings-before-task order."""
    from app.study.policy.clock import study_calendar_at

    timezone_name = refresh_requests.lock_study_timezone(database)
    if timezone_name is None:
        raise RuntimeError("Current Study policy is unavailable")
    now = datetime.now(UTC)
    return now, study_calendar_at(now, timezone_name).day.isoformat()


def _admit_current_day_slice(database, task, queue_date):
    now, current_day = _queue_slice_current_day(database)
    if not lock_current_slice(database, task):
        return False
    if queue_date > current_day:
        defer_future_queue_slice_in_transaction(database, task, now=now)
        return False
    return True


def execute_postgres_queue_refresh_slice(task: dict[str, Any]) -> bool:
    """Apply one eligibility phase; later queue phases remain explicitly gated."""

    payload = task["payload"]
    queue_date = accepted_queue_day(payload)
    if queue_date is None:
        # Never prepare or publish a slice using a newly inferred day. This
        # transaction fences the original generation and preserves its payload.
        with connection(background=True) as database:
            if lock_current_slice(database, task):
                hold_unknown_queue_day_in_transaction(database, task)
        return False
    # Pin before preparing any work. An upgraded checkpoint cannot certify that
    # its earlier slices covered requests accepted before epoch tracking existed.
    if "_refresh_cycle_epoch" not in payload:
        with connection(background=True) as database:
            if not _admit_current_day_slice(database, task, queue_date):
                return False
            epochs = refresh_requests.lock_refresh_epochs(database, queue_date)
            epoch = (
                int(epochs[0])
                if epochs is not None
                else refresh_requests.signal_queue_refresh_in_transaction(
                    database, queue_date
                )
            )
            return advance_task_slice_in_transaction(
                database,
                task,
                next_phase="unlock_opening",
                next_payload={
                    "queue_date": queue_date,
                    "_queue_phase": "unlock_opening",
                    "_refresh_cycle_epoch": epoch,
                },
            )
    cycle_epoch = int(payload["_refresh_cycle_epoch"])
    phase = str(payload.get("_queue_phase") or _ELIGIBILITY_PHASES[0])
    if phase not in (
        *_ELIGIBILITY_PHASES,
        "tactical_introductions",
        "reset_opening_new",
        "reset_opening_stale",
        "reconcile_unseen",
        "admit_due",
        "admit_position_due",
        "prioritized_openings",
        "prioritized_opening_item",
        "study_opening_page",
        "study_opening_item",
        "admit_study",
        "real_game_misses",
        "randomize_queue",
        "quarantine",
        "publish_projection",
    ):
        raise RuntimeError(f"Queue refresh phase is not yet ported: {phase}")
    with background_read_connection() as frozen_database:
        frozen_token = list(frozen_opening_order.activation(frozen_database))
    if payload.get("frozen_token", [None, 0]) != frozen_token:
        payload = {
            "queue_date": queue_date,
            "_queue_phase": "unlock_opening",
            "frozen_token": frozen_token,
            "_refresh_cycle_epoch": cycle_epoch,
            **(
                {"preserve_through_entry_id": payload["preserve_through_entry_id"]}
                if "preserve_through_entry_id" in payload
                else {}
            ),
        }
        phase = payload["_queue_phase"]
    # Resume existing checkpoints without traversing optional admission/ranking.
    skipped_phases = {
        "prioritized_openings": "study_opening_page",
        "prioritized_opening_item": "study_opening_page",
        "real_game_misses": "randomize_queue",
    }
    if phase in skipped_phases:
        phase = skipped_phases[phase]
    # Old optional-priority cursors had a leading miss rank. Restart the
    # authored scan; existing queue identities exclude already admitted work.
    if any(
        len(payload.get(key) or ()) == 8
        for key in ("study_opening_cursor", "study_opening_upper")
    ):
        payload = {
            key: value
            for key, value in payload.items()
            if not key.startswith("study_opening_")
        }
        if phase in {"study_opening_page", "study_opening_item"}:
            phase = "study_opening_page"
    study_opening_page = None
    if phase == "study_opening_page":
        activity_gate.wait_for_foreground()
        study_opening_page = _prepare_study_opening_page(queue_date, payload)
    prepared_tactic = None
    if phase == "tactical_introductions":
        activity_gate.wait_for_foreground()
        prepared_tactic = _prepare_tactical_introduction(queue_date)
    processed_ids = [int(identifier) for identifier in payload.get("processed_ids", [])]
    introduced_counts = {
        str(identifier): int(count)
        for identifier, count in payload.get("introduced_counts", {}).items()
    }
    reconciliation_candidate = None
    starting_count = 0
    if phase == "reconcile_unseen":
        activity_gate.wait_for_foreground()
        reconciliation_candidate, starting_count = _prepare_unseen_reconciliation(
            queue_date,
            processed_ids,
            introduced_counts,
        )
    due_card_id = None
    if phase == "admit_due":
        activity_gate.wait_for_foreground()
        due_card_id = _prepare_due_card(queue_date)
    position_due = None
    if phase == "admit_position_due":
        activity_gate.wait_for_foreground()
        from .position_queue_admission import prepare_due

        with read_connection(repertoire_snapshot=True) as database:
            position_due = prepare_due(database, queue_date, payload)
    position_introduction = None
    planned_introduction = None
    if phase == "study_opening_item":
        planned_introduction = payload["study_opening_items"][
            int(payload["study_opening_index"])
        ]
    if planned_introduction is not None:
        from .position_queue_admission import prepare_opening_introduction

        activity_gate.wait_for_foreground()
        with read_connection(repertoire_snapshot=True) as database:
            position_introduction = prepare_opening_introduction(
                database, planned_introduction
            )
    study_card_id = None
    if phase == "admit_study":
        activity_gate.wait_for_foreground()
        study_card_id = _prepare_study_admission(queue_date)
    randomization_plan = None
    if phase == "randomize_queue":
        activity_gate.wait_for_foreground()
        randomization_plan = _prepare_queue_randomization(
            queue_date,
            preserve_through_entry_id=payload.get("preserve_through_entry_id"),
        )
    quarantine_batch = None
    if phase == "quarantine":
        activity_gate.wait_for_foreground()
        quarantine_batch = _prepare_opening_quarantine(
            queue_date,
            int(payload.get("after_entry_id") or 0),
        )
    blocked_count = None
    if phase == "publish_projection":
        activity_gate.wait_for_foreground()
        blocked_count = _prepare_projection_blocked_count(queue_date)
    activity_gate.wait_for_foreground()
    with connection(
        background=True,
        **({"queue_refresh_publication": True} if phase == "randomize_queue" else {}),
    ) as database:
        if not _admit_current_day_slice(database, task, queue_date):
            return False
        if frozen_token != list(frozen_opening_order.activation(database, lock=True)):
            return advance_task_slice_in_transaction(
                database,
                task,
                next_phase="unlock_opening",
                next_payload={
                    "queue_date": queue_date,
                    "_queue_phase": "unlock_opening",
                    "_refresh_cycle_epoch": cycle_epoch,
                },
            )
        position_introduction_fence = None
        introduction_history_hold = False
        if planned_introduction is not None:
            from tempo_evidence_math.position_learning import Hold

            from .position_queue_admission import fence_opening_introduction

            if isinstance(position_introduction, Hold):
                raise RuntimeError(
                    "Position introduction held: " + position_introduction.reason
                )
            position_introduction_fence = fence_opening_introduction(
                database, position_introduction
            )
            if isinstance(position_introduction_fence, Hold):
                if (
                    position_introduction_fence.reason
                    == "position_introduction_history_changed"
                    and position_introduction_fence.detail
                ):
                    from tempo_storage.study.position_introductions import (
                        record_history_hold,
                    )

                    record_history_hold(
                        database, position_introduction[1], position_introduction_fence
                    )
                    introduction_history_hold = True
                else:
                    raise RuntimeError(
                        "Position introduction held: "
                        + position_introduction_fence.reason
                    )
        if phase == "publish_projection":
            epochs = refresh_requests.lock_refresh_epochs(database, queue_date)
            if epochs is None:
                raise RuntimeError("Queue refresh request epoch is missing")
            requested_epoch = int(epochs[0])
            if requested_epoch != cycle_epoch:
                if requested_epoch < cycle_epoch:
                    raise RuntimeError(
                        "Queue refresh cycle is ahead of its durable request epoch"
                    )
                # Coalesce every late request into one complete follow-up. This
                # checkpoint releases the lease using the existing post-commit
                # worker continuation; no readiness is published in between.
                return advance_task_slice_in_transaction(
                    database,
                    task,
                    next_phase="unlock_opening",
                    next_payload={
                        "queue_date": queue_date,
                        "_queue_phase": "unlock_opening",
                        "_refresh_cycle_epoch": requested_epoch,
                    },
                )
            if not refresh_requests.publish_refresh_epoch_if_current(
                database, queue_date, cycle_epoch
            ):
                raise RuntimeError("Queue refresh publication lost its request epoch")
            presentation_storage.publish_projection(
                database,
                queue_date,
                blocked_count,
                datetime.now(UTC).isoformat(),
            )
            if not complete_task_slice_in_transaction(database, task):
                raise RuntimeError("Queue projection publication lost its task lease")
            if "study_opening_counts" in payload:
                _logger.info(
                    "study_opening_admission %s",
                    json.dumps(
                        {
                            "queue_date": queue_date,
                            **payload["study_opening_counts"],
                        },
                        sort_keys=True,
                    ),
                )
            return False
        if phase == "unlock_opening":
            next_cursor = queue_application._unlock_eligible_opening_cards(
                database,
                queue_date,
                after_card_id=str(payload.get("after_card_id") or ""),
                batch_size=_UNLOCK_BATCH_SIZE,
            )
            if next_cursor is not None:
                next_phase = phase
                next_payload = {
                    "queue_date": queue_date,
                    "_queue_phase": phase,
                    "after_card_id": next_cursor,
                }
            else:
                next_phase = _ELIGIBILITY_PHASES[1]
                next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "tactical_introductions":
            more_tactics = _publish_tactical_introduction(
                database,
                queue_date,
                prepared_tactic,
            )
            next_phase = phase if more_tactics else "reset_opening_new"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "reset_opening_new":
            queue_application._reset_unintroduced_opening_cards(database, queue_date)
            next_phase = "reset_opening_stale"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "reset_opening_stale":
            queue_application._reset_stale_opening_introductions(database, queue_date)
            next_phase = "reconcile_unseen"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "reconcile_unseen":
            if reconciliation_candidate is None:
                next_phase = "admit_due"
                next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
            else:
                kept = _reconcile_one_unseen_entry(
                    database,
                    queue_date,
                    reconciliation_candidate,
                    starting_count,
                )
                processed_ids.append(int(reconciliation_candidate["id"]))
                repertoire_id = str(reconciliation_candidate["repertoire_id"])
                introduced_counts[repertoire_id] = starting_count + int(kept)
                next_phase = phase
                next_payload = {
                    "queue_date": queue_date,
                    "_queue_phase": phase,
                    "processed_ids": processed_ids,
                    "introduced_counts": introduced_counts,
                }
        elif phase == "admit_due":
            if due_card_id is None:
                next_phase = "admit_position_due"
            else:
                _admit_one_due_card(database, queue_date, due_card_id)
                next_phase = phase
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "admit_position_due":
            from tempo_evidence_math.position_learning import Hold

            from .position_queue_admission import admit_due

            if isinstance(position_due, Hold):
                raise RuntimeError(
                    "Position queue admission held: " + position_due.reason
                )
            if position_due is None:
                next_phase = "study_opening_page"
                next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
            elif "repertoire_done" in position_due:
                next_phase = phase
                next_payload = {
                    "queue_date": queue_date,
                    "_queue_phase": phase,
                    "position_repertoire_after": position_due["repertoire_done"],
                }
            else:
                held = admit_due(database, queue_date, position_due)
                if isinstance(held, Hold):
                    raise RuntimeError("Position queue admission held: " + held.reason)
                next_phase = phase
                next_payload = {
                    "queue_date": queue_date,
                    "_queue_phase": phase,
                    **position_due["cursor"],
                }
        elif phase == "study_opening_page":
            counts = dict(payload.get("study_opening_counts", {}))
            counts["examined"] = (
                counts.get("examined", 0) + study_opening_page["examined"]
            )
            counts["eligible"] = counts.get("eligible", 0) + len(
                study_opening_page["items"]
            )
            counts["excluded_or_no_allowance"] = counts["examined"] - counts["eligible"]
            counts["stop_reason"] = study_opening_page["stop_reason"]
            for reason, excluded_count in study_opening_page.get(
                "exclusions", {}
            ).items():
                counts[reason] = counts.get(reason, 0) + excluded_count
            next_phase = (
                "study_opening_item"
                if study_opening_page["items"]
                else "study_opening_page"
                if study_opening_page["has_more"]
                else "admit_study"
            )
            next_payload = {
                "queue_date": queue_date,
                "_queue_phase": next_phase,
                "study_opening_cursor": study_opening_page["cursor"],
                "study_opening_upper": study_opening_page["upper"],
                "study_opening_more": study_opening_page["has_more"],
                "study_opening_items": study_opening_page["items"],
                "study_opening_index": 0,
                "study_opening_counts": counts,
            }
        elif phase == "study_opening_item":
            items = payload["study_opening_items"]
            item_index = int(payload["study_opening_index"])
            from .position_queue_admission import redundant_opening_introduction

            admitted = (
                False
                if (
                    introduction_history_hold
                    or redundant_opening_introduction(
                        position_introduction, position_introduction_fence
                    )
                )
                else _admit_one_prioritized_opening(
                    database, queue_date, items[item_index]
                )
            )
            if admitted and position_introduction is not None:
                from .position_queue_admission import apply_opening_introduction

                apply_opening_introduction(
                    database,
                    position_introduction,
                    position_introduction_fence,
                    now=datetime.now(UTC),
                )
            counts = dict(payload["study_opening_counts"])
            counts["history_held"] = counts.get("history_held", 0) + int(
                introduction_history_hold
            )
            counts["admitted"] = counts.get("admitted", 0) + int(bool(admitted))
            counts["publication_rejected"] = counts.get(
                "publication_rejected", 0
            ) + int(not admitted)
            item_index += 1
            next_phase = (
                "study_opening_item"
                if item_index < len(items)
                else "study_opening_page"
                if payload["study_opening_more"]
                else "admit_study"
            )
            next_payload = {
                **payload,
                "_queue_phase": next_phase,
                "study_opening_index": item_index,
                "study_opening_counts": counts,
            }
            if next_phase != "study_opening_item":
                next_payload.pop("study_opening_items")
                next_payload.pop("study_opening_index")
        elif phase == "admit_study":
            if study_card_id is None:
                next_phase = "randomize_queue"
            else:
                _admit_one_study_card(database, queue_date, study_card_id)
                next_phase = phase
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "randomize_queue":
            published = _publish_queue_randomization(
                database,
                queue_date,
                randomization_plan,
            )
            next_phase = "quarantine" if published else phase
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
            for checkpoint in ("quarantine_started", "after_entry_id"):
                if checkpoint in payload:
                    next_payload[checkpoint] = payload[checkpoint]
        elif phase == "quarantine":
            if not payload.get("quarantine_started"):
                presentation_storage.clear_diagnostics(database, queue_date)
            if quarantine_batch["invalid"] is not None:
                _quarantine_one_opening(
                    database,
                    queue_date,
                    quarantine_batch["invalid"],
                )
            next_phase = phase if quarantine_batch["has_more"] else "publish_projection"
            next_payload = {
                "queue_date": queue_date,
                "_queue_phase": next_phase,
                "quarantine_started": True,
                "after_entry_id": quarantine_batch["after_entry_id"],
            }
        else:
            phase_handlers = {
                "block_opening": queue_application._block_ineligible_opening_queue_entries,
                "block_defense": queue_application._block_unapproved_defense_queue_entries,
                "restore_due": queue_application._restore_eligible_due_queue_entries,
                "restore_study": queue_application._restore_published_study_queue_entries,
            }
            phase_handlers[phase](database, queue_date)
            phase_index = _ELIGIBILITY_PHASES.index(phase)
            next_phase = (
                _ELIGIBILITY_PHASES[phase_index + 1]
                if phase_index + 1 < len(_ELIGIBILITY_PHASES)
                else "tactical_introductions"
            )
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        if "preserve_through_entry_id" in payload:
            next_payload["preserve_through_entry_id"] = payload[
                "preserve_through_entry_id"
            ]
        next_payload["_refresh_cycle_epoch"] = cycle_epoch
        if (
            "study_opening_counts" in payload
            and "study_opening_counts" not in next_payload
        ):
            next_payload["study_opening_counts"] = payload["study_opening_counts"]
        if frozen_token != [None, 0]:
            next_payload["frozen_token"] = frozen_token
        return advance_task_slice_in_transaction(
            database,
            task,
            next_phase=next_phase,
            next_payload=next_payload,
        )
