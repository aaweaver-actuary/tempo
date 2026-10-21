"""Existing shared scheduling SQL fragments, without activity policy changes."""


def control_order(source: str, id_expression: str) -> str:
    """SQL order expression shared by every claim path in one queue."""
    return ("COALESCE((SELECT promoted FROM background_activity a WHERE "
            f"a.source='{source}' AND a.work_id={id_expression}),0) DESC,")


def claimable(source: str, id_expression: str) -> str:
    mode_clause = "" if source == 'durable' else " AND (SELECT study_only_mode FROM settings WHERE id=1)=0"
    return ("(COALESCE((SELECT paused FROM background_activity a WHERE "
            f"a.source='{source}' AND a.work_id={id_expression}),0)=0{mode_clause})")
