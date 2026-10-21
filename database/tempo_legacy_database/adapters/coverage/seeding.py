"""Existing fixed coverage generation admission/staging/activation queries; caller owns policy and transaction."""


def lock_coverage(database, repertoire_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"tempo:coverage:{repertoire_id}",),
    )


def insert_run(database, values):
    database.execute_native(
        "INSERT INTO repertoire_coverage_runs("
        "id,repertoire_id,status,settings_json,total_nodes,created_at,updated_at) "
        "VALUES(%s,%s,'building',%s,0,%s,%s)",
        values,
    )


def merge_position(database, values):
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET ply=%s,routes_json=%s,"
        "covered_replies_json=%s,updated_at=%s WHERE id=%s",
        values,
    )


def fail_run(database, values):
    database.execute_native(
        "UPDATE repertoire_coverage_runs SET status='failed',last_error=%s,updated_at=%s "
        "WHERE id=%s",
        values,
    )


def insert_position(database, values):
    database.execute_native(
        "INSERT INTO repertoire_coverage_nodes("
        "id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,"
        "covered_replies_json,explorer_status,maia_status,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'staging','staging',%s)",
        values,
    )


def source_hash(database, values):
    return database.execute_native(
        "SELECT md5(COALESCE(string_agg("
        "md5(id || start_fen || moves_json || trained_color), '' ORDER BY id), '')) "
        "FROM repertoire_lines WHERE repertoire_id=%s",
        values,
    ).fetchone()


def repertoire_present(database, values):
    return database.execute_native(
        "SELECT 1 FROM repertoires WHERE id=%s", values,
    ).fetchone()


def active_run(database, values):
    return database.execute_native(
        "SELECT * FROM repertoire_coverage_runs WHERE repertoire_id=%s "
        "AND status IN ('building','queued','running') ORDER BY created_at DESC LIMIT 1",
        values,
    ).fetchone()


def locked_position(database, values):
    return database.execute_native(
        "SELECT id,ply,routes_json,covered_replies_json FROM repertoire_coverage_nodes "
        "WHERE run_id=%s AND fen_key=%s FOR UPDATE", values,
    ).fetchone()


def locked_compatible_run(database, values):
    return database.execute("SELECT * FROM repertoire_coverage_runs WHERE id=? FOR UPDATE", values).fetchone()


def fail_compatible_run(database, values):
    database.execute("UPDATE repertoire_coverage_runs SET status='failed',last_error=?,updated_at=? WHERE id=?",
    values)


def activate_run(database, values):
    database.execute_native(
        "UPDATE repertoire_coverage_runs SET total_nodes=%s,status=%s,"
        "last_error=%s,updated_at=%s WHERE id=%s AND status='building'",
        values,
    )


def fail_building_run(database, values):
    database.execute_native(
        "UPDATE repertoire_coverage_runs SET status='failed',last_error=%s,updated_at=%s WHERE id=%s AND status='building'",
        values,
    )


def settings_row(database):
    return database.execute_native("SELECT * FROM settings WHERE id=1").fetchone()


def next_source_line(database, values):
    return database.execute_native(
        "SELECT id,start_fen,moves_json,trained_color "
        "FROM repertoire_lines WHERE repertoire_id=%s AND id>%s "
        "ORDER BY id LIMIT 1", values,
    ).fetchone()


def activate_node(database, values):
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET explorer_status='queued',"
        "maia_status='queued',updated_at=%s WHERE id=%s AND run_id=%s "
        "AND explorer_status='staging'",
        values,
    )


def locked_run(database, values):
    return database.execute_native(
        "SELECT * FROM repertoire_coverage_runs WHERE id=%s FOR UPDATE",
        values,
    ).fetchone()


def next_staged_node(database, values):
    return database.execute_native(
        "SELECT id FROM repertoire_coverage_nodes WHERE run_id=%s "
        "AND id>%s AND explorer_status='staging' ORDER BY id LIMIT 1",
        values,
    ).fetchone()


def node_count(database, values):
    return database.execute_native(
        "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=%s",
        values,
    ).fetchone()[0]
