"""Existing system statements on the caller-owned connection/transaction."""


def frozen_opening_identity(database):
    return database.execute_native(
        "SELECT (SELECT system_identifier::text FROM pg_control_system()),(SELECT oid::text FROM pg_database WHERE datname=current_database()),current_database(),current_schema(),(SELECT run_id FROM frozen_opening_activation WHERE id=1)",
    ).fetchone()


def check_settings_read(database):
    return database.execute(
        "SELECT id FROM settings LIMIT 1",
    ).fetchone()


def recent_queue_projections(database):
    yield from database.execute(
        "SELECT * FROM queue_projections ORDER BY queue_date DESC LIMIT 7",
    )


def analysis_threshold(database):
    return database.execute(
        "SELECT major_mistake_cp FROM settings WHERE id=1",
    ).fetchone()
