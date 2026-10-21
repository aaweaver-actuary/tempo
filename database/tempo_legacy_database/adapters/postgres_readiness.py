"""Existing PostgreSQL readiness facts; application owns health decisions."""


def read_health_facts(reader, today):
    with reader() as database:
                version_row = database.execute_native(
                    "SELECT MAX(version) FROM tempo_schema_migrations"
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
                    "WHERE kind='daily_queue' AND deduplication_key='current'"
                ).fetchone()
    return version_row, settings_row, queue_row, queue_task
