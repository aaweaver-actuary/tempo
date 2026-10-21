"""Existing finding-card reads/writes; command validation stays outside storage.

These preserve the legacy shared-database transaction and queue-lock authority.
They do not establish a new Games-to-Study database permission or isolated domain.
"""
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position


def requires_native_queue_refresh(database):
    return hasattr(database, "execute_native")


def finding_card_context(database, finding_id, *, lock):
    if lock:
        source = database.execute(
            "SELECT game_id,source_opportunity_id FROM current_game_findings game_findings WHERE id=? FOR UPDATE",
            (finding_id,),
        ).fetchone()
        if source is not None:
            database.execute(
                "SELECT id FROM imported_games WHERE id=? FOR UPDATE", (source["game_id"],),
            )
            if source["source_opportunity_id"]:
                database.execute(
                    "SELECT id FROM tactical_opportunities WHERE id=? FOR UPDATE",
                    (source["source_opportunity_id"],),
                )
    return database.execute(
        """SELECT f.*,g.color,g.adaptive_excluded,g.analysis_version AS game_analysis_version,
                  o.active AS opportunity_active,o.analysis_version AS opportunity_analysis_version,
                  o.accepted_moves_json,o.evidence_json AS opportunity_evidence_json
           FROM current_game_findings f JOIN imported_games g ON g.id=f.game_id
           LEFT JOIN tactical_opportunities o ON o.id=f.source_opportunity_id WHERE f.id=?""",
        (finding_id,),
    ).fetchone()


def existing_tactic_card(database, card_id):
    return database.execute(
        "SELECT id FROM cards WHERE id=? AND content_type='tactic' AND archived=0 AND superseded_by IS NULL",
        (card_id,),
    ).fetchone()


def existing_middlegame_card(database, normalized_fen, moves_json):
    return database.execute(
        "SELECT id FROM cards WHERE content_type='middlegame' AND source_fen=? AND moves_json=? AND archived=0",
        (normalized_fen, moves_json),
    ).fetchone()


def accept_finding_card(database, finding_id, card_id, created_at):
    database.execute(
        "UPDATE game_findings SET card_id=?,status='accepted',updated_at=? WHERE id=?",
        (card_id, created_at, finding_id),
    )


def ensure_mistakes_repertoire(database, repertoire_id, repertoire_name, created_at):
    database.execute(
        """INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at,is_main)
           VALUES(?,?,?, ?,0)""",
        (repertoire_id, repertoire_name, "Accepted personal game findings", created_at),
    )


def insert_middlegame_card(database, card_id, repertoire_id, starting_fen, moves_json,
                         due_date, finding_id, normalized_fen, trained_color, introduced_at):
    return database.execute(
        """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
               content_type,source_ref,source_fen,trained_color,introduced_at)
               VALUES(?,?,?,?,?,'learning',?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
        (card_id, repertoire_id, "checkpoint", starting_fen,
         moves_json, due_date, "middlegame", finding_id, normalized_fen,
         trained_color, introduced_at),
    ).rowcount


def link_repertoire_card(database, repertoire_id, card_id):
    database.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?) ON CONFLICT DO NOTHING",
        (repertoire_id, card_id),
    )


def saved_card(database, card_id):
    return database.execute(
        "SELECT archived,content_type,source_fen,moves_json FROM cards WHERE id=?"
        + (" FOR UPDATE" if hasattr(database, "execute_native") else ""),
        (card_id,),
    ).fetchone()


def lock_native_queue_position(database, day):
    lock_queue_date_for_position(database, day)
