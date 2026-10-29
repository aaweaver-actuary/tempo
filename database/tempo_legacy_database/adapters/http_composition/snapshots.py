"""Fixed portable legacy snapshot source rows; exclusion/checksum policy stays outside."""


def automatic_coverage_runs(database):
    yield from database.execute(
        """SELECT id FROM repertoire_coverage_runs
                   WHERE settings_json LIKE ?""",
        ('%"automatic_priority": true%',),
    )


def coverage_nodes(database):
    return database.execute(
        "SELECT id,run_id FROM repertoire_coverage_nodes",
    ).fetchall()


def portable_table_rows(database):
    table_names = [
        "settings",
        "repertoires",
        "canonical_prefix_previews",
        "canonical_prefix_results",
        "canonical_prefix_positions",
        "repertoire_lines",
        "repertoire_cards",
        "cards",
        "reviews",
        "daily_queue",
        "queue_attempt_origins",
        "review_attempt_receipts",
        "review_schedule_snapshots",
        "daily_queue_days",
        "position_annotations",
        "teaching_states",
        "card_revisions",
        "prefix_splits",
        "tactic_progress",
        "tactic_captures",
        "game_accounts",
        "imported_games",
        "game_move_analysis",
        "game_move_analysis_candidates",
        "game_analysis_jobs",
        "game_sync_jobs",
        "game_derivation_jobs",
        "repertoire_game_scope",
        "game_position_occurrences",
        "gameplay_card_priorities",
        "repertoire_comparisons",
        "game_repertoire_matches",
        "repertoire_decision_events",
        "game_findings",
        "threat_training_candidates",
        "threat_analysis_requests",
        "threat_candidate_requests",
        "defense_attempts",
        "guided_review_sessions",
        "guided_review_attempts",
        "gameplay_events",
        "game_feature_rows",
        "daily_chess_snapshots",
        "daily_chess_insights",
        "game_insight_recommendations",
        "repertoire_coverage_runs",
        "repertoire_coverage_nodes",
        "repertoire_coverage_candidates",
        "game_sync_state",
    ]
    for name in table_names:
        yield name, database.execute(f"SELECT * FROM {name}").fetchall()
