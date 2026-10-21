"""Complete legacy finding queries, without motif/priority/recommendation policy."""


def upsert_finding_values(database, values):
    database.execute(
        """INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,repertoire_id,card_id,motif,source_opportunity_id,created_at,updated_at,game_scope_generation)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET confidence=excluded.confidence,evidence_json=excluded.evidence_json,
           repertoire_id=excluded.repertoire_id,game_scope_generation=excluded.game_scope_generation,
           card_id=CASE WHEN excluded.kind='repertoire lapse' THEN excluded.card_id
                        ELSE COALESCE(excluded.card_id,game_findings.card_id) END,
           motif=excluded.motif,
           source_opportunity_id=excluded.source_opportunity_id,updated_at=excluded.updated_at""",
        values,
    )


def comparison_games(database, game_id):
    where = "WHERE g.id=?" if game_id else ""
    return [dict(row) for row in database.execute(
        f"""SELECT g.*,m.repertoire_id,m.first_player_deviation_ply,m.first_player_deviation_fen,
                       m.first_player_deviation_expected_json,m.first_player_deviation_actual_uci,m.deviation_card_id
                       ,m.first_opponent_gap_ply,m.out_of_book_ply,m.timeline_json
                FROM imported_games g LEFT JOIN current_game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1 {where}""",
        (game_id,) if game_id else (),
    ).fetchall()]


def unseen_cards(database):
    return database.execute(
        """SELECT c.id,c.start_fen,c.moves_json FROM cards c
                   WHERE c.content_type='opening' AND c.archived=0 AND c.introduced_at IS NULL
                     AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)"""
    ).fetchall()


def unseen_card_page(database, last_card_id):
    return database.execute(
        """SELECT c.id,c.start_fen,c.moves_json FROM cards c
                       WHERE c.id>? AND c.content_type='opening' AND c.archived=0
                         AND c.introduced_at IS NULL
                         AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
                       ORDER BY c.id LIMIT 64""",
        (last_card_id,),
    ).fetchall()


def begin_finding_publication(database):
    database.execute("BEGIN IMMEDIATE")


def supersede_old_opportunities(database, game_id, analysis_version, now):
    database.execute(
        "UPDATE tactical_opportunities SET active=0,superseded_at=?,updated_at=? WHERE game_id=? AND active=1 AND analysis_version!=?",
        (now, now, game_id, analysis_version),
    )


def upsert_opportunity_values(database, values):
    database.execute(
        """INSERT INTO tactical_opportunities(
                    id,game_id,analysis_version,ply,motif,outcome,confidence,opportunity_value_cp,
                    evaluation_loss_cp,played_move_uci,accepted_moves_json,evidence_json,engine_version,
                    network_version,active,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(id) DO UPDATE SET outcome=excluded.outcome,confidence=excluded.confidence,
                    opportunity_value_cp=excluded.opportunity_value_cp,evaluation_loss_cp=excluded.evaluation_loss_cp,
                    played_move_uci=excluded.played_move_uci,accepted_moves_json=excluded.accepted_moves_json,
                    evidence_json=excluded.evidence_json,engine_version=excluded.engine_version,
                    network_version=excluded.network_version,active=1,superseded_at=NULL,updated_at=excluded.updated_at""",
        values,
    )


def upsert_priority_values(database, priority):
    database.execute(
        """INSERT INTO gameplay_card_priorities(
                       card_id,source_game_id,finding_id,priority_date,reason,created_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(card_id) DO UPDATE SET
                       source_game_id=excluded.source_game_id,
                       finding_id=excluded.finding_id,
                       priority_date=MIN(gameplay_card_priorities.priority_date,excluded.priority_date),
                       reason=excluded.reason""",
        priority,
    )


def motif_opportunities(database, window_start):
    return database.execute(
        """SELECT e.*,g.color AS player_color,g.played_at
                 FROM gameplay_events e
                 JOIN imported_games g ON g.id=e.game_id
                WHERE g.adaptive_excluded=0 AND g.played_at>=?
                  AND e.kind='tactical opportunity' AND e.confidence>=0.8
                  AND e.motif!='unclassified' AND e.beneficiary_color=g.color""",
        (window_start,),
    ).fetchall()
