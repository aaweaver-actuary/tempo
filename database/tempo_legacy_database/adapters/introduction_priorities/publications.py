"""Existing priority rows and publication writes inside caller-owned transactions."""


def delete_legacy_rows(database, repertoire_id):
    return database.execute(
        "DELETE FROM repertoire_card_introduction_priorities WHERE repertoire_id=?",
        (repertoire_id,),
    )


def insert_legacy_rows(database, priority_rows):
    return database.executemany(
        """INSERT INTO repertoire_card_introduction_priorities(
               repertoire_id,card_id,scoring_version,completed_line_ids_json,
               completion_mass,frontier_decisions_json,frontier_reach,
               priority_score,evidence_json,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        priority_rows,
    )


def publish_legacy_generation(database, repertoire_id, generation, updated_at):
    return database.execute(
                    'INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) '
                    'VALUES(?,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET '
                    'generation=excluded.generation,updated_at=excluded.updated_at',
                    (repertoire_id, generation, updated_at),
                )


def delete_staged_generation(database, repertoire_id, generation):
    return database.execute(
            "DELETE FROM repertoire_card_priority_generations WHERE repertoire_id=? AND generation=?",
            (repertoire_id, generation),
        )


def insert_generation_rows(database, priority_rows):
    return database.executemany(
                """INSERT INTO repertoire_card_priority_generations(
                       repertoire_id,generation,card_id,scoring_version,
                       completed_line_ids_json,completion_mass,frontier_decisions_json,
                       frontier_reach,priority_score,evidence_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                priority_rows,
            )


def publish_generation(database, repertoire_id, generation, updated_at):
    return database.execute(
            """INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at)
               VALUES(?,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET
               generation=excluded.generation,updated_at=excluded.updated_at""",
            (repertoire_id, generation, updated_at),
        )


def published_evidence(database, repertoire_id):
    return database.execute(
        """SELECT evidence_json,updated_at FROM current_repertoire_card_priority_generations generated
           WHERE generated.repertoire_id=? AND generated.generation=(
               SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=?
           ) ORDER BY updated_at DESC LIMIT 1""",
        (repertoire_id, repertoire_id),
    ).fetchone()


def legacy_evidence(database, repertoire_id):
    return database.execute(
            """SELECT evidence_json,updated_at FROM current_repertoire_card_introduction_priorities
               WHERE repertoire_id=? ORDER BY updated_at DESC LIMIT 1""",
            (repertoire_id,),
        ).fetchone()
