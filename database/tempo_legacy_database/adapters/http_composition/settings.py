"""Existing settings statements on the caller-owned connection/transaction."""


def public_settings(database):
    return database.execute(
        "SELECT tactics_new_per_day,defense_new_cards_per_day,include_defensive_cards_in_daily_stack,defensive_analysis_enabled,study_only_mode,discovery_window_days,initial_depth,timezone,new_cards_per_day,study_new_per_day,lichess_username,chesscom_username,auto_sync_minutes,engine_line_window_cp,major_mistake_cp,light_first_interval_days,draw_hold_user_moves,coverage_reply_denominator,coverage_cumulative_target,coverage_horizon_fullmoves,coverage_path_floor,coverage_maia_elo FROM settings WHERE id=1",
    ).fetchone()


def previous_settings(database):
    return database.execute(
        "SELECT discovery_window_days,include_defensive_cards_in_daily_stack,defensive_analysis_enabled,study_only_mode,coverage_reply_denominator,coverage_cumulative_target,coverage_horizon_fullmoves,coverage_path_floor,coverage_maia_elo FROM settings WHERE id=1",
    ).fetchone()


def save_settings(
    database,
    tactics_new_per_day,
    defense_new_cards_per_day,
    include_defensive_cards_in_daily_stack,
    defensive_analysis_enabled,
    study_only_mode,
    discovery_window_days,
    initial_depth,
    timezone,
    new_cards_per_day,
    study_new_per_day,
    lichess_username,
    chesscom_username,
    auto_sync_minutes,
    engine_line_window_cp,
    major_mistake_cp,
    light_first_interval_days,
    draw_hold_user_moves,
    coverage_reply_denominator,
    coverage_cumulative_target,
    coverage_horizon_fullmoves,
    coverage_path_floor,
    coverage_maia_elo,
) -> None:
    database.execute(
        "UPDATE settings SET tactics_new_per_day=?,defense_new_cards_per_day=?,include_defensive_cards_in_daily_stack=?,defensive_analysis_enabled=?,study_only_mode=?,discovery_window_days=?,initial_depth=?,timezone=?,new_cards_per_day=?,study_new_per_day=?,lichess_username=?,chesscom_username=?,auto_sync_minutes=?,engine_line_window_cp=?,major_mistake_cp=?,light_first_interval_days=?,draw_hold_user_moves=?,coverage_reply_denominator=?,coverage_cumulative_target=?,coverage_horizon_fullmoves=?,coverage_path_floor=?,coverage_maia_elo=? WHERE id=1",
        (
            tactics_new_per_day,
            defense_new_cards_per_day,
            include_defensive_cards_in_daily_stack,
            defensive_analysis_enabled,
            study_only_mode,
            discovery_window_days,
            initial_depth,
            timezone,
            new_cards_per_day,
            study_new_per_day,
            lichess_username,
            chesscom_username,
            auto_sync_minutes,
            engine_line_window_cp,
            major_mistake_cp,
            light_first_interval_days,
            draw_hold_user_moves,
            coverage_reply_denominator,
            coverage_cumulative_target,
            coverage_horizon_fullmoves,
            coverage_path_floor,
            coverage_maia_elo,
        ),
    )


def clean_repertoires(database):
    yield from database.execute(
        """SELECT r.id FROM repertoires r
                   JOIN repertoire_integrity_state state ON state.repertoire_id=r.id
                   WHERE r.id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__')
                     AND state.status='clean'""",
    )
