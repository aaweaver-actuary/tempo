"""Exact engine scheduling queries and dialect handling on a borrowed connection."""
from ..defensive_analysis import search_admission_sql


ELIGIBLE_THREAT_REQUEST_SQL = (
    "SELECT request.id,request.request_json FROM threat_analysis_requests request "
    "LEFT JOIN background_activity control ON control.source='threat_analysis' "
    "AND control.work_id=request.id "
    "WHERE request.state='queued' AND COALESCE(control.paused,0)=0 "
    "AND " + search_admission_sql("request.id") + " "
    "AND (EXISTS(SELECT 1 FROM threat_candidate_requests relation "
    "JOIN threat_training_candidates candidate ON candidate.id=relation.candidate_id "
    "JOIN imported_games game ON game.id=candidate.game_id "
    "WHERE relation.request_id=request.id AND candidate.superseded_at IS NULL "
    "AND candidate.analysis_version=game.analysis_version "
    # OFFSET 0 keeps these as indexed per-request probes. Flattening the view
    # into a global semi-join scanned the entire restored backlog per claim.
    "AND (candidate.validation_state='needs_analysis' OR relation.role='attempt') "
    "LIMIT 1 OFFSET 0) "
    "OR EXISTS(SELECT 1 FROM discovery_recommendation_requests recommendation "
    "JOIN current_repertoire_opportunities opportunity ON opportunity.id=recommendation.opportunity_id "
    "WHERE recommendation.request_id=request.id AND opportunity.status='active' "
    "AND opportunity.card_id IS NULL LIMIT 1 OFFSET 0) "
    "OR EXISTS(SELECT 1 FROM coverage_discovery_recommendation_requests recommendation "
    "JOIN current_repertoire_opportunities opportunity ON opportunity.id=recommendation.opportunity_id "
    "WHERE recommendation.request_id=request.id AND opportunity.status='active' "
    "AND opportunity.card_id IS NULL LIMIT 1 OFFSET 0)) "
)


INTERACTIVE_REQUEST_FILTER_SQL = (
    "AND request.id IN (SELECT request_id FROM threat_candidate_requests WHERE role='attempt') "
)


ORDINARY_GAME_ELIGIBILITY_SQL = (
    "FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
    "LEFT JOIN background_activity control ON control.source='game_analysis' AND control.work_id=j.game_id "
    "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<?)) "
    "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
    "AND COALESCE(control.paused,0)=0 "
)


ORDINARY_GAME_ORDER_SQL = "ORDER BY COALESCE(control.promoted,0) DESC,g.played_at DESC,j.game_id "


def execute_scheduling_sql(database, statement, parameters=()):
    if hasattr(database, 'execute_native'):
        return database.execute_native(statement.replace('?', '%s'), parameters)
    return database.execute(statement, parameters)


def record_automated_selection(database):
    minimum = 'LEAST' if hasattr(database, 'execute_native') else 'MIN'
    execute_scheduling_sql(database,
        f'UPDATE engine_scheduling_state SET automated_streak={minimum}(3,automated_streak+1) WHERE id=1')


def record_ordinary_selection(database):
    execute_scheduling_sql(database,
        'UPDATE engine_scheduling_state SET automated_streak=0 WHERE id=1 AND automated_streak<>0')


def interactive_request_sql():
    return (ELIGIBLE_THREAT_REQUEST_SQL + INTERACTIVE_REQUEST_FILTER_SQL +
            'ORDER BY COALESCE(control.promoted,0) DESC,request.created_at,request.id LIMIT 1')


def scheduling_row(database):
    lock_suffix = ' FOR UPDATE SKIP LOCKED' if hasattr(database, 'execute_native') else ''
    return execute_scheduling_sql(database,
        'SELECT automated_streak FROM engine_scheduling_state WHERE id=1' + lock_suffix).fetchone()


def scheduling_state_exists(database):
    return execute_scheduling_sql(database, 'SELECT 1 FROM engine_scheduling_state WHERE id=1').fetchone()


def ordinary_game(database, now):
    lock_suffix = ' FOR UPDATE OF j,g SKIP LOCKED' if hasattr(database, 'execute_native') else ''
    return execute_scheduling_sql(database,
        'SELECT j.game_id ' + ORDINARY_GAME_ELIGIBILITY_SQL + ORDINARY_GAME_ORDER_SQL + 'LIMIT 1' + lock_suffix,
        (now,)).fetchone()


def interactive_request(database):
    lock_suffix = ' FOR UPDATE OF request SKIP LOCKED' if hasattr(database, 'execute_native') else ''
    return execute_scheduling_sql(database, interactive_request_sql() + lock_suffix).fetchone()
