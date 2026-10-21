"""Existing SQLite-compatible graph source/seed/publisher operations; caller owns policy."""


def legacy_review_rows(database, values):
    return database.execute(
        'SELECT review.* FROM reviews review\n               JOIN repertoire_cards link ON link.card_id=review.card_id\n               WHERE link.repertoire_id=? ORDER BY review.reviewed_at DESC,review.id DESC',
        values,
    )


def clear_staging(database, values):
    database.execute(
        "DELETE FROM opening_graph_steps WHERE repertoire_id=? AND generation=?",
        values,
    )


def publish_generation(database, values):
    database.execute(
        "INSERT INTO opening_graph_publications(\n                   repertoire_id,generation,state,last_error,published_at\n               ) VALUES(?,?,'ready',NULL,?)\n               ON CONFLICT(repertoire_id) DO UPDATE SET\n                   generation=excluded.generation,state='ready',last_error=NULL,\n                   published_at=excluded.published_at",
        values,
    )


def insert_links(database, values):
    database.execute(
        'INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id,canonical_route_source)\n               SELECT repertoire_id,card_id,0 FROM opening_graph_steps\n               WHERE repertoire_id=? AND generation=?',
        values,
    )


def classify_generated_cards(database, values):
    database.execute(
        "UPDATE cards SET archived=0,\n                   kind=CASE WHEN EXISTS(\n                       SELECT 1 FROM opening_graph_steps prefix_step\n                       JOIN opening_graph_publications prefix_publication\n                         ON prefix_publication.repertoire_id=prefix_step.repertoire_id\n                        AND prefix_publication.generation=prefix_step.generation\n                       WHERE prefix_step.card_id=cards.id\n                         AND prefix_step.segment_kind='prefix'\n                   ) THEN 'prefix' ELSE 'response' END\n               WHERE canonical_route_source=0 AND id IN (\n                   SELECT card_id FROM opening_graph_steps\n                   WHERE repertoire_id=? AND generation=?\n               )",
        values,
    )


def lock_unintroduced_descendants(database):
    database.execute(
        "UPDATE cards SET state='locked'\n               WHERE content_type='opening' AND state='new'\n                 AND introduced_at IS NULL\n                 AND NOT EXISTS(SELECT 1 FROM reviews WHERE card_id=cards.id)\n                 AND EXISTS(\n                     SELECT 1 FROM opening_graph_steps descendant_step\n                     JOIN opening_graph_publications descendant_publication\n                       ON descendant_publication.repertoire_id=descendant_step.repertoire_id\n                      AND descendant_publication.generation=descendant_step.generation\n                     WHERE descendant_step.card_id=cards.id\n                       AND descendant_step.parent_card_id IS NOT NULL\n                 )\n                 AND NOT EXISTS(\n                     SELECT 1 FROM opening_graph_steps root_step\n                     JOIN opening_graph_publications root_publication\n                       ON root_publication.repertoire_id=root_step.repertoire_id\n                      AND root_publication.generation=root_step.generation\n                     WHERE root_step.card_id=cards.id\n                       AND root_step.parent_card_id IS NULL\n                 )"
    )


def unlock_unintroduced_roots(database, values):
    database.execute(
        "UPDATE cards SET state='new',due_date=?\n               WHERE content_type='opening' AND state='locked'\n                 AND introduced_at IS NULL\n                 AND NOT EXISTS(SELECT 1 FROM reviews WHERE card_id=cards.id)\n                 AND EXISTS(\n                     SELECT 1 FROM opening_graph_steps root_step\n                     JOIN opening_graph_publications root_publication\n                       ON root_publication.repertoire_id=root_step.repertoire_id\n                      AND root_publication.generation=root_step.generation\n                     WHERE root_step.card_id=cards.id\n                       AND root_step.parent_card_id IS NULL\n                 )",
        values,
    )


def retire_obsolete_queue(database, values):
    database.execute(
        "UPDATE daily_queue SET status='superseded'\n               WHERE status='queued' AND card_id IN (\n                   SELECT link.card_id FROM repertoire_cards link\n                   JOIN cards legacy ON legacy.id=link.card_id\n                   WHERE link.repertoire_id=? AND legacy.content_type='opening' AND link.canonical_route_source=0 AND (legacy.canonical_route_source=0 OR legacy.repertoire_id=link.repertoire_id)\n                     AND NOT EXISTS(SELECT 1 FROM repertoire_cards retained WHERE retained.card_id=legacy.id AND retained.repertoire_id<>link.repertoire_id)\n                     AND NOT EXISTS(\n                         SELECT 1 FROM opening_graph_steps step\n                         WHERE step.repertoire_id=? AND step.generation=?\n                           AND step.card_id=legacy.id\n                     )\n               )",
        values,
    )


def archive_generated_owner(database, values):
    database.execute(
        "UPDATE cards SET archived=1 WHERE repertoire_id=? AND content_type='opening'\n                 AND EXISTS(SELECT 1 FROM repertoire_cards former\n                     WHERE former.card_id=cards.id AND former.repertoire_id=? AND former.canonical_route_source=0)\n                 AND NOT EXISTS(SELECT 1 FROM repertoire_cards retained\n                     WHERE retained.card_id=cards.id AND retained.repertoire_id<>?)\n                 AND NOT EXISTS(SELECT 1 FROM opening_graph_steps step\n                     WHERE step.repertoire_id=? AND step.generation=? AND step.card_id=cards.id)",
        values,
    )


def reassign_retained_owner(database, values):
    database.execute(
        "UPDATE cards SET repertoire_id=(\n                   SELECT MIN(retained.repertoire_id) FROM repertoire_cards retained\n                   WHERE retained.card_id=cards.id AND retained.repertoire_id<>?\n               ) WHERE repertoire_id=? AND content_type='opening'\n                 AND EXISTS(SELECT 1 FROM repertoire_cards former\n                     WHERE former.card_id=cards.id AND former.repertoire_id=? AND former.canonical_route_source=0)\n                 AND EXISTS(SELECT 1 FROM repertoire_cards retained\n                     WHERE retained.card_id=cards.id AND retained.repertoire_id<>?)\n                 AND NOT EXISTS(SELECT 1 FROM opening_graph_steps step\n                     WHERE step.repertoire_id=? AND step.generation=? AND step.card_id=cards.id)",
        values,
    )


def delete_generated_links(database, values):
    database.execute(
        "DELETE FROM repertoire_cards\n               WHERE repertoire_id=? AND canonical_route_source=0 AND card_id IN (\n                   SELECT card.id FROM cards card WHERE card.content_type='opening'\n                     AND NOT EXISTS(\n                         SELECT 1 FROM opening_graph_steps step\n                         WHERE step.repertoire_id=? AND step.generation=?\n                           AND step.card_id=card.id\n                     )\n               )",
        values,
    )


def archive_unlinked_generated_cards(database):
    database.execute(
        "UPDATE cards SET archived=1\n               WHERE content_type='opening' AND archived=0 AND canonical_route_source=0\n                 AND NOT EXISTS(\n                     SELECT 1 FROM repertoire_cards link WHERE link.card_id=cards.id\n                 )"
    )


def delete_blocks(database, values):
    database.execute(
        "DELETE FROM repertoire_integrity_card_blocks WHERE repertoire_id=?",
        values,
    )


def insert_blocks(database, values):
    database.execute(
        'INSERT OR IGNORE INTO repertoire_integrity_card_blocks(\n                   repertoire_id,card_id,issue_id,scan_generation,published_at\n               )\n               SELECT ?,step.card_id,issue.id,?,?\n               FROM opening_graph_steps step\n               JOIN json_each(step.decision_fen_keys_json) decision_position\n               JOIN repertoire_integrity_issues issue\n                 ON issue.repertoire_id=step.repertoire_id\n                AND issue.fen_key=decision_position.value\n               WHERE step.repertoire_id=? AND step.generation=?',
        values,
    )


def refresh_validation(database, values):
    database.execute(
        'UPDATE cards SET pending_validation=CASE WHEN EXISTS(\n                   SELECT 1 FROM repertoire_integrity_card_blocks block\n                   WHERE block.card_id=cards.id\n               ) THEN 1 ELSE 0 END\n               WHERE id IN (\n                   SELECT card_id FROM opening_graph_steps\n                   WHERE repertoire_id=? AND generation=?\n               )',
        values,
    )


def insert_steps(database, values):
    database.executemany(
        'INSERT INTO opening_graph_steps(\n                       repertoire_id,generation,line_id,decision_index,\n                       segment_kind,first_decision_index,last_decision_index,\n                       decision_fen_keys_json,card_id,parent_card_id,\n                       decision_fen_key,starting_fen,moves_json,trained_color\n                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        values,
    )


def insert_cards(database, values):
    database.executemany(
        "INSERT OR IGNORE INTO cards(\n                       id,repertoire_id,kind,start_fen,moves_json,state,due_date,\n                       content_type,trained_color,pending_validation,canonical_route_source\n                   ) VALUES(?,?,?,?,?,?,?,'opening',?,0,0)",
        values,
    )


def insert_legacy_mappings(database, values):
    database.executemany(
        'INSERT OR IGNORE INTO opening_graph_legacy_mappings(\n                       repertoire_id,generation,legacy_card_id,decision_card_id\n                   ) VALUES(?,?,?,?)',
        values,
    )


def initial_depth_row(database):
    return database.execute(
        "SELECT initial_depth FROM settings WHERE id=1"
    ).fetchone()


def existing_card_rows(database):
    return database.execute(
        "SELECT id FROM cards"
    )


def apply_seed(database, values):
    database.execute(
        'UPDATE cards SET state=?,due_date=?,interval_days=?,stability=?,\n                           fsrs_card_json=?,first_correct_at=?,reinforcement_pending=0,\n                           introduced_at=?\n                       WHERE id=? AND NOT EXISTS(\n                           SELECT 1 FROM reviews WHERE card_id=cards.id\n                       )',
        values,
    )


def insert_seed(database, values):
    database.execute(
        'INSERT OR IGNORE INTO opening_card_schedule_seeds(\n                           card_id,source_card_id,baseline_successful_days,\n                           baseline_recent_clean,verification_due,created_at\n                       ) VALUES(?,?,?,?,?,?)',
        values,
    )


def task_generation(database, values):
    return database.execute(
        "SELECT generation FROM background_tasks WHERE id=? AND lease_token=?",
        values,
    ).fetchone()


def source_line_rows(database, values):
    return database.execute(
        'SELECT line.*,depth.learner_decision_count\n                   FROM repertoire_lines line\n                   LEFT JOIN repertoire_line_training_depths depth ON depth.line_id=line.id\n                   WHERE line.repertoire_id=? ORDER BY line.id',
        values,
    )


def prefix_override_rows(database):
    return database.execute(
        'SELECT split.source_card_id,split.shortened_card_id,\n                          shortened.start_fen shortened_start_fen,\n                          shortened.moves_json shortened_moves_json,\n                          split.continuation_card_id,\n                          continuation.start_fen continuation_start_fen,\n                          continuation.moves_json continuation_moves_json\n                   FROM prefix_splits split\n                   JOIN cards shortened ON shortened.id=split.shortened_card_id\n                   JOIN cards continuation ON continuation.id=split.continuation_card_id'
    )


def legacy_card_rows(database, values):
    return database.execute(
        "SELECT DISTINCT card.*,\n                          COALESCE(seed.baseline_successful_days,0) seed_successful_days,\n                          COALESCE(seed.baseline_recent_clean,0) seed_recent_clean\n                   FROM cards card\n                   JOIN repertoire_cards link ON link.card_id=card.id\n                   LEFT JOIN opening_card_schedule_seeds seed ON seed.card_id=card.id\n                   WHERE link.repertoire_id=? AND card.content_type='opening'\n                     AND card.archived=0",
        values,
    )


def deleted_card_rows(database):
    return database.execute("SELECT card_id FROM deleted_cards")


def leased_task(database, values):
    return database.execute("SELECT 1 FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'", values).fetchone()


def card_present(database, values):
    return database.execute("SELECT 1 FROM cards WHERE id=?", values).fetchone()
