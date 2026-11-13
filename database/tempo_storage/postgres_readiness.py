"""Existing PostgreSQL readiness facts; application owns health decisions."""


def read_health_facts(reader, today):
    with reader() as database:
                version_row = database.execute_native(
                    "SELECT MAX(version) FROM tempo_schema_migrations"
                ).fetchone()
                position_tables = database.execute_native(
                    "SELECT to_regclass('public.study_position_epochs') IS NOT NULL AND "
                    "to_regclass('public.study_position_owners') IS NOT NULL AND "
                    "to_regclass('public.study_position_states') IS NOT NULL AND "
                    "to_regclass('public.study_position_fsrs_id_seq') IS NOT NULL AND "
                    "to_regclass('public.study_position_seed_runs') IS NOT NULL AND "
                    "to_regclass('public.study_position_seed_outcomes') IS NOT NULL AND "
                    "to_regclass('public.study_position_issued_plans') IS NOT NULL AND "
                    "to_regclass('public.study_position_first_responses') IS NOT NULL AND "
                    "to_regclass('public.study_position_grades') IS NOT NULL"
                ).fetchone()
                settings_row = database.execute_native(
                    "SELECT 1 FROM settings WHERE id=1"
                ).fetchone()
                queue_row = database.execute_native(
                    "SELECT state,refresh_pending,last_error FROM queue_projections "
                    "WHERE queue_date=%s", (today().isoformat(),),
                ).fetchone()
                queue_task = database.execute_native(
                    "SELECT state,payload_json FROM background_tasks "
                    "WHERE kind='daily_queue' AND (deduplication_key='current' OR deduplication_key=%s) "
                    "AND payload_json::jsonb->>'queue_date'=%s LIMIT 1",
                    ("current-day:" + today().isoformat(), today().isoformat()),
                ).fetchone()
    return version_row, position_tables, settings_row, queue_row, queue_task
