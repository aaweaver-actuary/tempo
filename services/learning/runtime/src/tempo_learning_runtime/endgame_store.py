"""Complete synchronous persistence phases for the optional endgame API.

Every operation releases its connection before returning. Provider HTTP and
candidate policy remain with the caller; attempt effects stay with the gateway.
"""

from datetime import datetime, timezone
import json

from .database import connection


def cached_answer(key):
    with connection(read_only=True) as db:
        row = db.execute(
            "SELECT response_json FROM tablebase_cache WHERE fen_key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None


def cache_answer(key, data):
    with connection() as db:
        db.execute(
            "INSERT INTO tablebase_cache VALUES(?,?,?) ON CONFLICT(fen_key) DO UPDATE SET response_json=excluded.response_json,fetched_at=excluded.fetched_at",
            (key, json.dumps(data), datetime.now(timezone.utc).isoformat()),
        )


def prior_attempt(operation_id):
    with connection(read_only=True) as db:
        row = db.execute(
            "SELECT command_name,payload_json FROM operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        return dict(row) if row else None


def active_template(identifier):
    with connection(read_only=True) as db:
        row = db.execute(
            "SELECT * FROM endgame_templates WHERE id=? AND enabled=1", (identifier,)
        ).fetchone()
        return dict(row) if row else None


def active_templates():
    with connection(read_only=True) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM endgame_templates WHERE enabled=1"
        ).fetchall()]
