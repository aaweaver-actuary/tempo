"""Persisted defensive-only admission policy for durable tasks."""

from fastapi import HTTPException
from tempo_legacy_database.adapters import defensive_analysis as storage

DEFENSIVE_TASK_KINDS = (
    'defensive_threat_scan', 'defensive_threat_validate', 'defensive_threat_backfill',
    'defensive_threat_report_audit', 'defensive_rubric_audit', 'defensive_admission',
)


def analysis_enabled(database) -> bool:
    setting = storage.analysis_setting(database)
    if setting is None:
        raise HTTPException(503, 'Defensive analysis settings are unavailable; restore the database and retry')
    return bool(setting[0])


def task_admission_sql(kind_expression: str) -> str:
    return storage.task_admission_sql(kind_expression, DEFENSIVE_TASK_KINDS)
