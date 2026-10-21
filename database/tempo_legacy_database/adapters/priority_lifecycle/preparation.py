"""Existing priority preparation, epoch locks and staged-publication database operations."""
from tempo_legacy_database.adapters.introduction_priorities.publications import publish_generation


def task_lease(database, task_id):
    return database.execute(
        "SELECT generation,lease_token,state FROM background_tasks WHERE id=?",
        (task_id,),
    ).fetchone()


def locked_current_job(database, repertoire_id):
    return database.execute(
        "SELECT generation,status FROM repertoire_priority_jobs "
        "WHERE repertoire_id=? FOR UPDATE", (repertoire_id,),
    ).fetchone()


def preparation_manifest(database, repertoire_id, generation):
    return database.execute(
        "SELECT * FROM repertoire_priority_preparations "
        "WHERE repertoire_id=? AND generation=?", (repertoire_id, generation),
    ).fetchone()


def insert_preparation(database, repertoire_id, generation, source_version, scoring_version, calculated_at):
    return database.execute(
                """INSERT INTO repertoire_priority_preparations(
                   repertoire_id,generation,source_version,scoring_version,
                   calculated_at,ordering_version,expected_count,status)
                   VALUES(?,?,?,?,?,1,NULL,'preparing')""",
                (repertoire_id, generation, source_version,
                 scoring_version, calculated_at),
            )


def upsert_prepared_rows(database, row_values):
    return database.executemany(
                """INSERT INTO repertoire_priority_prepared_rows(
                   repertoire_id,generation,ordinal,card_id,completed_line_ids_json,
                   completion_mass,frontier_decisions_json,frontier_reach,
                   priority_score,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(repertoire_id,generation,ordinal) DO UPDATE SET
                     card_id=excluded.card_id,
                     completed_line_ids_json=excluded.completed_line_ids_json,
                     completion_mass=excluded.completion_mass,
                     frontier_decisions_json=excluded.frontier_decisions_json,
                     frontier_reach=excluded.frontier_reach,
                     priority_score=excluded.priority_score,
                     evidence_json=excluded.evidence_json""",
                row_values,
            )


def upsert_staged_rows(database, row_values):
    return database.executemany(
                """INSERT INTO repertoire_card_priority_generations(
                   repertoire_id,generation,card_id,scoring_version,
                   completed_line_ids_json,completion_mass,frontier_decisions_json,
                   frontier_reach,priority_score,evidence_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(repertoire_id,generation,card_id) DO UPDATE SET
                     completed_line_ids_json=excluded.completed_line_ids_json,
                     completion_mass=excluded.completion_mass,
                     frontier_decisions_json=excluded.frontier_decisions_json,
                     frontier_reach=excluded.frontier_reach,
                     priority_score=excluded.priority_score,
                     evidence_json=excluded.evidence_json,
                     updated_at=excluded.updated_at""",
                row_values,
            )


def prepared_count(database, repertoire_id, generation):
    return database.execute(
            "SELECT COUNT(*) FROM repertoire_priority_prepared_rows "
            "WHERE repertoire_id=? AND generation=?", (repertoire_id, generation),
        ).fetchone()[0]


def mark_preparation_ready(database, expected_count, repertoire_id, generation):
    return database.execute(
            "UPDATE repertoire_priority_preparations SET expected_count=?,status='ready' "
            "WHERE repertoire_id=? AND generation=? AND status='preparing'",
            (expected_count, repertoire_id, generation),
        )


def mark_running(database, updated_at, repertoire_id, generation):
    return database.execute(
            "UPDATE repertoire_priority_jobs SET status='running',updated_at=? "
            "WHERE repertoire_id=? AND generation=?",
            (updated_at, repertoire_id, generation),
        )


def prepared_page(database, repertoire_id, generation, cursor, row_limit):
    return database.execute(
            "SELECT * FROM repertoire_priority_prepared_rows "
            "WHERE repertoire_id=? AND generation=? AND ordinal>=? "
            "ORDER BY ordinal LIMIT ?",
            (repertoire_id, generation, cursor, row_limit),
        )


def staged_count(database, repertoire_id, generation):
    return database.execute(
            "SELECT COUNT(*) FROM repertoire_card_priority_generations "
            "WHERE repertoire_id=? AND generation=?", (repertoire_id, generation),
        ).fetchone()[0]


def complete_job(database, updated_at, repertoire_id, generation):
    return database.execute(
            "UPDATE repertoire_priority_jobs SET status='complete',last_error=NULL,updated_at=? "
            "WHERE repertoire_id=? AND generation=?", (updated_at, repertoire_id, generation),
        )


def source_version(database, repertoire_id, *, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    shared_version = int(database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1" + suffix,
    ).fetchone()[0])
    database.execute(
        """INSERT INTO priority_repertoire_source_epochs(repertoire_id,version)
           SELECT id,0 FROM repertoires WHERE id=?
           ON CONFLICT(repertoire_id) DO NOTHING""",
        (repertoire_id,),
    )
    repertoire_row = database.execute(
        "SELECT version FROM priority_repertoire_source_epochs "
        "WHERE repertoire_id=?" + suffix, (repertoire_id,),
    ).fetchone()
    if repertoire_row is None:
        return None
    repertoire_version = int(repertoire_row[0])
    return f"{shared_version}:{repertoire_version}"
