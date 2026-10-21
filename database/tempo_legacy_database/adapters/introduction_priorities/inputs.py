"""Complete existing priority-input projections on borrowed read sections."""
from tempo_legacy_database.adapters.games.derivation.priorities import game_color
from tempo_legacy_database.adapters.repertoire_scope.freshness import coverage_scope_predicate


def coverage_run(database, repertoire_id):
    return database.execute(
        """SELECT id,settings_json FROM repertoire_coverage_runs WHERE repertoire_id=?
           ORDER BY CASE WHEN status='complete' THEN 0 ELSE 1 END,
                    created_at DESC LIMIT 1""",
        (repertoire_id,),
    ).fetchone()


def coverage_nodes(database, run_id):
    return database.execute(
        """SELECT * FROM repertoire_coverage_nodes WHERE run_id=?""", (run_id,)
    ).fetchall()


def coverage_candidates(database, node_id):
    return database.execute(
            """SELECT move_uci,explorer_probability,maia_probability
               FROM repertoire_coverage_candidates WHERE node_id=?""",
            (node_id,),
        ).fetchall()


def personal_evidence_rows(database, fen_keys, trained_color, supported_speeds):
    placeholders = ",".join("?" for _ in fen_keys)
    return database.execute(
        f"""SELECT p.fen_key,p.move_uci,g.played_at
            FROM game_position_occurrences p JOIN imported_games g ON g.id=p.game_id
            WHERE p.fen_key IN ({placeholders}) AND p.move_uci IS NOT NULL
              AND g.color=? AND g.adaptive_excluded=0
              AND g.speed IN ({",".join("?" for _ in supported_speeds)})""",
        (*sorted(fen_keys), trained_color, *supported_speeds),
    ).fetchall()


def repertoire_lines(database, repertoire_id):
    return database.execute(
                "SELECT * FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
                (repertoire_id,),
            )


def active_opening_cards(database, repertoire_id):
    return database.execute(
                """SELECT DISTINCT c.* FROM cards c
                   JOIN repertoire_cards rc ON rc.card_id=c.id
                   WHERE rc.repertoire_id=? AND c.content_type='opening' AND c.archived=0""",
                (repertoire_id,),
            )


def priority_settings(database):
    return database.execute(
                "SELECT coverage_horizon_fullmoves,coverage_path_floor FROM settings WHERE id=1"
            ).fetchone()


def personal_evidence_page(database, selected_keys, trained_color, supported_speeds, repertoire_id, last_position, row_limit):
    placeholders = ",".join("?" for _ in selected_keys)
    cursor_clause = " AND (p.fen_key,p.game_id,p.ply)>(?,?,?)" if last_position else ""
    scope_clause = " AND EXISTS(SELECT 1 FROM current_game_repertoire_matches match WHERE match.game_id=p.game_id AND match.repertoire_id=?)" if repertoire_id else ""
    return database.execute(
                    f"""SELECT p.fen_key,p.game_id,p.ply,p.move_uci,g.played_at
                    FROM game_position_occurrences p
                    JOIN imported_games g ON g.id=p.game_id
                    WHERE p.fen_key IN ({placeholders}) AND p.move_uci IS NOT NULL
                      AND g.color=? AND g.adaptive_excluded=0
                      AND g.speed IN ({','.join('?' for _ in supported_speeds)})
                      {scope_clause} {cursor_clause}
                    ORDER BY p.fen_key,p.game_id,p.ply LIMIT ?""",
                    (*selected_keys, trained_color, *supported_speeds,
                     *([repertoire_id] if repertoire_id else []), *(last_position or ()), row_limit),
                ).fetchall()


def compatibility_opening_cards(database, repertoire_id):
    return database.execute(
            """SELECT DISTINCT c.* FROM cards c JOIN repertoire_cards rc ON rc.card_id=c.id
               WHERE rc.repertoire_id=? AND c.content_type='opening' AND c.archived=0""",
            (repertoire_id,),
        )


def repertoire_ids_for_color(database, color):
    return database.execute(
                "SELECT DISTINCT repertoire_id FROM repertoire_lines WHERE trained_color=?",
                (color,),
            )


def coverage_error(database, repertoire_id):
    return database.execute(
        f"""SELECT last_error FROM repertoire_coverage_runs r WHERE r.repertoire_id=?
           AND {coverage_scope_predicate(database, repertoire_id='r.repertoire_id')}
           ORDER BY created_at DESC LIMIT 1""",
        (repertoire_id,),
    ).fetchone()
