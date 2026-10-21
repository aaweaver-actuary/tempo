"""Existing defensive-analysis settings/read predicates; no application imports."""


def analysis_setting(database):
    return database.execute('SELECT defensive_analysis_enabled FROM settings WHERE id=1').fetchone()


def task_admission_sql(kind_expression: str, task_kinds) -> str:
    kinds = ','.join("'" + kind + "'" for kind in task_kinds)
    return (f"(({kind_expression}='daily_queue' OR (SELECT study_only_mode FROM settings WHERE id=1)=0) AND "
            f"({kind_expression} NOT IN ({kinds}) OR "
            "(SELECT defensive_analysis_enabled FROM settings WHERE id=1)=1))")


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
    return ('((SELECT study_only_mode FROM settings WHERE id=1)=0 AND '
            '((SELECT defensive_analysis_enabled FROM settings WHERE id=1)=1 OR '
            + recommendation_sql(request_id_expression) + '))')


def recommendation_request_ids_sql() -> str:
    """Drive paused claims from recommendation relations, rather than scanning the defensive backlog."""
    return ' UNION '.join(
        f"SELECT recommendation.request_id FROM {table} recommendation "
        "JOIN repertoire_opportunities opportunity ON opportunity.id=recommendation.opportunity_id "
        "WHERE opportunity.status='active' AND opportunity.card_id IS NULL"
        for table in ('discovery_recommendation_requests', 'coverage_discovery_recommendation_requests')
    )
