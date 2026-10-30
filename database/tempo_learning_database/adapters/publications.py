"""Existing accepted-publication facts, locks and writes on the caller transaction."""


def lock_publication(database, publication_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("learning:publication:" + publication_id,),
    )


def accepted_digest(database, publication_id, generation):
    return database.execute(
        "SELECT digest FROM accepted_publications WHERE publication_id=? AND generation=?",
        (publication_id, generation),
    ).fetchone()


def latest_generation(database, publication_id):
    return database.execute(
        "SELECT MAX(generation) FROM accepted_publications WHERE publication_id=?",
        (publication_id,),
    ).fetchone()[0]


def insert_accepted(database, publication_id, generation, fingerprint, payload_json):
    database.execute(
        "INSERT INTO accepted_publications(publication_id,generation,digest,payload_json) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
        (publication_id, generation, fingerprint, payload_json),
    )


def opening_repertoires(database, publication_id):
    return database.execute(
        "SELECT rc.repertoire_id FROM repertoire_cards rc JOIN cards c ON c.id=rc.card_id WHERE c.publication_id=? AND c.content_type='opening' UNION SELECT repertoire_id FROM cards WHERE publication_id=? AND content_type='opening' AND repertoire_id IS NOT NULL",
        (publication_id, publication_id),
    ).fetchall()


def lock_repertoire(database, repertoire_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("learning:repertoire-owner:" + repertoire_id,),
    )


def opening_graph_steps(database, generation, line_ids):
    return database.execute(
        "SELECT repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,starting_fen,moves_json,trained_color FROM opening_graph_steps WHERE generation=? AND line_id=ANY(?)",
        (generation, line_ids),
    ).fetchall()


def activation_receipt(database, operation_id):
    return database.execute(
        "SELECT request_hash,result_json FROM activation_receipts WHERE operation_id=?",
        (operation_id,),
    ).fetchone()


def serving_for_update(database, publication_id):
    return database.execute(
        "SELECT generation,activation_revision FROM serving_publications WHERE publication_id=? FOR UPDATE",
        (publication_id,),
    ).fetchone()


def accepted_for_update(database, publication_id, generation):
    return database.execute(
        "SELECT payload_json,state FROM accepted_publications WHERE publication_id=? AND generation=? FOR UPDATE",
        (publication_id, generation),
    ).fetchone()


def publication_card_ids(database, publication_id):
    return database.execute(
        "SELECT id FROM cards WHERE publication_id=?", (publication_id,)
    ).fetchall()


def lock_card(database, card_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("tempo:card-edit:" + card_id,),
    )
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))


def deleted_cards(database, card_ids):
    return database.execute(
        "SELECT 1 FROM deleted_cards WHERE card_id=ANY(?) LIMIT 1", (card_ids,)
    ).fetchone()


def deactivate_cards(database, publication_id):
    database.execute(
        "UPDATE cards SET serving_active=0 WHERE publication_id=?", (publication_id,)
    )


def card_identity(database, card_id):
    return database.execute(
        "SELECT publication_id,revision,content_type,study_exercise_id,source_ref FROM cards WHERE id=?",
        (card_id,),
    ).fetchone()


def defense_identity(database, card_id):
    return database.execute(
        "SELECT candidate_id FROM accepted_defense_exercises WHERE card_id=?",
        (card_id,),
    ).fetchone()


def other_opening_owner(database, membership, publication_id):
    return database.execute(
        "SELECT 1 FROM cards c JOIN repertoire_cards rc ON rc.card_id=c.id WHERE rc.repertoire_id=? AND c.content_type='opening' AND c.publication_id!=? AND c.serving_active=1 LIMIT 1",
        (membership, publication_id),
    ).fetchone()


def upsert_repertoire(database, repertoire_id, name, source_name, created_at, allowance):
    database.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,?,?, ?,?) ON CONFLICT(id) DO UPDATE SET new_cards_per_day=excluded.new_cards_per_day",
        (repertoire_id, name, source_name, created_at, allowance),
    )


def insert_study(database, study_id, title):
    database.execute(
        "INSERT INTO studies(id,title) VALUES(?,?) ON CONFLICT DO NOTHING", (study_id, title)
    )


def upsert_study_position(database, position_id, fen):
    database.execute(
        "INSERT INTO study_positions(id,fen) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET fen=excluded.fen",
        (position_id, fen),
    )


def upsert_study_exercise(database, exercise_id, study_id, position_id, revision, sibling_group):
    database.execute(
        "INSERT INTO study_exercises(id,study_id,position_id,current_revision,status,sibling_group) VALUES(?,?,?,?,'published',?) ON CONFLICT(id) DO UPDATE SET current_revision=excluded.current_revision,status='published',sibling_group=excluded.sibling_group",
        (exercise_id, study_id, position_id, revision, sibling_group),
    )


def insert_exercise_revision(database, exercise_id, revision, encoded, fingerprint, created_at):
    database.execute(
        "INSERT INTO study_exercise_revisions VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
        (exercise_id, revision, encoded, fingerprint, created_at),
    )


def upsert_card(database, card_id, repertoire_id, kind, starting_fen, moves_json,
                due_date, content_type, revision, trained_color, exercise_id,
                source_ref, source_fen, publication_id, generation):
    database.execute(
        """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,revision,trained_color,study_exercise_id,source_ref,source_fen,publication_id,publication_generation,serving_active)
           VALUES(?,?,?,?,?,'new',?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
             start_fen=excluded.start_fen,moves_json=excluded.moves_json,revision=excluded.revision,trained_color=excluded.trained_color,
             publication_generation=excluded.publication_generation,serving_active=1""",
        (card_id, repertoire_id, kind, starting_fen, moves_json, due_date, content_type,
         revision, trained_color, exercise_id, source_ref, source_fen, publication_id, generation),
    )


def update_validation(database, pending_validation, card_id):
    database.execute(
        "UPDATE cards SET pending_validation=? WHERE id=?", (pending_validation, card_id)
    )


def remove_card_memberships(database, card_id):
    database.execute("DELETE FROM repertoire_cards WHERE card_id=?", (card_id,))


def remove_card_integrity_blocks(database, card_id):
    database.execute("DELETE FROM repertoire_integrity_card_blocks WHERE card_id=?", (card_id,))


def insert_membership(database, repertoire_id, card_id):
    database.execute("INSERT INTO repertoire_cards VALUES(?,?)", (repertoire_id, card_id))


def insert_integrity_block(database, repertoire_id, card_id):
    database.execute("INSERT INTO repertoire_integrity_card_blocks VALUES(?,?)", (repertoire_id, card_id))


def insert_line(database, line_id, repertoire_id, trained_color, created_at):
    database.execute(
        "INSERT INTO repertoire_lines(id,repertoire_id,trained_color,created_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
        (line_id, repertoire_id, trained_color, created_at),
    )


def upsert_opening_publication(database, repertoire_id, generation):
    database.execute(
        "INSERT INTO opening_graph_publications VALUES(?,?,'ready') ON CONFLICT(repertoire_id) DO UPDATE SET generation=excluded.generation,state='ready'",
        (repertoire_id, generation),
    )


def insert_opening_step(database, repertoire_id, generation, line_id, decision_index,
                        card_id, parent_card_id, starting_fen, moves_json, trained_color):
    database.execute(
        "INSERT INTO opening_graph_steps VALUES(?,?,?,?,?,?,?,?,?)",
        (repertoire_id, generation, line_id, decision_index, card_id, parent_card_id,
         starting_fen, moves_json, trained_color),
    )




def upsert_defense(database, candidate_id, card_id, revision, definition_json):
    database.execute(
        "INSERT INTO accepted_defense_exercises VALUES(?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET exercise_revision=excluded.exercise_revision,definition_json=excluded.definition_json",
        (candidate_id, card_id, revision, definition_json),
    )


def lock_frozen_activation(database):
    database.execute("SELECT id FROM frozen_opening_activation WHERE id=1 FOR UPDATE")


def frozen_run(database, run_id):
    return database.execute("SELECT 1 FROM frozen_opening_runs WHERE run_id=?", (run_id,)).fetchone()


def insert_frozen_run(database, run_id, reader_version):
    database.execute("INSERT INTO frozen_opening_runs VALUES(?,?)", (run_id, reader_version))


def card(database, card_id):
    return database.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()


def card_memberships(database, card_id):
    return database.execute("SELECT repertoire_id FROM repertoire_cards WHERE card_id=?", (card_id,)).fetchall()


def insert_frozen_rank(database, run_id, card_id, ordinal, fingerprint, memberships_json, definition_json):
    database.execute(
        "INSERT INTO frozen_opening_ranks VALUES(?,?,?,?,?,?)",
        (run_id, card_id, ordinal, fingerprint, memberships_json, definition_json),
    )


def activate_frozen_run(database, run_id):
    database.execute("UPDATE frozen_opening_activation SET run_id=?,epoch=epoch+1 WHERE id=1", (run_id,))


def supersede_card_queue(database, card_id):
    database.execute("UPDATE daily_queue SET status='superseded' WHERE card_id=? AND status='queued'", (card_id,))


def activate_accepted(database, publication_id, generation):
    database.execute("UPDATE accepted_publications SET state='active' WHERE publication_id=? AND generation=?", (publication_id, generation))


def upsert_serving(database, publication_id, generation, revision):
    database.execute(
        "INSERT INTO serving_publications VALUES(?,?,?) ON CONFLICT(publication_id) DO UPDATE SET generation=excluded.generation,activation_revision=excluded.activation_revision",
        (publication_id, generation, revision),
    )


def insert_activation_receipt(database, operation_id, fingerprint, result_json):
    database.execute("INSERT INTO activation_receipts VALUES(?,?,?)", (operation_id, fingerprint, result_json))


def ordered_publication_cards(database, publication_id):
    return database.execute("SELECT id FROM cards WHERE publication_id=? ORDER BY id", (publication_id,)).fetchall()


def archive_study_exercises(database, publication_id):
    database.execute("UPDATE study_exercises SET status='archived' WHERE id IN (SELECT study_exercise_id FROM cards WHERE publication_id=?)", (publication_id,))




def supersede_publication_queue(database, publication_id):
    database.execute("UPDATE daily_queue SET status='superseded' WHERE status='queued' AND card_id IN (SELECT id FROM cards WHERE publication_id=?)", (publication_id,))


def retire_accepted(database, publication_id, generation):
    database.execute("UPDATE accepted_publications SET state='retired' WHERE publication_id=? AND generation=?", (publication_id, generation))


def increment_activation_revision(database, publication_id):
    database.execute("UPDATE serving_publications SET activation_revision=activation_revision+1 WHERE publication_id=?", (publication_id,))
