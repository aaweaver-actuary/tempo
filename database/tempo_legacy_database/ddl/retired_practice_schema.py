"""SQLite compatibility retirement on the existing caller-owned transaction.

PostgreSQL uses ordered migration 055, never this compatibility adapter.
"""


def retire_endgames(database):
    tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if database.execute("SELECT 1 FROM cards WHERE repertoire_id='__endgames__' AND content_type<>'endgame'").fetchone():
        raise ValueError("Retired practice has cross-family ownership; keep the original database")
    if 'endgame_templates' in tables and database.execute("SELECT 1 FROM endgame_templates e JOIN cards c ON c.id=e.card_id WHERE c.content_type<>'endgame'").fetchone():
        raise ValueError("Retired template references another practice family")
    if database.execute("SELECT 1 FROM opening_graph_steps WHERE card_id IN (SELECT id FROM cards WHERE content_type='endgame') OR parent_card_id IN (SELECT id FROM cards WHERE content_type='endgame')").fetchone():
        raise ValueError("Retired practice has opening graph dependencies")
    identifiers = [row[0] for row in database.execute("SELECT id FROM cards WHERE content_type='endgame'")]
    for identifier in identifiers:
        database.execute("INSERT OR IGNORE INTO deleted_cards(card_id,deleted_at) VALUES(?,datetime('now'))", (identifier,))
        database.execute("DELETE FROM study_attempts WHERE card_id=? OR queue_entry_id IN (SELECT id FROM daily_queue WHERE card_id=?)", (identifier, identifier))
        if 'review_schedule_snapshots' in tables:
            database.execute("DELETE FROM review_schedule_snapshots WHERE review_id IN (SELECT id FROM reviews WHERE card_id=?)", (identifier,))
        for table in ('card_revisions', 'queue_projection_diagnostics', 'repertoire_cards'):
            if table in tables:
                database.execute(f"DELETE FROM {table} WHERE card_id=?", (identifier,))
        database.execute("DELETE FROM cards WHERE id=?", (identifier,))
    database.execute("DELETE FROM queue_attempt_origins WHERE content_type='endgame'")
    database.execute("DELETE FROM repertoire_lines WHERE repertoire_id='__endgames__'")
    database.execute("DELETE FROM repertoires WHERE id='__endgames__'")
    for table in ('endgame_attempts', 'endgame_templates', 'tablebase_cache'):
        database.execute(f"DROP TABLE IF EXISTS {table}")
    columns = {row[1] for row in database.execute("PRAGMA table_info(settings)")}
    if 'draw_hold_user_moves' in columns:
        database.execute("ALTER TABLE settings DROP COLUMN draw_hold_user_moves")
    for event in ('INSERT', 'UPDATE OF content_type'):
        name = 'retired_practice_' + ('insert' if event == 'INSERT' else 'update')
        database.execute(f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {event} ON cards WHEN NEW.content_type='endgame' BEGIN SELECT RAISE(ABORT,'Practice family retired'); END")
