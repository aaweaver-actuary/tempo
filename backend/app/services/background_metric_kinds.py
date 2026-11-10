"""Finite public diagnostic label allowlist."""

KINDS = frozenset(
    (
        "daily_queue",
        "game_sync_record",
        "game_sync_window",
        "opening_graph_rebuild",
        "integrity_scan",
        "other",
    )
)
