"""Position-level integrity checks and repairs for opening repertoires."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import sqlite3
import uuid

import chess

from .repertoire_game_refresh import refresh_game_publications_after_mutation
from tempo_legacy_database.adapters.authored_repertoires.integrity import storage
from tempo_legacy_database.connections import card_columns, read_connection
from tempo_legacy_database.adapters.database_executor import submit_background_write

from app.chess.cards import card_id
from .repertoire_comparison import canonical_fen

from ..card_deletion import SYSTEM_REPERTOIRE_IDS
SYSTEM_REPERTOIRES = set(SYSTEM_REPERTOIRE_IDS)
_LOGGER = logging.getLogger("tempo.background")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue_id(repertoire_id: str, kind: str, fen_key: str | None, sources: list[dict] | None = None) -> str:
    source_key = "|".join(
        f"{source.get('type')}:{source.get('id')}"
        for source in (sources or [])
    )
    return hashlib.sha256(
        f"{repertoire_id}\0{kind}\0{fen_key or source_key}".encode()
    ).hexdigest()


def _signature(issue: dict) -> str:
    payload = {
        "kind": issue["kind"],
        "fen_key": issue.get("fen_key"),
        "moves": sorted(issue.get("moves", [])),
        "sources": sorted(
            [
                (source.get("type"), source.get("id"), source.get("move_index"), source.get("move"))
                for source in issue.get("sources", [])
            ],
            key=repr,
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _source_rows(database: sqlite3.Connection, repertoire_id: str) -> list[dict]:
    lines = [
        {**dict(row), "source_type": "line", "source_id": row["id"]}
        for row in storage.source_lines(database, (repertoire_id,))
    ]
    cards = [
        {**dict(row), "source_type": "card", "source_id": row["id"]}
        for row in storage.source_cards(database, (repertoire_id, repertoire_id, repertoire_id))
    ]
    return lines + cards


def _scan_source(
    source: dict, repertoire_color: str | None
) -> tuple[dict[str, dict], list[dict]]:
    positions: dict[str, dict] = {}
    invalid: list[dict] = []
    source_label = f"{source['source_type']} {source['source_id']}"
    try:
        board = chess.Board(source["start_fen"])
        moves = json.loads(source["moves_json"])
        if not isinstance(moves, list):
            raise ValueError("moves_json must be a list")
        target = chess.WHITE if source["trained_color"] == "white" else chess.BLACK
        if source["trained_color"] not in {"white", "black"}:
            raise ValueError("trained color is unknown")
        if repertoire_color is not None and source["trained_color"] != repertoire_color:
            raise ValueError(
                f"trained color {source['trained_color']} does not match repertoire color {repertoire_color}"
            )
        last_moving_color = None
        for move_index, move_value in enumerate(moves):
            if board.turn == target:
                _record_position(positions, board, source, move_index, str(move_value))
            move = chess.Move.from_uci(str(move_value).lower())
            if move not in board.legal_moves:
                raise ValueError(f"illegal move at ply {move_index + 1}")
            last_moving_color = board.turn
            board.push(move)
        if board.turn == target and any(board.legal_moves) and last_moving_color != target:
            _record_position(positions, board, source, len(moves), None)
    except (TypeError, ValueError, json.JSONDecodeError, KeyError) as error:
        invalid.append(
            {
                "kind": "invalid_source",
                "fen_key": None,
                "fen": None,
                "trained_color": source.get("trained_color"),
                "moves": [],
                "sources": [
                    {
                        "type": source["source_type"],
                        "id": source["source_id"],
                        "label": source_label,
                        "error": str(error),
                    }
                ],
            }
        )
    return positions, invalid


def _scan_sources(database: sqlite3.Connection, repertoire_id: str) -> list[dict]:
    positions: dict[str, dict] = {}
    invalid: list[dict] = []
    repertoire_color: str | None = None
    for source in _source_rows(database, repertoire_id):
        source_positions, source_invalid = _scan_source(source, repertoire_color)
        if repertoire_color is None and source.get("trained_color") in {"white", "black"}:
            repertoire_color = source["trained_color"]
        for key, value in source_positions.items():
            if key not in positions:
                positions[key] = value
            else:
                positions[key]["moves"].extend(value["moves"])
                positions[key]["sources"].extend(value["sources"])
        invalid.extend(source_invalid)
    issues: list[dict] = []
    for value in positions.values():
        move_set = set(value["moves"])
        if len(move_set) == 1:
            continue
        value["kind"] = "missing_response" if not move_set else "multiple_responses"
        issues.append(value)
    for issue in invalid:
        issue["id"] = _issue_id(repertoire_id, issue["kind"], None, issue.get("sources"))
        issue["signature"] = _signature(issue)
        issues.append(issue)
    for issue in issues:
        issue.setdefault("id", _issue_id(repertoire_id, issue["kind"], issue.get("fen_key"), issue.get("sources")))
        issue.setdefault("signature", _signature(issue))
    return sorted(
        issues,
        key=lambda item: (item.get("fen_key") or "", item["kind"], item["id"]),
    )


def _record_position(
    positions: dict[str, dict],
    board: chess.Board,
    source: dict,
    move_index: int,
    move: str | None,
) -> None:
    fen_key = canonical_fen(board.fen())
    entry = positions.setdefault(
        fen_key,
        {
            "fen_key": fen_key,
            "fen": board.fen(),
            "trained_color": source["trained_color"],
            "moves": [],
            "sources": [],
        },
    )
    if move is not None:
        entry["moves"].append(move.lower())
    entry["sources"].append(
        {
            "type": source["source_type"],
            "id": source["source_id"],
            "move_index": move_index,
            "move": move.lower() if move else None,
        }
    )


def _publish_card_blocks(
    database: sqlite3.Connection,
    repertoire_id: str,
    issues: list[dict],
    scan_generation: str,
    published_at: str,
) -> None:
    """Atomically replace the card-level projection for one completed scan."""

    storage.clear_card_blocks(database, (repertoire_id,))
    rows = {
        (repertoire_id, source["id"], issue["id"], scan_generation, published_at)
        for issue in issues
        for source in issue.get("sources", [])
        if source.get("type") == "card"
        and storage.card_exists(database, (source.get("id"),))
    }
    storage.insert_card_blocks(database, sorted(rows))
    storage.update_card_validation(database, (repertoire_id, repertoire_id))
def sweep_repertoire(database: sqlite3.Connection, repertoire_id: str) -> dict:
    if not storage.repertoire_exists(database, (repertoire_id,)):
        raise KeyError("Repertoire not found")
    issues = _scan_sources(database, repertoire_id)
    now = _now()
    storage.clear_issues(database, (repertoire_id,))
    for issue in issues:
        storage.insert_sweep_issue(
            database,
            (
                issue["id"],
                repertoire_id,
                issue["kind"],
                issue.get("fen_key"),
                issue.get("fen"),
                issue.get("trained_color"),
                issue["signature"],
                json.dumps(sorted(set(issue.get("moves", [])))),
                json.dumps(issue.get("sources", [])),
                now,
                now,
            ),
        )
    status = "needs_repair" if issues else "clean"
    storage.publish_sweep_state(database, (repertoire_id, status, now))
    _publish_card_blocks(database, repertoire_id, issues, f"sweep:{now}", now)
    return integrity_summary(database, repertoire_id)


def sweep_all(database: sqlite3.Connection) -> None:
    ids = [
        row[0]
        for row in storage.all_repertoire_ids(database)
        if row[0] not in SYSTEM_REPERTOIRES
    ]
    for repertoire_id in ids:
        sweep_repertoire(database, repertoire_id)


def enqueue_integrity_scans(
    repertoire_id: str | None = None,
    *,
    stale_only: bool = False,
    restart: bool = False,
    background: bool = False,
) -> list[str]:
    """Queue integrity work without changing the last published result."""

    queued: list[str] = []
    stale_repertoire_targets, all_repertoire_targets = storage.repertoire_scan_targets(SYSTEM_REPERTOIRES)
    from tempo_legacy_database.connections import connection

    with connection(background=background) as database:
        if repertoire_id:
            ids = [repertoire_id]
        elif stale_only:
            ids = [
                row[0]
                for row in stale_repertoire_targets(database, tuple(SYSTEM_REPERTOIRES))
            ]
        else:
            ids = [
                row[0]
                for row in all_repertoire_targets(database, tuple(SYSTEM_REPERTOIRES))
            ]
        now = _now()
        for identifier in ids:
            if not storage.repertoire_exists(database, (identifier,)):
                raise KeyError("Repertoire not found")
            active = storage.active_scan(database, (identifier,))
            if active and not restart:
                queued.append(active["run_id"])
                continue
            total = storage.source_count(database, (identifier, identifier, identifier))
            run_id = str(uuid.uuid4())
            storage.queue_scan_job(database, (identifier, run_id, total, now))
            storage.queue_scan_state(database, (identifier, total))
            queued.append(run_id)
    return queued


def _issues_from_observations(
    repertoire_id: str, observations: list[dict], invalid: list[dict]
) -> list[dict]:
    positions: dict[str, dict] = {}
    for source_positions in observations:
        for value in source_positions:
            key = value["fen_key"]
            if key not in positions:
                positions[key] = value
            else:
                positions[key]["moves"].extend(value.get("moves", []))
                positions[key]["sources"].extend(value.get("sources", []))
    issues: list[dict] = []
    for value in positions.values():
        move_set = set(value["moves"])
        if len(move_set) == 1:
            continue
        value["kind"] = "missing_response" if not move_set else "multiple_responses"
        issues.append(value)
    for issue in invalid:
        issue["id"] = _issue_id(repertoire_id, issue["kind"], None, issue.get("sources"))
        issue["signature"] = _signature(issue)
        issues.append(issue)
    for issue in issues:
        issue.setdefault("id", _issue_id(repertoire_id, issue["kind"], issue.get("fen_key"), issue.get("sources")))
        issue.setdefault("signature", _signature(issue))
    return sorted(issues, key=lambda item: (item.get("fen_key") or "", item["kind"], item["id"]))


def claim_integrity_slice() -> dict | None:
    from tempo_legacy_database.connections import connection
    from .background_activity import claimable, control_order

    with connection(background=True) as database:
        storage.begin_write(database)
        row = storage.next_scan_job(database)
        if not row:
            return None
        now = _now()
        if row["status"] == "finalizing" or row["source_offset"] >= row["total_sources"]:
            storage.mark_scan_finalizing(database, (now, row["repertoire_id"]))
            storage.mark_state_running(database, (row["repertoire_id"],))
            return {**dict(row), "status": "finalizing"}
        storage.mark_scan_running(database, (now, row["repertoire_id"]))
        storage.mark_state_running(database, (row["repertoire_id"],))
        return {**dict(row), "status": "running"}


def execute_integrity_slice(job: dict) -> None:
    from tempo_legacy_database.connections import connection
    from .activity_gate import activity_gate
    from .background_activity import emit_progress

    with activity_gate.background_job("integrity", job["repertoire_id"]):
        if job["status"] == "finalizing":
            with connection(background=True) as database:
                staged = storage.staged_observations(database, (job["run_id"],))
            observations = [json.loads(row[0]) for row in staged]
            invalid = [item for row in staged for item in json.loads(row[1])]
            issues = _issues_from_observations(job["repertoire_id"], observations, invalid)
            now = _now()
            with connection(background=True) as database:
                current_job = storage.current_run(database, (job["repertoire_id"],))
                if not current_job or current_job["run_id"] != job["run_id"]:
                    _LOGGER.info(
                        "discarded stale integrity publication repertoire_id=%s run_id=%s",
                        job["repertoire_id"],
                        job["run_id"],
                    )
                    return
                storage.clear_issues(database, (job["repertoire_id"],))
                for issue in issues:
                    storage.insert_staged_issue(database, (issue["id"], job["repertoire_id"], issue["kind"], issue.get("fen_key"), issue.get("fen"), issue.get("trained_color"), issue["signature"], json.dumps(sorted(set(issue.get("moves", [])))), json.dumps(issue.get("sources", [])), now, now))
                status = "needs_repair" if issues else "clean"
                storage.publish_scan_state(database, (status, now, job["run_id"], job["repertoire_id"]))
                _publish_card_blocks(
                    database,
                    job["repertoire_id"],
                    issues,
                    job["run_id"],
                    now,
                )
                storage.complete_scan_job(database, (now, job["repertoire_id"]))
            from tempo_legacy_database.adapters.database_executor import database_writer
            from .durable_tasks import enqueue_task

            if database_writer.healthy:
                enqueue_task(
                    "daily_queue",
                    "current",
                    {},
                    priority=10,
                    foreground=False,
                )
            emit_progress("integrity", job["repertoire_id"], job["run_id"], "Published integrity result", job["total_sources"], job["total_sources"])
            return

        with connection(background=True) as database:
            sources = _source_rows(database, job["repertoire_id"])
            current_job = storage.current_run_sources(database, (job["repertoire_id"],))
            if not current_job or current_job["run_id"] != job["run_id"]:
                return
            if len(sources) != int(current_job["total_sources"]):
                storage.restart_changed_sources(database, (len(sources), _now(), job["repertoire_id"], job["run_id"]))
                storage.clear_staged_sources(database, (job["run_id"],))
                return
            if int(job["source_offset"]) >= len(sources):
                storage.finish_source_scanning(database, (_now(), job["repertoire_id"], job["run_id"]))
                return
            source = sources[job["source_offset"]]
            repertoire_color = next((item.get("trained_color") for item in sources if item.get("trained_color") in {"white", "black"}), None)
        positions, invalid = _scan_source(source, repertoire_color)
        now = _now()
        with connection(background=True) as database:
            storage.stage_source_observations(database, (job["run_id"], job["source_offset"], json.dumps(list(positions.values())), json.dumps(invalid)))
            next_offset = job["source_offset"] + 1
            status = "finalizing" if next_offset >= job["total_sources"] else "running"
            storage.advance_scan_job(database, (status, next_offset, now, job["repertoire_id"], job["run_id"]))
            storage.advance_scan_state(database, (next_offset, job["total_sources"], job["repertoire_id"]))
        emit_progress("integrity", job["repertoire_id"], job["run_id"], "Scanning sources", next_offset, job["total_sources"])


def requeue_integrity_slice(job: dict, error: Exception) -> None:
    """Return a failed slice to durable storage without disturbing published issues."""

    from tempo_legacy_database.connections import connection

    message = str(error)
    now = _now()
    with connection(background=True) as database:
        attempts = storage.scan_attempts(database, (job["repertoire_id"], job["run_id"]))
        attempt_count = int(attempts[0]) if attempts else int(job.get("attempts") or 0)
        if attempt_count >= 5:
            storage.fail_scan_job(database, (message, now, job["repertoire_id"], job["run_id"]))
            storage.fail_scan_state(database, (message, job["repertoire_id"]))
        else:
            storage.requeue_scan_job(database, (message, now, job["repertoire_id"], job["run_id"]))
            storage.requeue_scan_state(database, (message, job["repertoire_id"]))
    _LOGGER.warning(
        "integrity slice requeued job_type=integrity job_id=%s retry=%s error=%s",
        job["repertoire_id"],
        attempt_count,
        message,
    )


def integrity_summary(database: sqlite3.Connection, repertoire_id: str) -> dict:
    row = storage.summary_state(database, (repertoire_id,))
    if not row:
        raise KeyError("Repertoire not found")
    issue = storage.first_issue(database, (repertoire_id,))
    count = storage.issue_count(database, (repertoire_id,))
    return {
        "status": row["integrity_status"],
        "issue_count": count,
        "first_issue_id": issue[0] if issue else None,
        "checked_at": row["integrity_checked_at"],
        "scan_status": row["scan_status"],
        "scan_generation": row["scan_generation"],
        "scan_progress": {
            "completed": row["scan_completed_sources"],
            "total": row["scan_total_sources"],
        },
        "last_scan_error": row["scan_error"],
    }


def list_integrity_issues(database: sqlite3.Connection, repertoire_id: str) -> list[dict]:
    rows = storage.issue_details(database, (repertoire_id,))
    results = []
    for row in rows:
        sources = json.loads(row["sources_json"])
        moves = json.loads(row["moves_json"])
        stats = {
            move: {"uci": move, "line_count": 0, "card_count": 0, "review_count": 0}
            for move in moves
        }
        for source in sources:
            move = source.get("move")
            if move not in stats:
                continue
            bucket = stats[move]
            if source.get("type") == "line":
                bucket["line_count"] += 1
            elif source.get("type") == "card":
                bucket["card_count"] += 1
                bucket["review_count"] += storage.card_review_count(database, (source["id"],))
        results.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "fen_key": row["fen_key"],
                "fen": row["fen"],
                "trained_color": row["trained_color"],
                "signature": row["signature"],
                "moves": list(stats.values()),
                "sources": sources,
            }
        )
    return results


def _transform_source(start_fen: str, moves: list[str], trained_color: str, target_fen: str, selected: str) -> tuple[list[str], bool]:
    board = chess.Board(start_fen)
    target = chess.WHITE if trained_color == "white" else chess.BLACK
    transformed = list(moves)
    for index, move_value in enumerate(list(moves)):
        if board.turn == target and canonical_fen(board.fen()) == target_fen:
            if move_value.lower() == selected:
                return transformed, False
            transformed = transformed[:index] + [selected]
            return transformed, True
        board.push_uci(move_value)
    if board.turn == target and canonical_fen(board.fen()) == target_fen:
        transformed.append(selected)
        return transformed, True
    return transformed, False


def _rewrite_line(database: sqlite3.Connection, row: sqlite3.Row, moves: list[str], *, validated_route: dict | None = None, certify: bool = True) -> bool:
    from .canonical_prefix import ensure_line_in_scope, certify_admitted_route
    if validated_route is None:
        validated_route = ensure_line_in_scope(database, row["repertoire_id"], row["start_fen"], moves, remember=False)
    new_id = hashlib.sha256(
        f"{row['repertoire_id']}\0{card_id(row['start_fen'], moves)}".encode()
    ).hexdigest()
    if new_id == row["id"]:
        return False
    storage.insert_replacement_line(database, (new_id, row["repertoire_id"], row["name"], row["trained_color"], row["start_fen"], json.dumps(moves), row["created_at"]))
    storage.delete_source_line(database, (row["id"],))
    if certify:
        certify_admitted_route(database, row["repertoire_id"], validated_route)
    return True


def _rewrite_card(database: sqlite3.Connection, repertoire_id: str, row: sqlite3.Row, moves: list[str], *, validated_route: dict | None = None, certify: bool = True) -> bool:
    from .canonical_prefix import ensure_line_in_scope, certify_admitted_route
    if validated_route is None:
        validated_route = ensure_line_in_scope(database, repertoire_id, row["start_fen"], moves, remember=False)
    new_id = card_id(row["start_fen"], moves)
    if new_id == row["id"]:
        return False
    existing = storage.card_exists(database, (new_id,))
    if not existing:
        columns = card_columns(database)
        values = dict(row)
        values.update({"id": new_id, "repertoire_id": repertoire_id, "moves_json": json.dumps(moves), "state": "new", "due_date": datetime.now(timezone.utc).date().isoformat(), "interval_days": 0, "repetitions": 0, "lapses": 0, "introduced_at": None, "archived": 0, "superseded_by": None, "canonical_route_source": 1})
        storage.insert_replacement_card(database, columns, values)
    storage.mark_card_authored(database, (new_id,))
    storage.upsert_authored_membership(database, (repertoire_id, new_id))
    storage.delete_membership(database, (repertoire_id, row["id"]))
    remaining = storage.card_has_membership(database, (row["id"],))
    if not remaining:
        storage.archive_superseded_card(database, (new_id, row["id"]))
    if certify:
        certify_admitted_route(database, repertoire_id, validated_route)
    return True


def _reconcile_derived_cards(database: sqlite3.Connection, repertoire_id: str) -> int:
    """Reconcile unsupported generated memberships, preserving authored routes."""
    lines = storage.line_routes(database, (repertoire_id,))
    line_routes = [
        (canonical_fen(row["start_fen"]), json.loads(row["moves_json"]))
        for row in lines
    ]
    cards = storage.derived_cards(database, (repertoire_id, repertoire_id))
    changed = 0
    for card in cards:
        supported = any(
            canonical_fen(card["start_fen"]) == start_key
            and route[: len(json.loads(card["moves_json"]))] == json.loads(card["moves_json"])
            for start_key, route in line_routes
        )
        if not supported:
            changed += int(_archive_unsupported_card(database, repertoire_id, card["id"]))
    return changed


def _archive_unsupported_card(
    database: sqlite3.Connection, repertoire_id: str, card_identifier: str
) -> bool:
    """Detach an already-computed unsupported card without chess traversal."""

    card = storage.active_card_authority(database, (card_identifier,))
    if not card:
        return False
    membership = storage.membership_authority(database, (repertoire_id, card_identifier))
    if membership is not None and membership["canonical_route_source"]:
        return False
    if membership is None and (card["repertoire_id"] != repertoire_id or card["canonical_route_source"]):
        return False
    remaining = storage.remaining_membership(database, (card_identifier, repertoire_id))
    if remaining and card["repertoire_id"] == repertoire_id:
        storage.reassign_card_owner(database, (remaining["repertoire_id"], card_identifier))
    elif not remaining and (not card["canonical_route_source"] or card["repertoire_id"] == repertoire_id):
        storage.archive_unsupported_card(database, (card_identifier,))
    storage.delete_membership(database, (repertoire_id, card_identifier))
    return True


@refresh_game_publications_after_mutation
def resolve_issue(database: sqlite3.Connection, repertoire_id: str, issue_id: str, signature: str, selected_move: str) -> dict:
    row = storage.issue(database, (issue_id, repertoire_id))
    if not row:
        raise KeyError("Integrity issue not found")
    if row["signature"] != signature:
        raise RuntimeError("This integrity issue changed; refresh and try again")
    if not row["fen"]:
        raise ValueError("Invalid source issues must be repaired by editing or removing the source")
    board = chess.Board(row["fen"])
    selected = selected_move.lower()
    try:
        move = chess.Move.from_uci(selected)
    except ValueError as error:
        raise ValueError("Selected move is not valid UCI") from error
    if move not in board.legal_moves:
        raise ValueError("Selected move is illegal from this position")
    from .canonical_prefix import ensure_line_in_scope, certify_admitted_route
    changed_lines = changed_cards = 0
    line_changes = []
    card_changes = []
    target_fen = row["fen_key"]
    for source in json.loads(row["sources_json"]):
        if source.get("type") == "line":
            source_row = storage.source_line(database, (source["id"],))
            if not source_row:
                continue
            moves, changed = _transform_source(source_row["start_fen"], json.loads(source_row["moves_json"]), source_row["trained_color"], target_fen, selected)
            if changed:
                line_changes.append((source_row, moves, ensure_line_in_scope(database, repertoire_id, source_row["start_fen"], moves, remember=False)))
        elif source.get("type") == "card":
            source_row = storage.source_card(database, (source["id"],))
            if not source_row:
                continue
            trained = source_row["trained_color"] or row["trained_color"]
            moves, changed = _transform_source(source_row["start_fen"], json.loads(source_row["moves_json"]), trained, target_fen, selected)
            if changed:
                card_changes.append((source_row, moves, ensure_line_in_scope(database, repertoire_id, source_row["start_fen"], moves, remember=False)))
    admitted_routes = []
    for source_row, moves, validated_route in line_changes:
        if _rewrite_line(database, source_row, moves, validated_route=validated_route, certify=False):
            changed_lines += 1
            admitted_routes.append(validated_route)
    for source_row, moves, validated_route in card_changes:
        if _rewrite_card(database, repertoire_id, source_row, moves, validated_route=validated_route, certify=False):
            changed_cards += 1
            admitted_routes.append(validated_route)
    changed_cards += _reconcile_derived_cards(database, repertoire_id)
    for validated_route in admitted_routes:
        certify_admitted_route(database, repertoire_id, validated_route)
    summary = sweep_repertoire(database, repertoire_id)
    return {"summary": summary, "changed_line_count": changed_lines, "changed_card_count": changed_cards}


def prepare_issue_resolution(
    repertoire_id: str,
    issue_id: str,
    signature: str,
    selected_move: str,
) -> dict:
    """Read and compute a guided repair without holding a SQLite connection."""

    with read_connection() as database:
        issue_row = storage.issue(database, (issue_id, repertoire_id))
        if not issue_row:
            raise KeyError("Integrity issue not found")
        issue = dict(issue_row)
        source_descriptors = json.loads(issue["sources_json"])
        line_rows = {
            row["id"]: dict(row)
            for row in storage.prepared_lines(database, (repertoire_id,))
        }
        card_rows = {
            row["id"]: dict(row)
            for row in storage.prepared_cards(database, (repertoire_id, repertoire_id))
        }
    if issue["signature"] != signature:
        raise RuntimeError("This integrity issue changed; refresh and try again")
    if not issue["fen"]:
        raise ValueError("Invalid source issues must be repaired by editing or removing the source")
    board = chess.Board(issue["fen"])
    selected = selected_move.lower()
    try:
        move = chess.Move.from_uci(selected)
    except ValueError as error:
        raise ValueError("Selected move is not valid UCI") from error
    if move not in board.legal_moves:
        raise ValueError("Selected move is illegal from this position")

    changed_lines: dict[str, list[str]] = {}
    changed_cards: dict[str, list[str]] = {}
    for source in source_descriptors:
        if source.get("type") == "line" and source.get("id") in line_rows:
            row = line_rows[source["id"]]
            moves, changed = _transform_source(
                row["start_fen"], json.loads(row["moves_json"]),
                row["trained_color"], issue["fen_key"], selected,
            )
            if changed:
                changed_lines[row["id"]] = moves
        elif source.get("type") == "card" and source.get("id") in card_rows:
            row = card_rows[source["id"]]
            moves, changed = _transform_source(
                row["start_fen"], json.loads(row["moves_json"]),
                row.get("trained_color") or issue["trained_color"],
                issue["fen_key"], selected,
            )
            if changed:
                changed_cards[row["id"]] = moves

    line_routes = [
        (
            canonical_fen(row["start_fen"]),
            changed_lines.get(row_identifier, json.loads(row["moves_json"])),
        )
        for row_identifier, row in line_rows.items()
    ]
    unsupported_card_ids: list[str] = []
    for row_identifier, row in card_rows.items():
        final_moves = changed_cards.get(row_identifier, json.loads(row["moves_json"]))
        final_card_identifier = (
            card_id(row["start_fen"], final_moves)
            if row_identifier in changed_cards
            else row_identifier
        )
        if not any(
            canonical_fen(row["start_fen"]) == line_start_fen
            and line_moves[: len(final_moves)] == final_moves
            for line_start_fen, line_moves in line_routes
        ):
            unsupported_card_ids.append(final_card_identifier)
    return {
        "repertoire_id": repertoire_id,
        "issue_id": issue_id,
        "signature": signature,
        "changed_lines": changed_lines,
        "changed_cards": changed_cards,
        "expected_line_moves": {
            source_id: line_rows[source_id]["moves_json"] for source_id in changed_lines
        },
        "expected_card_moves": {
            source_id: card_rows[source_id]["moves_json"] for source_id in changed_cards
        },
        "unsupported_card_ids": unsupported_card_ids,
    }


def execute_durable_integrity_repair(task: dict) -> None:
    payload = task["payload"]
    prepared = prepare_issue_resolution(
        payload["repertoire_id"],
        payload["issue_id"],
        payload["signature"],
        payload["selected_move_uci"],
    )

    @refresh_game_publications_after_mutation
    def publish(database: sqlite3.Connection) -> None:
        current = storage.issue_signature(database, (prepared["issue_id"], prepared["repertoire_id"]))
        if not current or current["signature"] != prepared["signature"]:
            raise RuntimeError("This integrity issue changed; refresh and try again")
        from .canonical_prefix import ensure_line_in_scope, certify_admitted_route
        line_changes = []
        card_changes = []
        for source_id, moves in prepared["changed_lines"].items():
            source_row = storage.source_line(database, (source_id,))
            if source_row:
                line_changes.append((source_row, moves, ensure_line_in_scope(database, prepared["repertoire_id"], source_row["start_fen"], moves, remember=False)))
        for source_id, moves in prepared["changed_cards"].items():
            source_row = storage.source_card(database, (source_id,))
            if source_row:
                card_changes.append((source_row, moves, ensure_line_in_scope(database, prepared["repertoire_id"], source_row["start_fen"], moves, remember=False)))
        admitted_routes = []
        for source_row, moves, validated_route in line_changes:
            if _rewrite_line(database, source_row, moves, validated_route=validated_route, certify=False):
                admitted_routes.append(validated_route)
        for source_row, moves, validated_route in card_changes:
            if _rewrite_card(database, prepared["repertoire_id"], source_row, moves, validated_route=validated_route, certify=False):
                admitted_routes.append(validated_route)
        for card_identifier in prepared["unsupported_card_ids"]:
            _archive_unsupported_card(
                database, prepared["repertoire_id"], card_identifier
            )
        for validated_route in admitted_routes:
            certify_admitted_route(database, prepared["repertoire_id"], validated_route)

    submit_background_write(
        publish,
        label=f"integrity-repair:{prepared['repertoire_id']}:{prepared['issue_id']}",
    )
    enqueue_integrity_scans(prepared["repertoire_id"])
