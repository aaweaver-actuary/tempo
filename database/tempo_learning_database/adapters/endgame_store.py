"""Saved endgame template and immutable receipt reads; no calculation/provider."""

from tempo_learning_database.connections import connection


def prior_attempt(operation_id):
    with connection(read_only=True) as db:
        row = db.execute(
            "SELECT command_name,payload_json FROM operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        return dict(row) if row else None


def active_templates():
    with connection(read_only=True) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM endgame_templates WHERE enabled=1"
        ).fetchall()]
