"""Fixed legacy Games projection/mutation queries; application decisions stay outside."""

from .. import postgres_store


GAME_PUBLIC_SELECT = """g.id,g.provider,g.username,g.played_at,g.speed,g.rated,
    g.color,g.result,g.start_fen,g.moves_json,g.game_url,g.opening_name,
    g.analysis_state,g.analysis_version,g.major_mistake_ply,g.missed_punishment_ply,
    m.repertoire_id,m.classification,
    m.first_player_deviation_ply AS divergence_ply,
    m.first_player_deviation_fen AS divergence_fen,
    m.first_player_deviation_expected_json AS expected_json,
    m.first_player_deviation_actual_uci AS actual_uci,
    m.deviation_card_id,m.matched_player_decisions,m.repertoire_opportunities,
    m.deepest_covered_ply,m.first_opponent_gap_ply,m.out_of_book_ply,m.timeline_json"""

GAME_SUMMARY_SELECT = """g.id,g.provider,g.played_at,g.speed,g.color,g.result,
    g.opening_name,g.analysis_state,g.major_mistake_ply,g.missed_punishment_ply,
    m.repertoire_id,m.classification,m.first_player_deviation_ply AS divergence_ply,
    m.matched_player_decisions,m.repertoire_opportunities"""


def summary_page(
    database, position_key, repertoire_id, provider, status, color, speed, outcome,
    played_from, cursor_played_at, cursor_id, limit,
):
    clauses: list[str] = []
    parameters: list[object] = []
    if position_key is not None:
        clauses.append(
            "EXISTS(SELECT 1 FROM game_position_occurrences p "
            "WHERE p.game_id=g.id AND p.fen_key=?)"
        )
        parameters.append(position_key)
    if repertoire_id:
        clauses.append("m.repertoire_id=?")
        parameters.append(repertoire_id)
    filters = {
        "g.provider": provider, "m.classification": status, "g.color": color,
        "g.speed": speed,
    }
    for column, value in filters.items():
        if value:
            clauses.append(f"{column}=?")
            parameters.append(value)
    if outcome:
        if outcome == "draw":
            clauses.append("g.result='1/2-1/2'")
        elif outcome == "won":
            clauses.append("((g.color='white' AND g.result='1-0') OR (g.color='black' AND g.result='0-1'))")
        elif outcome == "lost":
            clauses.append("((g.color='white' AND g.result='0-1') OR (g.color='black' AND g.result='1-0'))")
    if played_from:
        clauses.append("g.played_at>=?")
        parameters.append(played_from)
    if cursor_played_at and cursor_id:
        clauses.append("(g.played_at<? OR (g.played_at=? AND g.id<?))")
        parameters.extend([cursor_played_at, cursor_played_at, cursor_id])
    where = " AND ".join(clauses) if clauses else "1=1"
    rows = database.execute(
        f"""SELECT {GAME_SUMMARY_SELECT}
               FROM imported_games g
               LEFT JOIN current_game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1
               WHERE {where}
               ORDER BY g.played_at DESC,g.id DESC LIMIT ?""",
        (*parameters, limit + 1),
    ).fetchall()
    count_clauses = clauses[:-1] if cursor_played_at and cursor_id else clauses
    count_parameters = parameters[:-3] if cursor_played_at and cursor_id else parameters
    total = database.execute(
        f"""SELECT COUNT(*) FROM imported_games g
                 LEFT JOIN current_game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1
                 WHERE {' AND '.join(count_clauses) if count_clauses else '1=1'}""",
        count_parameters,
    ).fetchone()[0]
    return rows, total


def matched_position_ply(database, game_id, repertoire_id, position_key):
    return database.execute(
        """SELECT COALESCE(
                     (SELECT MIN(event.ply) FROM current_repertoire_decision_events event
                      WHERE event.game_id=? AND event.repertoire_id=? AND event.fen_key=?),
                     (SELECT MIN(occurrence.ply) FROM game_position_occurrences occurrence
                      WHERE occurrence.game_id=? AND occurrence.fen_key=?))""",
        (game_id, repertoire_id, position_key, game_id, position_key),
    ).fetchone()


def position_query_mode():
    return postgres_store.configured()


def position_occurrences(database, position_key, repertoire_id, postgres_mode):
    repertoire_filter = (
        "(%s::text IS NULL OR match.repertoire_id=%s)" if postgres_mode
        else "(? IS NULL OR match.repertoire_id=?)"
    )
    execute = database.execute_native if postgres_mode else database.execute
    return execute(
        f"""SELECT p.game_id,p.ply,p.move_uci,g.result,g.color,
                      a.loss_cp,a.label
               FROM game_position_occurrences p
               JOIN imported_games g ON g.id=p.game_id
               LEFT JOIN current_game_repertoire_matches match ON match.game_id=g.id AND match.is_primary=1
               LEFT JOIN game_move_analysis a ON a.game_id=p.game_id AND a.ply=p.ply
               WHERE p.fen_key={'%s' if postgres_mode else '?'}
                 AND {repertoire_filter}
               ORDER BY g.played_at DESC""",
        (position_key, repertoire_id, repertoire_id),
    ).fetchall()


def public_game_row(database, game_id):
    return database.execute(
        f"""SELECT {GAME_PUBLIC_SELECT}
               FROM imported_games g LEFT JOIN current_game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1
               WHERE g.id=?""",
        (game_id,),
    ).fetchone()
