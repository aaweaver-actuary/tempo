"""Persisted defensive-only admission policy; recommendations share engine requests."""

from fastapi import HTTPException

DEFENSIVE_TASK_KINDS = (
    'defensive_threat_scan', 'defensive_threat_validate', 'defensive_threat_backfill',
    'defensive_threat_report_audit', 'defensive_rubric_audit', 'defensive_admission',
)


def analysis_enabled(database) -> bool:
    setting = database.execute('SELECT defensive_analysis_enabled FROM settings WHERE id=1').fetchone()
    if setting is None:
        raise HTTPException(503, 'Defensive analysis settings are unavailable; restore the database and retry')
    return bool(setting[0])


def task_admission_sql(kind_expression: str) -> str:
    kinds = ','.join("'" + kind + "'" for kind in DEFENSIVE_TASK_KINDS)
    return (f"({kind_expression} NOT IN ({kinds}) OR "
            "(SELECT defensive_analysis_enabled FROM settings WHERE id=1)=1)")


def recommendation_sql(request_id_expression: str) -> str:
    # Both relations can refer to the same immutable engine request as a defensive candidate.
    return '(' + ' OR '.join(
        f"EXISTS(SELECT 1 FROM {table} recommendation "
        "JOIN repertoire_opportunities opportunity ON opportunity.id=recommendation.opportunity_id "
        f"WHERE recommendation.request_id={request_id_expression} AND opportunity.status='active' "
        "AND opportunity.card_id IS NULL LIMIT 1 OFFSET 0)"
        for table in ('discovery_recommendation_requests', 'coverage_discovery_recommendation_requests')
    ) + ')'


def search_admission_sql(request_id_expression: str) -> str:
    return ('((SELECT defensive_analysis_enabled FROM settings WHERE id=1)=1 OR '
            + recommendation_sql(request_id_expression) + ')')


def recommendation_request_ids_sql() -> str:
    """Drive paused claims from recommendation relations, rather than scanning the defensive backlog."""
    return ' UNION '.join(
        f"SELECT recommendation.request_id FROM {table} recommendation "
        "JOIN repertoire_opportunities opportunity ON opportunity.id=recommendation.opportunity_id "
        "WHERE opportunity.status='active' AND opportunity.card_id IS NULL"
        for table in ('discovery_recommendation_requests', 'coverage_discovery_recommendation_requests')
    )
