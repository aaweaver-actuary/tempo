"""The canonical feature-row upsert and existing derivation phase write."""

GAME_FEATURE_UPSERT_SQL = """INSERT INTO game_feature_rows(
       game_id,feature_version,local_day,local_hour,local_weekday,outcome_score,
       player_decisions,mean_loss_cp,major_mistakes,opening_exit_ply,
       opening_exit_eval_cp,endgame_entry_ply,endgame_entry_eval_cp,
       tactical_opportunities,tactical_found,tactical_conceded,
       primary_repertoire_id,updated_at
   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
   ON CONFLICT(game_id) DO UPDATE SET
       feature_version=excluded.feature_version,local_day=excluded.local_day,
       local_hour=excluded.local_hour,local_weekday=excluded.local_weekday,
       outcome_score=excluded.outcome_score,player_decisions=excluded.player_decisions,
       mean_loss_cp=excluded.mean_loss_cp,major_mistakes=excluded.major_mistakes,
       opening_exit_ply=excluded.opening_exit_ply,
       opening_exit_eval_cp=excluded.opening_exit_eval_cp,
       endgame_entry_ply=excluded.endgame_entry_ply,
       endgame_entry_eval_cp=excluded.endgame_entry_eval_cp,
       tactical_opportunities=excluded.tactical_opportunities,
       tactical_found=excluded.tactical_found,
       tactical_conceded=excluded.tactical_conceded,
       primary_repertoire_id=excluded.primary_repertoire_id,
       updated_at=excluded.updated_at"""


def write_feature_values(database, values):
    database.execute(GAME_FEATURE_UPSERT_SQL, values)


def advance_feature_phase(database, game_id, version):
    database.execute(
        "UPDATE game_derivation_jobs SET completed_phases=6,"
        "phase='enqueueing_priorities' "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    )
