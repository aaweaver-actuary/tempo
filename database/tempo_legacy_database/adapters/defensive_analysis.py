"""Existing defensive-analysis settings/read predicates; no application imports."""


def analysis_setting(database):
    return database.execute('SELECT defensive_analysis_enabled FROM settings WHERE id=1').fetchone()


def task_admission_sql(kind_expression: str, task_kinds) -> str:
    kinds = ','.join("'" + kind + "'" for kind in task_kinds)
    # Study maintenance and explicit bounded real-game encounter processing use
    # the approved targeted introduction exception. Other optional work retains
    # Study-only policy; durable per-work pause still applies to every claim.
    # Durable per-work pause is still checked separately by the claim owner.
    return (f"(({kind_expression} IN ('daily_queue','study_game_feedback','game_feedback_history') OR (SELECT study_only_mode FROM settings WHERE id=1)=0) AND "
            f"({kind_expression} NOT IN ({kinds}) OR "
            "(SELECT defensive_analysis_enabled FROM settings WHERE id=1)=1))")
