"""Existing endgame-template lookup and attempt insertion on a borrowed transaction."""


def enabled_template_for_update(database, template_id: str):
    return database.execute_native(
        "SELECT id FROM endgame_templates WHERE id=%s AND enabled=1 FOR UPDATE",
        (template_id,),
    ).fetchone()


def insert_endgame_attempt(database, attempt_id: str, template_id: str, fen: str,
                           target: str, created_at: str) -> None:
    database.execute_native(
        "INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) "
        "VALUES(%s,%s,%s,%s,%s)",
        (attempt_id, template_id, fen, target, created_at),
    )
