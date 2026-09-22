"""Read-only activity projection and small, durable scheduling controls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import sqlite3

from ..database import connection, read_connection


SOURCES = {
    "durable": ("background_tasks", "id"),
    "sync": ("game_sync_jobs", "id"),
    "derivation": ("game_derivation_jobs", "game_id"),
    "integrity": ("repertoire_integrity_jobs", "repertoire_id"),
    "coverage": ("repertoire_coverage_runs", "id"),
    "statistics": ("daily_statistics_jobs", "local_day"),
    "priority": ("repertoire_priority_jobs", "repertoire_id"),
    "game_analysis": ("game_analysis_jobs", "game_id"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def control_order(source: str, id_expression: str) -> str:
    """SQL order expression shared by every claim path in one queue."""
    return ("COALESCE((SELECT promoted FROM background_activity a WHERE "
            f"a.source='{source}' AND a.work_id={id_expression}),0) DESC,")


def claimable(source: str, id_expression: str) -> str:
    return ("COALESCE((SELECT paused FROM background_activity a WHERE "
            f"a.source='{source}' AND a.work_id={id_expression}),0)=0")


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
        generation_columns = {
            "durable": ("generation",), "derivation": ("derivation_version",),
            "integrity": ("run_id",), "coverage": ("id",),
            "priority": ("generation",), "game_analysis": ("analysis_version", "analysis_evidence_version"),
        }
        table, id_column = SOURCES[source]
        columns = generation_columns.get(source)
        if columns:
            current = database.execute(
                f"SELECT {','.join(columns)}{',status,lease_id' if source == 'game_analysis' else ''} FROM {table} WHERE {id_column}=?",
                (work_id,),
            ).fetchone()
            if not current:
                return False
            current_generation = ":".join(str(current[column]) for column in columns)
            if current_generation != generation_key:
                return False
            if source == "game_analysis" and (current["status"] != "leased" or current["lease_id"] != lease_id):
                return False
        database.execute(
            """INSERT INTO background_activity(source,work_id,generation_key,phase,
                   completed_units,total_units,updated_at) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(source,work_id) DO UPDATE SET
                   generation_key=excluded.generation_key,phase=excluded.phase,
                   completed_units=excluded.completed_units,total_units=excluded.total_units,
                   updated_at=excluded.updated_at""",
            (source, work_id, generation_key, phase, completed_units, total_units, _now()),
        )
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


def set_control(source: str, work_id: str, action: str) -> bool:
    if source not in SOURCES or action not in {"pause", "resume", "prioritize", "normal"}:
        return False
    table, id_column = SOURCES[source]
    with connection() as database:
        state_column = "state" if source == "durable" else "status"
        work_row = database.execute(
            f"SELECT {state_column} FROM {table} WHERE {id_column}=?", (work_id,)
        ).fetchone()
        if not work_row:
            return False
        if work_row[0] in {"failed", "superseded"}:
            return False
        if work_row[0] == "complete":
            if source != "coverage" or not database.execute(
                """SELECT 1 FROM repertoire_coverage_nodes WHERE run_id=?
                   AND (explorer_status!='complete' OR maia_status!='complete') LIMIT 1""",
                (work_id,),
            ).fetchone():
                return False
        now = _now()
        database.execute(
            """INSERT INTO background_activity(source,work_id,updated_at)
               VALUES(?,?,?) ON CONFLICT(source,work_id) DO NOTHING""",
            (source, work_id, now),
        )
        column = "paused" if action in {"pause", "resume"} else "promoted"
        value = int(action in {"pause", "prioritize"})
        database.execute(
            f"UPDATE background_activity SET {column}=?,updated_at=? WHERE source=? AND work_id=?",
            (value, now, source, work_id),
        )
        if source == "game_analysis" and action == "pause":
            database.execute(
                """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,
                   lease_expires_at=NULL,updated_at=? WHERE game_id=? AND status='leased'""",
                (now, work_id),
            )
            database.execute(
                "UPDATE imported_games SET analysis_state='pending' WHERE id=?",
                (work_id,),
            )
        if source == "coverage" and action == "pause":
            database.execute(
                """UPDATE repertoire_coverage_nodes SET maia_status='queued',
                   lease_id=NULL,lease_expires_at=NULL,updated_at=?
                   WHERE run_id=? AND maia_status='leased'""",
                (now, work_id),
            )
    return True


def _base_item(source: str, work_id: str, title: str, state: str, updated_at: str,
               generation_key: str, phase: str | None = None,
               completed: int | None = None, total: int | None = None,
               error: str | None = None) -> dict:
    return {
        "source": source, "id": work_id, "title": title, "state": state,
        "phase": phase or state, "completed": completed, "total": total,
        "updated_at": updated_at, "generation_key": generation_key,
        "error": error, "paused": False, "promoted": False,
    }


def list_activity(*, offset: int = 0, limit: int = 50) -> dict:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    items: list[dict] = []
    with read_connection() as database:
        for row in database.execute("SELECT * FROM background_tasks WHERE state!='complete' OR updated_at>=?", (cutoff,)):
            items.append(_base_item("durable", row["id"], row["kind"].replace("_", " ").title(), row["state"], row["updated_at"], str(row["generation"]), row["phase"], error=row["last_error"]))
        for row in database.execute("SELECT * FROM game_sync_jobs WHERE status!='complete' OR updated_at>=?", (cutoff,)):
            items.append(_base_item("sync", row["id"], "Game sync", row["status"], row["updated_at"], row["id"], error=row["error"]))
        for row in database.execute("""SELECT j.*,g.provider,g.played_at FROM game_derivation_jobs j
                                      JOIN imported_games g ON g.id=j.game_id
                                      WHERE j.status!='complete' OR j.updated_at>=?""", (cutoff,)):
            title = f"{row['provider'].title()} game {row['played_at'][:10]} · {row['game_id'][-8:]} findings"
            items.append(_base_item("derivation", row["game_id"], title, row["status"], row["updated_at"], str(row["derivation_version"]), row["phase"], error=row["last_error"]))
        for row in database.execute("""SELECT j.*,r.name AS repertoire_name FROM repertoire_integrity_jobs j
                                      JOIN repertoires r ON r.id=j.repertoire_id
                                      WHERE j.status!='complete' OR j.updated_at>=?""", (cutoff,)):
            items.append(_base_item("integrity", row["repertoire_id"], f"{row['repertoire_name']} integrity", row["status"], row["updated_at"], row["run_id"], row["status"], row["source_offset"], row["total_sources"], row["last_error"]))
        coverage_counts = {row["run_id"]: row for row in database.execute(
                """SELECT run_id,COUNT(*) AS total,
                   SUM(CASE WHEN explorer_status='complete' THEN 1 ELSE 0 END) AS explorer_done,
                   SUM(CASE WHEN maia_status='complete' THEN 1 ELSE 0 END) AS maia_done,
                   SUM(CASE WHEN explorer_status='running' OR maia_status='leased' THEN 1 ELSE 0 END) AS active
                   FROM repertoire_coverage_nodes GROUP BY run_id"""
            )}
        for row in database.execute("""SELECT runs.*,r.name AS repertoire_name FROM repertoire_coverage_runs runs
                                      JOIN repertoires r ON r.id=runs.repertoire_id"""):
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
        for row in database.execute("SELECT * FROM daily_statistics_jobs WHERE status!='complete' OR updated_at>=?", (cutoff,)):
            items.append(_base_item("statistics", row["local_day"], f"Daily insights {row['local_day']}", row["status"], row["updated_at"], row["local_day"], error=row["last_error"]))
        for row in database.execute("""SELECT j.*,r.name AS repertoire_name FROM repertoire_priority_jobs j
                                      JOIN repertoires r ON r.id=j.repertoire_id
                                      WHERE j.status!='complete' OR j.updated_at>=?""", (cutoff,)):
            items.append(_base_item("priority", row["repertoire_id"], f"{row['repertoire_name']} priorities", row["status"], row["updated_at"], str(row["generation"]), error=row["last_error"]))
        for row in database.execute("""SELECT j.*,g.provider,g.played_at FROM game_analysis_jobs j
                                      JOIN imported_games g ON g.id=j.game_id
                                      WHERE j.status!='complete' OR j.updated_at>=?""", (cutoff,)):
            title = f"{row['provider'].title()} game {row['played_at'][:10]} · {row['game_id'][-8:]} analysis"
            items.append(_base_item("game_analysis", row["game_id"], title, row["status"], row["updated_at"], f"{row['analysis_version']}:{row['analysis_evidence_version']}", error=row["last_error"]))
        controls = {(row["source"], row["work_id"]): row for row in database.execute("SELECT * FROM background_activity")}
    for item in items:
        control = controls.get((item["source"], item["id"]))
        if control:
            item["paused"] = bool(control["paused"])
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
