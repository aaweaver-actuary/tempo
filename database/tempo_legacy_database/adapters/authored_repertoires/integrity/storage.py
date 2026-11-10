"""Existing integrity scan, projection and repair SQL; caller owns policy/transaction."""


def summary_state(database, values):
    return database.execute(
        """SELECT r.id,COALESCE(s.status,'unchecked') integrity_status,s.checked_at integrity_checked_at,
                  COALESCE(s.scan_status,'idle') scan_status,s.scan_generation,
                  COALESCE(s.scan_completed_sources,0) scan_completed_sources,
                  COALESCE(s.scan_total_sources,0) scan_total_sources,s.scan_error
           FROM repertoires r LEFT JOIN repertoire_integrity_state s ON s.repertoire_id=r.id
           WHERE r.id=?""",
        values,
    ).fetchone()


def first_issue(database, values):
    return database.execute(
        "SELECT id FROM repertoire_integrity_issues WHERE repertoire_id=? ORDER BY updated_at,id LIMIT 1",
        values,
    ).fetchone()


def issue_count(database, values):
    return database.execute(
        "SELECT COUNT(*) FROM repertoire_integrity_issues WHERE repertoire_id=?",
        values,
    ).fetchone()[0]


def issue_details(database, values):
    return database.execute(
        """SELECT id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json
           FROM repertoire_integrity_issues WHERE repertoire_id=? ORDER BY updated_at,id""",
        values,
    ).fetchall()


def card_review_count(database, values):
    return database.execute(
        "SELECT COUNT(*) FROM reviews WHERE card_id=?",
        values,
    ).fetchone()[0]


def delete_membership(database, values):
    return database.execute(
        "DELETE FROM repertoire_cards WHERE repertoire_id=? AND card_id=?",
        values,
    )


def active_card_authority(database, values):
    return database.execute(
        "SELECT repertoire_id,canonical_route_source FROM cards WHERE id=? AND archived=0",
        values,
    ).fetchone()


def membership_authority(database, values):
    return database.execute(
        "SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?",
        values,
    ).fetchone()


def remaining_membership(database, values):
    return database.execute(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? AND repertoire_id<>? ORDER BY repertoire_id LIMIT 1",
        values,
    ).fetchone()


def reassign_card_owner(database, values):
    return database.execute(
        "UPDATE cards SET repertoire_id=? WHERE id=?",
        values,
    )


def archive_unsupported_card(database, values):
    return database.execute(
        "UPDATE cards SET archived=1,state='locked',superseded_by=NULL WHERE id=?",
        values,
    )


def issue(database, values):
    return database.execute(
        "SELECT * FROM repertoire_integrity_issues WHERE id=? AND repertoire_id=?",
        values,
    ).fetchone()


def prepared_lines(database, values):
    return database.execute(
        "SELECT * FROM repertoire_lines WHERE repertoire_id=?",
        values,
    ).fetchall()


def prepared_cards(database, values):
    return database.execute(
        """SELECT DISTINCT c.* FROM cards c
                   LEFT JOIN repertoire_cards rc ON rc.card_id=c.id
                   WHERE c.content_type='opening' AND c.archived=0
                     AND (c.repertoire_id=? OR rc.repertoire_id=?)""",
        values,
    ).fetchall()
