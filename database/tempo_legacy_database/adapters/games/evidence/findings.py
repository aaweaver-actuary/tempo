"""Existing staged-finding reads and publication writes, without phase policy."""


def staged_item_count(database, game_id, version):
    return database.execute(
        "SELECT COUNT(*) FROM game_finding_publication_items "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    ).fetchone()[0]


def publish_finding_items(database, game_version):
    database.execute_native(
        "INSERT INTO game_findings "
        "SELECT (jsonb_populate_record(NULL::game_findings, "
        "staged.payload_json::jsonb || jsonb_build_object('game_scope_generation',(SELECT generation FROM repertoire_game_scope WHERE id=1)))).* "
        "FROM game_finding_publication_items staged "
        "WHERE staged.game_id=%s AND staged.derivation_version=%s "
        "AND staged.item_kind='finding' "
        "ON CONFLICT(id) DO UPDATE SET "
        "confidence=excluded.confidence,evidence_json=excluded.evidence_json,"
        "repertoire_id=excluded.repertoire_id,game_scope_generation=excluded.game_scope_generation,"
        "card_id=CASE WHEN excluded.kind='repertoire lapse' THEN excluded.card_id "
        "ELSE COALESCE(excluded.card_id,game_findings.card_id) END,"
        "motif=excluded.motif,source_opportunity_id=excluded.source_opportunity_id,"
        "updated_at=excluded.updated_at", game_version,
    )


def supersede_old_opportunities(database, game_id, publication_time):
    database.execute(
        "UPDATE tactical_opportunities SET active=0,superseded_at=?,updated_at=? "
        "WHERE game_id=? AND active=1 AND analysis_version!=("
        "SELECT analysis_version FROM imported_games WHERE id=?)",
        (publication_time, publication_time, game_id, game_id),
    )


def publish_opportunity_items(database, game_version):
    database.execute_native(
        "INSERT INTO tactical_opportunities "
        "SELECT (jsonb_populate_record(NULL::tactical_opportunities, "
        "staged.payload_json::jsonb)).* "
        "FROM game_finding_publication_items staged "
        "WHERE staged.game_id=%s AND staged.derivation_version=%s "
        "AND staged.item_kind='opportunity' "
        "ON CONFLICT(id) DO UPDATE SET outcome=excluded.outcome,"
        "confidence=excluded.confidence,opportunity_value_cp=excluded.opportunity_value_cp,"
        "evaluation_loss_cp=excluded.evaluation_loss_cp,"
        "played_move_uci=excluded.played_move_uci,"
        "accepted_moves_json=excluded.accepted_moves_json,"
        "evidence_json=excluded.evidence_json,engine_version=excluded.engine_version,"
        "network_version=excluded.network_version,active=1,superseded_at=NULL,"
        "updated_at=excluded.updated_at", game_version,
    )


def publish_priority_items(database, game_version):
    database.execute_native(
        "INSERT INTO gameplay_card_priorities "
        "SELECT (jsonb_populate_record(NULL::gameplay_card_priorities, "
        "staged.payload_json::jsonb)).* "
        "FROM game_finding_publication_items staged "
        "WHERE staged.game_id=%s AND staged.derivation_version=%s "
        "AND staged.item_kind='priority' "
        "ON CONFLICT(card_id) DO UPDATE SET "
        "source_game_id=excluded.source_game_id,finding_id=excluded.finding_id,"
        "priority_date=LEAST(gameplay_card_priorities.priority_date,excluded.priority_date),"
        "reason=excluded.reason", game_version,
    )


def advance_findings_phase(database, game_version):
    database.execute(
        "UPDATE game_derivation_jobs SET completed_phases=3,"
        "phase='applying_real_game_misses' "
        "WHERE game_id=? AND derivation_version=?", game_version,
    )


def comparison_generation(database, game_id):
    return database.execute("SELECT repertoire_scope_generation FROM imported_games WHERE id=?", (game_id,)).fetchone()


def prepared_item(database, game_id, version, cursor):
    return database.execute_native(
        "SELECT source_signature,item_count,items_json -> %s AS item "
        "FROM game_finding_preparations "
        "WHERE game_id=%s AND derivation_version=%s",
        (cursor, game_id, version),
    ).fetchone()


def store_preparation(database, game_id, version, signature, item_count, items_json):
    database.execute_native(
        "INSERT INTO game_finding_preparations("
        "game_id,derivation_version,source_signature,item_count,items_json) "
        "VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
        (game_id, version, signature, item_count, items_json),
    )


def restart_findings_generation(database, game_id, version, next_version):
    database.execute(
        "UPDATE game_derivation_jobs SET derivation_version=? "
        "WHERE game_id=? AND derivation_version=?",
        (next_version, game_id, version),
    )


def stage_item(database, game_id, version, kind, item_key, payload_json):
    database.execute(
        """INSERT INTO game_finding_publication_items(
                     game_id,derivation_version,item_kind,item_key,payload_json)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(game_id,derivation_version,item_kind,item_key)
                   DO UPDATE SET payload_json=excluded.payload_json""",
        (game_id, version, kind, item_key, payload_json),
    )
