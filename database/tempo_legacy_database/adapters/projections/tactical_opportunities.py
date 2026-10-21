"""Fixed SQLite/PostgreSQL tactical query projections; response metrics stay outside."""


def sqlite_tactical_rows(database, filters: dict[str, str | None]):
    clauses = [
        "o.active=1", "o.outcome IN ('exploited','missed')",
        "g.adaptive_excluded=0", "g.analysis_state IN ('ready','complete')",
        "o.analysis_version=g.analysis_version",
    ]
    parameters: list[str] = []
    for column, key in (("g.played_at", "from_date"), ("g.played_at", "to_date")):
        value = filters.get(key)
        if value:
            clauses.append(f"date({column}) {'>=' if key == 'from_date' else '<='} date(?)")
            parameters.append(value)
    for column, key in (("g.provider", "provider"), ("g.speed", "speed"), ("g.color", "color"), ("o.motif", "motif"), ("o.outcome", "outcome")):
        value = filters.get(key)
        if value:
            clauses.append(f"{column}=?")
            parameters.append(value)
    rows = database.execute(
            f"""SELECT o.*,g.played_at,g.provider,g.speed,g.color
                FROM tactical_opportunities o JOIN imported_games g ON g.id=o.game_id
                WHERE {' AND '.join(clauses)}
                ORDER BY g.played_at DESC,o.ply,o.motif""", parameters,
        ).fetchall()
    return rows


def postgres_tactical_rows(database, filters: dict[str, str | None]):
    clauses = [
        "o.active=1", "o.outcome IN ('exploited','missed')",
        "g.adaptive_excluded=0", "g.analysis_state IN ('ready','complete')",
        "o.analysis_version=g.analysis_version",
    ]
    parameters: list[str] = []
    for key, operator in (("from_date", ">="), ("to_date", "<=")):
        value = filters.get(key)
        if value:
            clauses.append(f"LEFT(g.played_at,10) {operator} %s")
            parameters.append(value)
    for column, key in (("g.provider", "provider"), ("g.speed", "speed"),
                        ("g.color", "color"), ("o.motif", "motif"),
                        ("o.outcome", "outcome")):
        value = filters.get(key)
        if value:
            clauses.append(f"{column}=%s")
            parameters.append(value)
    where = " AND ".join(clauses)
    aggregates = database.execute_native(
            "SELECT o.motif,COUNT(*) AS opportunities,"
            "COUNT(*) FILTER (WHERE o.outcome='exploited') AS exploited,"
            "COUNT(*) FILTER (WHERE o.outcome='missed') AS missed,"
            "AVG(o.evaluation_loss_cp) FILTER (WHERE o.outcome='missed') AS missed_cost,"
            "COUNT(DISTINCT o.game_id) AS supporting_games,"
            "AVG(o.confidence) AS mean_confidence,MIN(o.confidence) AS minimum_confidence "
            "FROM tactical_opportunities o JOIN imported_games g ON g.id=o.game_id "
            f"WHERE {where} GROUP BY ROLLUP(o.motif)",
            parameters,
        ).fetchall()
    pin_rows = database.execute_native(
            "SELECT o.game_id,o.outcome,o.confidence,o.evaluation_loss_cp,o.evidence_json "
            "FROM tactical_opportunities o JOIN imported_games g ON g.id=o.game_id "
            f"WHERE {where} AND o.motif='pin'",
            parameters,
        ).fetchall()
    return aggregates, pin_rows
