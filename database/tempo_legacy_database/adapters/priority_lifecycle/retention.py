"""Existing bounded priority-retention reads and writes on borrowed transactions."""
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.introduction_priorities.jobs import current_job
from .preparation import locked_current_job


def native_configured() -> bool:
    return postgres_store.configured()


def delete_prepared_rows(database, row_values):
    return database.executemany(
                """DELETE FROM repertoire_priority_prepared_rows
                   WHERE repertoire_id=? AND generation=? AND ordinal=?""",
                row_values,
            )


def delete_stale_generations(database, row_values):
    return database.executemany(
                """DELETE FROM repertoire_card_priority_generations
                   WHERE repertoire_id=? AND generation=? AND card_id=?""",
                row_values,
            )


def delete_sqlite_rows(database, row_values):
    return database.executemany(
                    "DELETE FROM repertoire_card_priority_generations WHERE rowid=?",
                    row_values,
                )


def current_lease(database, task_id, task_generation, lease_token):
    return database.execute(
                """SELECT 1 FROM background_tasks
                   WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
                (task_id, task_generation, lease_token),
            ).fetchone()


def publication(database, repertoire_id):
    return database.execute(
            "SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=?",
            (repertoire_id,),
        ).fetchone()


def stale_preparation(database, repertoire_id, keep_generation):
    return database.execute(
                "SELECT generation FROM repertoire_priority_preparations "
                "WHERE repertoire_id=? AND generation<>? ORDER BY generation NULLS LAST LIMIT 1",
                (repertoire_id, keep_generation),
            ).fetchone()


def prepared_keys(database, repertoire_id, generation, row_limit):
    return [
                (row["generation"], row["ordinal"])
                for row in database.execute(
                    """SELECT generation,ordinal FROM repertoire_priority_prepared_rows
                       WHERE repertoire_id=? AND generation=?
                       ORDER BY ordinal NULLS LAST LIMIT ? FOR UPDATE SKIP LOCKED""",
                    (repertoire_id, generation, row_limit),
                )
            ]


def delete_empty_preparation(database, repertoire_id, generation):
    return database.execute(
                    """DELETE FROM repertoire_priority_preparations
                       WHERE repertoire_id=? AND generation=? AND NOT EXISTS(
                         SELECT 1 FROM repertoire_priority_prepared_rows
                         WHERE repertoire_id=? AND generation=? LIMIT 1)""",
                    (repertoire_id, generation,
                     repertoire_id, generation),
                )


def stale_generation_keys(database, repertoire_id, published_generation, active_generation, row_limit):
    return [
                (row["generation"], row["card_id"])
                for row in database.execute(
                    """SELECT generation,card_id FROM repertoire_card_priority_generations
                       WHERE repertoire_id=? AND generation<>? AND generation<>?
                       ORDER BY generation,card_id LIMIT ? FOR UPDATE SKIP LOCKED""",
                    (repertoire_id, published_generation, active_generation, row_limit),
                )
            ]


def stale_sqlite_row_ids(database, repertoire_id, published_generation, active_generation, row_limit):
    return [
                row["rowid"]
                for row in database.execute(
                    """SELECT rowid FROM repertoire_card_priority_generations
                       WHERE repertoire_id=? AND generation<>? AND generation<>?
                       ORDER BY generation,card_id LIMIT ?""",
                    (repertoire_id, published_generation, active_generation, row_limit),
                )
            ]


def has_stale_generations(database, repertoire_id, published_generation, active_generation):
    return database.execute(
            """SELECT 1 FROM repertoire_card_priority_generations
               WHERE repertoire_id=? AND generation<>? AND generation<>? LIMIT 1""",
            (repertoire_id, published_generation, active_generation),
        ).fetchone() is not None


def has_stale_preparations(database, repertoire_id, keep_generation):
    return database.execute(
                """SELECT 1 FROM repertoire_priority_preparations
                   WHERE repertoire_id=? AND generation<>? LIMIT 1""",
                (repertoire_id, keep_generation),
            ).fetchone() is not None
