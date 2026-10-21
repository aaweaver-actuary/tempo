"""Existing HTTP card revision/split/archive statements, without application policy."""

from tempo_legacy_database.adapters.foreground_controls import prefix_splits as prefix_split_storage


def published_usage(database, identifier):
    return database.execute(
        """SELECT COUNT(DISTINCT step.line_id) line_count,
                          COUNT(DISTINCT step.repertoire_id) repertoire_count
                   FROM opening_graph_steps step
                   JOIN opening_graph_publications publication
                     ON publication.repertoire_id=step.repertoire_id
                    AND publication.generation=step.generation
                   WHERE step.card_id=?""",
        (identifier,),
    ).fetchone()


def split_repertoire_rows(database, parent_card_id, continuation_card_id):
    yield from prefix_split_storage.linked_repertoire_rows(database, parent_card_id, continuation_card_id)


def latest_failed_review_id(database, identifier):
    return database.execute(
        """SELECT COALESCE(MAX(id),0) FROM reviews
                   WHERE card_id=? AND rating='again' AND source_kind='study'
                     AND invalidated_at IS NULL""",
        (identifier,),
    ).fetchone()[0]


def affected_repertoire_rows(database, identifier):
    yield from database.execute(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? "
            "UNION SELECT repertoire_id FROM cards WHERE id=? ORDER BY repertoire_id",
        (identifier, identifier),
    )


def record_revision(database, identifier, revision, start_fen, moves_json, history_mode, now):
    database.execute(
        "INSERT OR IGNORE INTO card_revisions(card_id,revision,start_fen,moves_json,history_mode,created_at) VALUES(?,?,?,?,?,?)",
        (identifier, revision, start_fen, moves_json, history_mode, now),
    )


def revise_existing_definition(database, starting_fen, moves_json, source_fen, revision, identifier):
    database.execute(
        "UPDATE cards SET start_fen=?,moves_json=?,source_fen=?,revision=?,canonical_route_source=1 WHERE id=?",
        (starting_fen, moves_json, source_fen, revision, identifier),
    )


def complete_queued_card(database, identifier):
    database.execute(
        "UPDATE daily_queue SET status='complete' WHERE card_id=? AND status='queued'",
        (identifier,),
    )


def reassign_queued_card(database, replacement, identifier):
    database.execute(
        "UPDATE daily_queue SET card_id=? WHERE card_id=? AND status='queued'",
        (replacement, identifier),
    )


def copy_source_memberships(database, replacement, identifier):
    database.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) SELECT repertoire_id,?,1 FROM repertoire_cards WHERE card_id=? ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1",
        (replacement, identifier),
    )


def ensure_canonical_membership(database, repertoire_id, replacement):
    database.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1) ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1",
        (repertoire_id, replacement),
    )


def mark_pending_validation(database, replacement):
    database.execute(
        "UPDATE cards SET pending_validation=1 WHERE id=?",
        (replacement,),
    )


def archive_membership_rows(database, identifier):
    yield from database.execute(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=?",
        (identifier,),
    )


def archive_card(database, identifier):
    return database.execute(
        "UPDATE cards SET archived=1 WHERE id=?",
        (identifier,),
    ).rowcount
