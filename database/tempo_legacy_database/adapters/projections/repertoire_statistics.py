"""Read-only stored facts for repertoire metrics; date/window/metric decisions stay outside."""
from tempo_legacy_database.adapters.opening_progression import PRACTICED_OPENING_PARENT_SQL


def repertoire_identity(database, repertoire_id: str):
    return database.execute("SELECT id,name FROM repertoires WHERE id=?", (repertoire_id,)).fetchone()


def opening_publication(database, repertoire_id: str):
    return database.execute("SELECT generation,published_at FROM opening_graph_publications WHERE repertoire_id=?", (repertoire_id,)).fetchone()


def opening_graph_task(database, repertoire_id: str):
    return database.execute("SELECT state,last_error FROM background_tasks WHERE kind='opening_graph_rebuild' AND deduplication_key=?", (repertoire_id,)).fetchone()


def game_refresh_task(database):
    return database.execute("SELECT state,last_error FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'", ()).fetchone()


def pending_derivation_count(database):
    return database.execute("SELECT COUNT(*) FROM game_derivation_jobs WHERE status IN ('queued','running')").fetchone()[0]


def failed_derivation_count(database):
    return database.execute("SELECT COUNT(*) FROM game_derivation_jobs WHERE status='failed'").fetchone()[0]


def published_card_rows(database, repertoire_id: str):
    return [dict(row) for row in database.execute(
            """SELECT DISTINCT card.* FROM opening_graph_steps step
               JOIN opening_graph_publications published ON published.repertoire_id=step.repertoire_id AND published.generation=step.generation
               JOIN cards card ON card.id=step.card_id
               JOIN repertoire_cards link ON link.repertoire_id=step.repertoire_id AND link.card_id=card.id
               WHERE step.repertoire_id=? AND card.content_type='opening' AND card.archived=0""", (repertoire_id,),
        )]


def blocked_card_ids(database, repertoire_id: str):
    return {row[0] for row in database.execute("SELECT card_id FROM repertoire_integrity_card_blocks WHERE repertoire_id=?", (repertoire_id,))}


def valid_review_rows(database, repertoire_id: str):
    return [dict(row) for row in database.execute(
            """SELECT review.id,review.card_id,review.rating,review.reviewed_at,review.source_kind
               FROM reviews review JOIN cards card ON card.id=review.card_id
               JOIN repertoire_cards link ON link.card_id=card.id
               WHERE link.repertoire_id=? AND review.invalidated_at IS NULL
               ORDER BY review.reviewed_at,review.id""", (repertoire_id,),
        )]


def study_timezone(database):
    return database.execute("SELECT timezone FROM settings WHERE id=1").fetchone()[0]


def primary_game_rows(database, repertoire_id: str):
    return [dict(row) for row in database.execute(
            """SELECT game.id,game.played_at,game.result,game.color FROM current_game_repertoire_matches match
               JOIN imported_games game ON game.id=match.game_id
               WHERE match.repertoire_id=? AND match.is_primary=1 AND game.adaptive_excluded=0""", (repertoire_id,),
        )]


def primary_decision_rows(database, repertoire_id: str):
    return [dict(row) for row in database.execute(
            """SELECT event.game_id,event.card_id,event.fen_key,event.outcome,event.played_at
               FROM current_repertoire_decision_events event JOIN imported_games game ON game.id=event.game_id
               JOIN current_game_repertoire_matches match ON match.game_id=event.game_id
                 AND match.repertoire_id=event.repertoire_id AND match.is_primary=1
               WHERE event.repertoire_id=? AND game.adaptive_excluded=0""", (repertoire_id,),
        )]


def published_position_count(database, repertoire_id: str):
    return database.execute(
            """SELECT COUNT(DISTINCT position.value) FROM opening_graph_steps step
               JOIN opening_graph_publications published ON published.repertoire_id=step.repertoire_id AND published.generation=step.generation
               JOIN cards card ON card.id=step.card_id AND card.content_type='opening' AND card.archived=0
               JOIN repertoire_cards link ON link.repertoire_id=step.repertoire_id AND link.card_id=card.id
               JOIN json_each(step.decision_fen_keys_json) position
               WHERE step.repertoire_id=?""", (repertoire_id,),
        ).fetchone()[0]


def frontier_rows(database, repertoire_id: str):
    return [dict(row) for row in database.execute(
            f"""SELECT DISTINCT child.card_id,child.parent_card_id,line.name line_name,
                      parent.state,parent.due_date,parent.pending_validation,
                      parent.archived parent_archived,candidate.pending_validation child_pending_validation,
                      CASE WHEN EXISTS(
                          SELECT 1 FROM opening_graph_steps incoming_route
                          JOIN opening_graph_publications incoming_publication
                            ON incoming_publication.repertoire_id=incoming_route.repertoire_id
                           AND incoming_publication.generation=incoming_route.generation
                          JOIN cards parent ON parent.id=incoming_route.parent_card_id
                          WHERE incoming_route.card_id=child.card_id AND {PRACTICED_OPENING_PARENT_SQL}
                      ) THEN 1 ELSE 0 END parent_practiced
               FROM opening_graph_steps child
               JOIN opening_graph_publications published ON published.repertoire_id=child.repertoire_id AND published.generation=child.generation
               JOIN cards candidate ON candidate.id=child.card_id AND candidate.state IN ('locked','new')
                   AND candidate.introduced_at IS NULL AND candidate.archived=0
               JOIN cards parent ON parent.id=child.parent_card_id
               JOIN repertoire_lines line ON line.id=child.line_id
               WHERE child.repertoire_id=? AND parent.state!='locked'""", (repertoire_id,),
        )]


def repertoire_presence(database, repertoire_id: str):
    return database.execute("SELECT 1 FROM repertoires WHERE id=?", (repertoire_id,)).fetchone()


def known_position_keys(database, repertoire_id: str):
    return {row[0] for row in database.execute(
            """SELECT DISTINCT position.value FROM opening_graph_steps step
               JOIN opening_graph_publications published ON published.repertoire_id=step.repertoire_id AND published.generation=step.generation
               JOIN cards card ON card.id=step.card_id AND card.content_type='opening' AND card.archived=0
               JOIN repertoire_cards link ON link.repertoire_id=step.repertoire_id AND link.card_id=card.id
               JOIN json_each(step.decision_fen_keys_json) position WHERE step.repertoire_id=?""", (repertoire_id,),
        )}


def published_card_ids(database, repertoire_id: str):
    return {row[0] for row in database.execute(
            """SELECT DISTINCT step.card_id FROM opening_graph_steps step
               JOIN opening_graph_publications published ON published.repertoire_id=step.repertoire_id AND published.generation=step.generation
               JOIN cards card ON card.id=step.card_id AND card.content_type='opening' AND card.archived=0
               JOIN repertoire_cards link ON link.repertoire_id=step.repertoire_id AND link.card_id=card.id
               WHERE step.repertoire_id=?""", (repertoire_id,),
        )}


def position_decision_rows(database, repertoire_id: str):
    return [dict(row) for row in database.execute(
            """SELECT event.fen_key,event.expected_uci,event.actual_uci,event.outcome,event.card_id,
                      event.game_id,event.ply,event.played_at
               FROM current_repertoire_decision_events event
               JOIN imported_games game ON game.id=event.game_id
               JOIN current_game_repertoire_matches match ON match.game_id=event.game_id
                 AND match.repertoire_id=event.repertoire_id AND match.is_primary=1
               WHERE event.repertoire_id=? AND game.adaptive_excluded=0""", (repertoire_id,),
        )]
