"""Read-only activity projection and small, durable scheduling controls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import sqlite3
from fastapi import HTTPException

from tempo_legacy_database.connections import connection, read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.controls.activity import storage
from tempo_legacy_database.adapters.controls.activity.storage import SOURCES
from tempo_legacy_database.adapters.controls.claim_sql import claimable, control_order


from .defensive_analysis import DEFENSIVE_TASK_KINDS, analysis_enabled, recommendation_sql, search_admission_sql, task_admission_sql


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_progress(
    source: str, work_id: str, generation_key: str, phase: str,
    completed_units: int | None = None, total_units: int | None = None,
    *, background: bool = True, lease_id: str | None = None,
) -> bool:
    if source not in SOURCES:
        raise ValueError("Unknown background work source")
    if completed_units is not None and (completed_units < 0 or total_units is None or total_units < completed_units):
        raise ValueError("Invalid progress")
    with connection(background=background) as database:
        columns, read_current = storage.prepare_progress_read(source)
        if columns:
            current = read_current(database, work_id)
            if not current:
                return False
            current_generation = ":".join(str(current[column]) for column in columns)
            if current_generation != generation_key:
                return False
            if source == "game_analysis" and (current["status"] != "leased" or current["lease_id"] != lease_id):
                return False
        storage.upsert_progress(database, (source, work_id, generation_key, phase, completed_units, total_units, _now()))
    return True


def emit_progress(
    source: str, work_id: str, generation_key: str, phase: str,
    completed_units: int | None = None, total_units: int | None = None,
) -> None:
    """Progress must never turn a successfully committed slice into a failure."""
    try:
        report_progress(source, work_id, generation_key, phase, completed_units, total_units)
    except sqlite3.OperationalError:
        logging.getLogger("tempo.background").warning(
            "Could not record progress source=%s work_id=%s", source, work_id,
            exc_info=True,
        )


def set_control(source: str, work_id: str, action: str, *, allow_settings_blocked_resume: bool = False) -> bool:
    if source not in SOURCES or action not in {"pause", "resume", "prioritize", "normal"}:
        return False
    with connection() as database:
        return set_control_in_transaction(database, source, work_id, action,
                                          allow_settings_blocked_resume=allow_settings_blocked_resume)


def set_control_in_transaction(database, source: str, work_id: str, action: str,
                               *, allow_settings_blocked_resume: bool = False) -> bool:
    """Apply a validated activity control in the caller's short write transaction."""
    if source not in SOURCES or action not in {"pause", "resume", "prioritize", "normal"}:
        return False
    read_work = storage.prepare_control_read(source, DEFENSIVE_TASK_KINDS)
    work_row = read_work(database, work_id)
    if not work_row or work_row[0] in {"failed", "superseded"}:
        return False
    if work_row[0] == "complete":
        if source != "coverage" or not storage.unfinished_coverage(database, (work_id,)):
            return False
    if action == 'resume' and not allow_settings_blocked_resume and not work_row[1]:
        raise HTTPException(409, 'Defensive analysis is disabled in Settings. '
                            'Enable Defensive analysis in Settings before resuming this work.')
    now = _now()
    storage.ensure_control(database, (source, work_id, now))
    write_control = storage.prepare_control_update(action)
    value = int(action in {"pause", "prioritize"})
    write_control(database, (value, now, source, work_id))
    if source == "game_analysis" and action == "pause":
        storage.release_analysis_lease(database, (now, work_id))
        storage.mark_analysis_pending(database, (work_id,))
    if source == "coverage" and action == "pause":
        storage.release_maia_lease(database, (now, work_id))
    if source == "threat_analysis" and action == "pause":
        storage.release_threat_lease(database, (now, work_id))
    return True


def _base_item(source: str, work_id: str, title: str, state: str, updated_at: str,
               generation_key: str, phase: str | None = None,
               completed: int | None = None, total: int | None = None,
               error: str | None = None) -> dict:
    return {
        "source": source, "id": work_id, "title": title, "state": state,
        "phase": phase or state, "completed": completed, "total": total,
        "updated_at": updated_at, "generation_key": generation_key,
        "error": error, "paused": False, "paused_by_settings": False, "promoted": False,
    }


def list_activity(*, offset: int = 0, limit: int = 50) -> dict:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    items: list[dict] = []
    with read_connection() as database:
        analysis_enabled(database)
        for row in storage.durable_activity(database, (cutoff,), DEFENSIVE_TASK_KINDS):
            items.append(_base_item("durable", row["id"], row["kind"].replace("_", " ").title(), row["state"], row["updated_at"], str(row["generation"]), row["phase"], error=row["last_error"]))
            items[-1]['paused_by_settings'] = bool(row['paused_by_settings'])
            items[-1]['paused'] = items[-1]['paused_by_settings']
        for row in storage.threat_activity(database, (cutoff,)):
            state = ("running" if row["state"] == "leased" else
                     "retrying" if row["state"] == "queued" and row["attempts"] else row["state"])
            title = "Repertoire recommendation search" if row['is_recommendation'] else "Defensive engine search"
            items.append(_base_item("threat_analysis", row["id"], title,
                                    state, row["updated_at"], str(row["attempts"]),
                                    error=row["last_error"]))
            items[-1]['paused_by_settings'] = bool(row['paused_by_settings'])
            items[-1]['paused'] = items[-1]['paused_by_settings']
        for row in storage.sync_activity(database, (cutoff,)):
            items.append(_base_item("sync", row["id"], "Game sync", row["status"], row["updated_at"], row["id"], error=row["error"]))
        for row in storage.derivation_activity(database, (cutoff,)):
            title = f"{row['provider'].title()} game {row['played_at'][:10]} · {row['game_id'][-8:]} findings"
            items.append(_base_item("derivation", row["game_id"], title, row["status"], row["updated_at"], str(row["derivation_version"]), row["phase"], error=row["last_error"]))
        for row in storage.integrity_activity(database, (cutoff,)):
            items.append(_base_item("integrity", row["repertoire_id"], f"{row['repertoire_name']} integrity", row["status"], row["updated_at"], row["run_id"], row["status"], row["source_offset"], row["total_sources"], row["last_error"]))
        coverage_counts = {row["run_id"]: row for row in storage.coverage_counts(database)}
        for row in storage.coverage_activity(database):
            counts = coverage_counts.get(row["id"])
            total_nodes = counts["total"] if counts else 0
            completed_nodes = ((counts["explorer_done"] or 0) + (counts["maia_done"] or 0)) if counts else 0
            state = "running" if counts and counts["active"] else row["status"]
            if total_nodes and completed_nodes == total_nodes * 2:
                state = "complete"
            elif state == "complete":
                state = "queued"
            if state == "complete" and row["updated_at"] < cutoff:
                continue
            items.append(_base_item("coverage", row["id"], f"{row['repertoire_name']} coverage", state, row["updated_at"], row["id"], "Checking positions", completed_nodes, total_nodes * 2, row["last_error"]))
        for row in storage.statistics_activity(database, (cutoff,)):
            items.append(_base_item("statistics", row["local_day"], f"Daily insights {row['local_day']}", row["status"], row["updated_at"], row["local_day"], error=row["last_error"]))
        for row in storage.priority_activity(database, (cutoff,)):
            items.append(_base_item("priority", row["repertoire_id"], f"{row['repertoire_name']} priorities", row["status"], row["updated_at"], str(row["generation"]), error=row["last_error"]))
        game_position_counts = {(item["game_id"], item["analysis_version"]): item
                                for item in storage.analysis_position_counts(database)}
        for row in storage.analysis_activity(database, (cutoff,)):
            title = f"{row['provider'].title()} game {row['played_at'][:10]} · {row['game_id'][-8:]} analysis"
            progress = game_position_counts.get((row["game_id"], row["analysis_version"]))
            state = "running" if row["status"] == "leased" else (
                "retrying" if row["status"] == "queued" and progress and progress["started"] else row["status"])
            phase = "Confirming critical positions" if progress and progress["confirming"] else "Scanning positions"
            items.append(_base_item("game_analysis", row["game_id"], title, state,
                                    row["updated_at"], f"{row['analysis_version']}:{row['analysis_evidence_version']}",
                                    phase, progress["completed"] if progress else 0,
                                    (row["position_total"] * (2 if progress and progress["confirming"] else 1))
                                    if row["position_total"] else None,
                                    row["last_error"]))
        controls = {(row["source"], row["work_id"]): row for row in storage.activity_controls(database)}
    for item in items:
        control = controls.get((item["source"], item["id"]))
        if control:
            item["paused"] = item["paused"] or bool(control["paused"])
            item["promoted"] = bool(control["promoted"])
            if control["generation_key"] == item["generation_key"]:
                item["phase"] = control["phase"] or item["phase"]
                if control["completed_units"] is not None:
                    item["completed"] = control["completed_units"]
                    item["total"] = control["total_units"]
        if item["paused"] and item["state"] in {"running", "leased", "finalizing"}:
            item["state"] = "pausing"
        elif item["paused"] and item["state"] in {"queued", "retrying"}:
            item["state"] = "paused"
            item["phase"] = "Paused"
        if item["state"] == "complete" and item["total"] is not None:
            item["completed"] = item["total"]
    order = {"pausing": 0, "running": 1, "leased": 1, "finalizing": 1,
             "queued": 2, "retrying": 2, "paused": 3, "failed": 4, "complete": 5}
    items.sort(key=lambda item: (order.get(item["state"], 5), not item["promoted"], item["updated_at"], item["source"], item["id"]))
    counts = {
        "running": sum(item["state"] in {"running", "leased", "finalizing", "pausing"} for item in items),
        "queued": sum(item["state"] in {"queued", "retrying"} for item in items),
        "paused": sum(item["state"] == "paused" for item in items),
        "failed": sum(item["state"] == "failed" for item in items),
    }
    return {"items": items[offset:offset + limit], "counts": counts,
            "total": len(items), "next_offset": offset + limit if offset + limit < len(items) else None}
