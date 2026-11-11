"""Pure validation of published repertoire integrity certificate facts."""

from datetime import datetime
import json


def integrity_certificate_from_row(row: dict, repertoire_id: str, graph_generation: int) -> dict | None:
    """The same publication predicate used by capture and its acceptance fence."""
    try:
        payload = json.loads(row["payload_json"])
        checked = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
        if (row["integrity_status"] not in {"clean", "needs_repair"}
                or row["scan_status"] != "idle" or not row["checked_at"] or row["scan_error"] is not None
                or checked.tzinfo is None or checked.utcoffset() is None
                or type(row["scan_completed_sources"]) is not int or type(row["scan_total_sources"]) is not int
                or row["scan_total_sources"] < 0
                or row["scan_completed_sources"] != row["scan_total_sources"]
                or row["graph_task_generation"] != graph_generation or row["graph_task_state"] != "complete"
                or row["scan_task_state"] != "complete"
                or row["scan_generation"] != f"{row['scan_task_id']}:{row['scan_task_generation']}"
                or payload.get("repertoire_id") != repertoire_id
                or type(payload.get("graph_generation")) is not int
                or payload["graph_generation"] != graph_generation):
            return None
        return {key: value for key, value in row.items() if key != "payload_json"}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
