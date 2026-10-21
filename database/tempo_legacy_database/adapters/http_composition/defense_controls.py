"""Fixed legacy publication/defense queries on the caller's borrowed connection."""

from ..controls.claim_sql import claimable
from ..defensive_analysis import search_admission_sql
from ..games.mutations import analyzed_game


def engine_request(database, request_id):
    return database.execute(
        f"""SELECT state,lease_id,
                    ({search_admission_sql('request.id')} AND
                     {claimable('threat_analysis', 'request.id')}) AS search_allowed
                FROM threat_analysis_requests request WHERE request.id=?""",
        (request_id,),
    ).fetchone()


def fail_request(database, error, now, request_id, lease_id):
    return database.execute(
        """UPDATE threat_analysis_requests SET state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,
                  last_error=?,lease_id=NULL,
                  lease_expires_at=NULL,updated_at=?
               WHERE id=? AND state='leased' AND lease_id=?""",
        (error, now, request_id, lease_id),
    ).rowcount


def request_state(database, request_id):
    return database.execute(
        "SELECT state FROM threat_analysis_requests WHERE id=?",
        (request_id,),
    ).fetchone()


def release_request(database, now, request_id, lease_id):
    return database.execute(
        """UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,
                  attempts=MAX(0,attempts-1),
                  lease_expires_at=NULL,updated_at=?
               WHERE id=? AND state='leased' AND lease_id=?""",
        (now, request_id, lease_id),
    ).rowcount


def retry_request(database, now, request_id):
    return database.execute(
        """UPDATE threat_analysis_requests SET state='queued',attempts=0,last_error=NULL,updated_at=?
               WHERE id=? AND state='failed'""",
        (now, request_id),
    ).rowcount


def current_candidates(database, game_id):
    game_filter = "AND c.game_id=?" if game_id is not None else ""
    return database.execute(
        f"""SELECT c.*,r.name AS card_repertoire_name FROM threat_training_candidates c
               JOIN imported_games g ON g.id=c.game_id
               LEFT JOIN cards card ON card.id=c.card_id
               LEFT JOIN repertoires r ON r.id=card.repertoire_id
               WHERE c.superseded_at IS NULL AND c.analysis_version=g.analysis_version
                 {game_filter}
               ORDER BY c.updated_at DESC,c.id LIMIT 100""",
        (game_id,) if game_id is not None else (),
    ).fetchall()


def candidate_requests(database, candidate_id):
    for row in database.execute(
        """SELECT relation.role,analysis.id,analysis.state,analysis.last_error
                   FROM threat_candidate_requests relation
                   JOIN threat_analysis_requests analysis ON analysis.id=relation.request_id
                   WHERE relation.candidate_id=? ORDER BY relation.role,analysis.id""",
        (candidate_id,),
    ):
        yield row
