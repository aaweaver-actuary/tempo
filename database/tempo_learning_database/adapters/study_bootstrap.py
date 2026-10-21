"""Study settings and accepted-executable row operations on a borrowed transaction."""


def settings_row(database):
    row = database.execute("SELECT * FROM settings WHERE id=1").fetchone()
    return row


def write_settings_columns(database, columns):
    database.execute("SELECT id FROM settings WHERE id=1 FOR UPDATE").fetchone()
    for column, value in columns:
        database.execute(f"UPDATE settings SET {column}=? WHERE id=1", (value,))


def tactic_progress_rows(database):
    return database.execute(
        "SELECT p.deck_id,p.puzzle_id,p.clean_pass_at FROM tactic_progress p "
        "WHERE p.clean_pass_at IS NOT NULL OR EXISTS "
        "(SELECT 1 FROM tactic_discovery_attempts a WHERE a.puzzle_id=p.puzzle_id) "
        "ORDER BY p.deck_id,p.puzzle_id"
    ).fetchall()


def membership_card(database, card_id):
    card = database.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    return card


def membership_candidates(database, card_id):
    rows = database.execute(
        "SELECT c.id,c.revision,c.trained_color,p.payload_json,COALESCE(alias.content_card_id,c.id) AS authored_id FROM cards c "
        "JOIN accepted_publications p ON p.publication_id=c.publication_id AND p.generation=c.publication_generation "
        "JOIN serving_publications serving ON serving.publication_id=p.publication_id AND serving.generation=p.generation "
        "LEFT JOIN content_card_aliases alias ON alias.learning_card_id=c.id AND alias.publication_id=p.publication_id "
        "WHERE p.state='active' AND c.serving_active=1 "
        "AND c.archived=0 AND c.superseded_by IS NULL AND c.content_type='opening' "
        "AND NOT EXISTS (SELECT 1 FROM deleted_cards d WHERE d.card_id=c.id) "
        "AND EXISTS (SELECT 1 FROM repertoire_cards member JOIN repertoire_cards current "
        "ON current.repertoire_id=member.repertoire_id WHERE member.card_id=c.id AND current.card_id=?)",
        (card_id,)).fetchall()
    return rows
