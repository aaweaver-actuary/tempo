"""Finite public diagnostic label allowlist."""
KINDS = frozenset((
    "daily_queue", "defensive_rubric_audit", "defensive_admission", "repertoire_game_refresh",
    "defensive_threat_report_audit", "defensive_threat_scan", "defensive_threat_backfill",
    "defensive_threat_validate", "priority_retention", "next_opponent_profile", "game_sync_record",
    "game_derivation_positions", "game_derivation_compare", "game_derivation_findings",
    "game_derivation_misses", "game_derivation_events", "game_derivation_features",
    "game_derivation_priorities", "repertoire_priority", "daily_statistics", "game_sync_window",
    "opening_graph_rebuild", "prefix_transition_application", "integrity_scan", "opening_segmentation", "repertoire_opportunity",
    "discovery_recommendation", "discovery_admission", "canonical_prefix_preview", "coverage_seed", "coverage_explorer",
    "game_analysis_publish", "game_analysis_followup", "engine_game", "engine_defense", "other",
))
