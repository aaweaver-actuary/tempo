"""Complete legacy repertoire HTTP browsing and metadata persistence operations."""

from collections.abc import Callable


def repertoire_rows(database, system_repertoires, current_day: Callable[[], str]):
    system_placeholders = ",".join("?" for _ in system_repertoires)
    return database.execute(
        f"""
            WITH line_counts AS (
                SELECT repertoire_id,COUNT(*) AS line_count
                FROM repertoire_lines
                GROUP BY repertoire_id
            ), card_counts AS (
                SELECT repertoire_id,COUNT(*) AS card_count
                FROM repertoire_cards
                GROUP BY repertoire_id
            ), active_prefix_counts AS (
                SELECT step.repertoire_id,COUNT(DISTINCT card.id) AS active_prefix_count
                FROM opening_graph_steps step
                JOIN opening_graph_publications published ON published.repertoire_id=step.repertoire_id AND published.generation=step.generation
                JOIN cards card ON card.id=step.card_id
                JOIN repertoire_cards link ON link.repertoire_id=step.repertoire_id AND link.card_id=card.id
                WHERE card.kind='prefix' AND card.content_type='opening' AND card.archived=0
                  AND card.state!='locked' AND card.pending_validation=0
                  AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                 WHERE block.repertoire_id=step.repertoire_id AND block.card_id=card.id)
                GROUP BY step.repertoire_id
            ), due_counts AS (
                SELECT rc.repertoire_id,COUNT(DISTINCT q.card_id) AS due_count
                FROM daily_queue q
                JOIN repertoire_cards rc ON rc.card_id=q.card_id
                WHERE q.queue_date=? AND q.status='queued' AND NOT EXISTS(
                    SELECT 1 FROM repertoire_integrity_card_blocks block
                    WHERE block.repertoire_id=rc.repertoire_id AND block.card_id=q.card_id
                )
                GROUP BY rc.repertoire_id
            ), blocked_counts AS (
                SELECT block.repertoire_id,
                       COUNT(DISTINCT block.card_id) AS blocked_card_count,
                       COUNT(DISTINCT CASE WHEN card.due_date<=? AND card.state IN ('learning','mature')
                                          THEN block.card_id END) AS blocked_due_count
                FROM repertoire_integrity_card_blocks block
                JOIN cards card ON card.id=block.card_id AND card.archived=0
                GROUP BY block.repertoire_id
            ), issue_counts AS (
                SELECT repertoire_id,COUNT(*) AS issue_count,
                       SUM(CASE WHEN kind IN ('missing_response','multiple_responses','invalid_source')
                           THEN 1 ELSE 0 END) AS conflict_count
                FROM repertoire_integrity_issues
                GROUP BY repertoire_id
            )
            SELECT r.id,r.name,r.source_name,r.created_at,r.is_main,r.new_cards_per_day,
                   r.canonical_prefix_moves_json,r.canonical_prefix_revision,
                   COALESCE(r.new_cards_per_day,(SELECT new_cards_per_day FROM settings WHERE id=1)) effective_new_cards_per_day,
                   COALESCE(rs.status,'unchecked') integrity_status,
                   COALESCE(rs.scan_status,'idle') integrity_scan_status,rs.scan_error integrity_scan_error,
                   (SELECT ii.id FROM repertoire_integrity_issues ii WHERE ii.repertoire_id=r.id ORDER BY ii.updated_at,ii.id LIMIT 1) integrity_first_issue_id,
                   COALESCE(lc.line_count,0) AS line_count,
                   COALESCE(cc.card_count,0) AS card_count,
                   COALESCE(apc.active_prefix_count,0) AS active_prefix_count,
                   (SELECT l2.trained_color FROM repertoire_lines l2 WHERE l2.repertoire_id=r.id ORDER BY l2.created_at LIMIT 1) AS trained_color,
                   COALESCE(dc.due_count,0) AS due_count,
                   COALESCE(bc.blocked_due_count,0) AS blocked_due_count,
                   COALESCE(bc.blocked_card_count,0) AS blocked_card_count,
                   COALESCE(ic.issue_count,0) AS integrity_issue_count,
                   COALESCE(ic.conflict_count,0) AS conflict_count,
                   COALESCE(graph.generation,0) AS graph_generation,
                   graph.published_at AS graph_updated_at,
                   CASE
                     WHEN graph_task.state IN ('queued','leased','retrying') THEN 'refreshing'
                     WHEN graph_task.state='failed' THEN 'failed'
                     WHEN graph.generation IS NOT NULL THEN 'ready'
                     ELSE 'refreshing'
                   END AS graph_state,
                   graph_task.last_error AS graph_error
            FROM repertoires r
            LEFT JOIN repertoire_integrity_state rs ON rs.repertoire_id=r.id
            LEFT JOIN line_counts lc ON lc.repertoire_id=r.id
            LEFT JOIN card_counts cc ON cc.repertoire_id=r.id
            LEFT JOIN active_prefix_counts apc ON apc.repertoire_id=r.id
            LEFT JOIN due_counts dc ON dc.repertoire_id=r.id
            LEFT JOIN blocked_counts bc ON bc.repertoire_id=r.id
            LEFT JOIN issue_counts ic ON ic.repertoire_id=r.id
            LEFT JOIN opening_graph_publications graph ON graph.repertoire_id=r.id
            LEFT JOIN background_tasks graph_task
              ON graph_task.kind='opening_graph_rebuild'
             AND graph_task.deduplication_key=r.id
            WHERE r.id NOT IN ({system_placeholders}) OR (r.id='__retained_cards__' AND EXISTS(
                SELECT 1 FROM cards retained WHERE retained.repertoire_id=r.id))
            ORDER BY r.created_at DESC
        """,
        (current_day(), current_day(), *system_repertoires),
    ).fetchall()


def line_rows(database):
    return database.execute(
        """SELECT l.id,l.repertoire_id,l.name,l.trained_color,l.start_fen,l.moves_json,
                                  r.name repertoire_name,r.is_main
                           FROM repertoire_lines l JOIN repertoires r ON r.id=l.repertoire_id
                           WHERE r.id NOT IN ('__tactics__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__') ORDER BY r.created_at,l.created_at""",
    ).fetchall()


def comparison_card_rows(database):
    return database.execute(
        """SELECT c.id,c.start_fen,c.moves_json,c.state,c.kind,
                                  c.trained_color,r.id repertoire_id,r.name repertoire_name
                           FROM cards c
                           JOIN repertoire_cards rc ON rc.card_id=c.id
                           JOIN repertoires r ON r.id=rc.repertoire_id
                           WHERE c.content_type='opening' AND c.archived=0 AND r.id<>'__retained_cards__'
                             AND c.pending_validation=0
                           ORDER BY c.id,r.id""",
    ).fetchall()


def integrity_issue_signature(database, issue_id, identifier):
    return database.execute(
        "SELECT signature FROM repertoire_integrity_issues WHERE id=? AND repertoire_id=?",
        (issue_id, identifier),
    ).fetchone()


def position_annotation_rows(database, identifier, position_key):
    return database.execute(
        "SELECT * FROM position_annotations WHERE repertoire_id=? AND fen_key=?",
        (identifier, position_key),
    ).fetchall()


def annotation_rows(database, identifier):
    return database.execute(
        "SELECT * FROM position_annotations WHERE repertoire_id=? ORDER BY updated_at",
        (identifier,),
    ).fetchall()


def save_annotation(database, identifier, key, comment, arrows_json, squares_json, now):
    database.execute(
        """INSERT INTO position_annotations(repertoire_id,fen_key,comment,arrows_json,squares_json,updated_at)
                          VALUES(?,?,?,?,?,?) ON CONFLICT(repertoire_id,fen_key) DO UPDATE SET
                          comment=excluded.comment,arrows_json=excluded.arrows_json,squares_json=excluded.squares_json,updated_at=excluded.updated_at""",
        (identifier, key, comment, arrows_json, squares_json, now),
    )


def teaching_rows(database, identifier):
    return database.execute(
        "SELECT card_id,revision,ply,taught_at FROM teaching_states WHERE card_id=? ORDER BY revision,ply",
        (identifier,),
    ).fetchall()


def active_card_presence(database, identifier):
    return database.execute(
        "SELECT 1 FROM cards WHERE id=? AND archived=0",
        (identifier,),
    ).fetchone()


def record_teaching_state(database, identifier, revision, ply, taught_at):
    database.execute(
        "INSERT OR IGNORE INTO teaching_states(card_id,revision,ply,taught_at) VALUES(?,?,?,?)",
        (identifier, revision, ply, taught_at),
    )


def selectable_repertoire(database, identifier):
    return database.execute(
        "SELECT 1 FROM repertoires WHERE id=? AND id NOT IN ('__tactics__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__')",
        (identifier,),
    ).fetchone()


def select_main_repertoire(database, identifier):
    database.execute(
        "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END WHERE id NOT IN ('__tactics__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__')",
        (identifier,),
    )


def export_line_rows(database, repertoire_id):
    query = "SELECT l.*,r.name repertoire_name FROM repertoire_lines l JOIN repertoires r ON r.id=l.repertoire_id"
    return database.execute(
        query
            + (" WHERE l.repertoire_id=?" if repertoire_id else "")
            + (" ORDER BY l.created_at"),
        (repertoire_id,) if repertoire_id else (),
    ).fetchall()


def export_annotation_rows(database, repertoire_id):
    return database.execute(
        "SELECT * FROM position_annotations"
            + (" WHERE repertoire_id=?" if repertoire_id else ""),
        (repertoire_id,) if repertoire_id else (),
    ).fetchall()
