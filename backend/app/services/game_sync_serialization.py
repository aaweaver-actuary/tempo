"""Read-only public projection of saved game-sync jobs."""

import json


def serialize_job(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "updated_at": row["updated_at"],
        "error": row["error"],
        "result": (json.loads(row["result_json"])
                   if row["status"] == "complete" and row["result_json"] else None),
    }
