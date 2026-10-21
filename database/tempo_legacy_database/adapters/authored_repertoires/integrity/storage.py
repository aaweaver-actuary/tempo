"""Existing integrity scan, projection and repair SQL; caller owns policy/transaction."""

from ...controls.claim_sql import claimable, control_order


def repertoire_scan_targets(system_repertoires):
    """Prepare the two fixed exclusions at the original pre-connection callpoint."""
    system_placeholders = ",".join("?" for _ in system_repertoires)

    def stale_repertoire_targets(database, values):
        return database.execute(
            f"""SELECT r.id FROM repertoires r
                       LEFT JOIN repertoire_integrity_state s ON s.repertoire_id=r.id
                       WHERE r.id NOT IN ({system_placeholders})
                         AND (s.repertoire_id IS NULL OR s.status='unchecked' OR s.scan_status='failed')
                       ORDER BY r.id""",
            values,
        )

    def all_repertoire_targets(database, values):
        return database.execute(
            f"SELECT id FROM repertoires WHERE id NOT IN ({system_placeholders}) ORDER BY id",
            values,
        )

    return stale_repertoire_targets, all_repertoire_targets


def source_lines(database, values):
    return database.execute(
        "SELECT id,repertoire_id,name,trained_color,start_fen,moves_json,created_at FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
        values,
    ).fetchall()


def source_cards(database, values):
    return database.execute(
        """SELECT DISTINCT c.id,c.repertoire_id,c.kind,c.start_fen,c.moves_json,
                              COALESCE(c.trained_color,(
                                  SELECT l.trained_color FROM repertoire_lines l
                                  WHERE l.repertoire_id=? ORDER BY l.created_at,l.id LIMIT 1
                              )) trained_color
               FROM cards c LEFT JOIN repertoire_cards rc ON rc.card_id=c.id
               WHERE (rc.repertoire_id=? OR c.repertoire_id=?) AND c.archived=0 AND c.content_type='opening'
               ORDER BY c.id""",
        values,
    ).fetchall()


def clear_card_blocks(database, values):
    return database.execute(
        "DELETE FROM repertoire_integrity_card_blocks WHERE repertoire_id=?",
        values,
    )


def card_exists(database, values):
    return database.execute(
        "SELECT 1 FROM cards WHERE id=?",
        values,
    ).fetchone()


def insert_card_blocks(database, values):
    return database.executemany(
        """INSERT INTO repertoire_integrity_card_blocks(
               repertoire_id,card_id,issue_id,scan_generation,published_at
           ) VALUES(?,?,?,?,?)""",
        values,
    )


def update_card_validation(database, values):
    return database.execute(
        """UPDATE cards SET pending_validation=CASE WHEN EXISTS(
               SELECT 1 FROM repertoire_integrity_card_blocks published_block
               WHERE published_block.card_id=cards.id
           ) THEN 1 ELSE 0 END
           WHERE archived=0 AND content_type='opening' AND
             (repertoire_id=? OR id IN (
                 SELECT card_id FROM repertoire_cards WHERE repertoire_id=?
             ))""",
        values,
    )


def repertoire_exists(database, values):
    return database.execute(
        "SELECT 1 FROM repertoires WHERE id=?",
        values,
    ).fetchone()


def clear_issues(database, values):
    return database.execute(
        "DELETE FROM repertoire_integrity_issues WHERE repertoire_id=?",
        values,
    )


def insert_sweep_issue(database, values):
    return database.execute(
        """INSERT INTO repertoire_integrity_issues(
                       id,repertoire_id,kind,fen_key,fen,trained_color,signature,
                       moves_json,sources_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        values,
    )


def publish_sweep_state(database, values):
    return database.execute(
        """INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at)
           VALUES(?,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET status=excluded.status,checked_at=excluded.checked_at""",
        values,
    )


def all_repertoire_ids(database):
    return database.execute(
        "SELECT id FROM repertoires",
    ).fetchall()


def active_scan(database, values):
    return database.execute(
        "SELECT run_id,status FROM repertoire_integrity_jobs WHERE repertoire_id=? AND status IN ('queued','running','finalizing')",
        values,
    ).fetchone()


def source_count(database, values):
    return database.execute(
        "SELECT COUNT(*) FROM (SELECT id FROM repertoire_lines WHERE repertoire_id=? UNION ALL SELECT DISTINCT c.id FROM cards c LEFT JOIN repertoire_cards rc ON rc.card_id=c.id WHERE (rc.repertoire_id=? OR c.repertoire_id=?) AND c.archived=0 AND c.content_type='opening')",
        values,
    ).fetchone()[0]


def queue_scan_job(database, values):
    return database.execute(
        "INSERT INTO repertoire_integrity_jobs(repertoire_id,run_id,status,source_offset,total_sources,updated_at) VALUES(?,?, 'queued',0,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET run_id=excluded.run_id,status='queued',source_offset=0,total_sources=excluded.total_sources,attempts=0,last_error=NULL,updated_at=excluded.updated_at",
        values,
    )


def queue_scan_state(database, values):
    return database.execute(
        "INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation,scan_completed_sources,scan_total_sources,scan_error) VALUES(?, 'unchecked','queued',NULL,0,?,NULL) ON CONFLICT(repertoire_id) DO UPDATE SET scan_status='queued',scan_generation=excluded.scan_generation,scan_completed_sources=0,scan_total_sources=excluded.scan_total_sources,scan_error=NULL",
        values,
    )


def begin_write(database):
    return database.execute(
        "BEGIN IMMEDIATE",
    )


def next_scan_job(database):
    return database.execute(
        f"SELECT * FROM repertoire_integrity_jobs WHERE status IN ('queued','running','finalizing') AND {claimable('integrity', 'repertoire_integrity_jobs.repertoire_id')} ORDER BY {control_order('integrity', 'repertoire_integrity_jobs.repertoire_id')}updated_at,repertoire_id LIMIT 1",
    ).fetchone()


def mark_scan_finalizing(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_jobs SET status='finalizing',updated_at=? WHERE repertoire_id=?",
        values,
    )


def mark_state_running(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_state SET scan_status='running' WHERE repertoire_id=?",
        values,
    )


def mark_scan_running(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_jobs SET status='running',attempts=attempts+1,updated_at=? WHERE repertoire_id=?",
        values,
    )


def staged_observations(database, values):
    return database.execute(
        "SELECT observations_json,invalid_json FROM repertoire_integrity_source_runs WHERE run_id=? ORDER BY source_offset",
        values,
    ).fetchall()


def current_run(database, values):
    return database.execute(
        "SELECT run_id FROM repertoire_integrity_jobs WHERE repertoire_id=?",
        values,
    ).fetchone()


def insert_staged_issue(database, values):
    return database.execute(
        "INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        values,
    )


def publish_scan_state(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_state SET status=?,checked_at=?,scan_status='idle',scan_generation=?,scan_completed_sources=scan_total_sources,scan_error=NULL WHERE repertoire_id=?",
        values,
    )


def complete_scan_job(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_jobs SET status='complete',last_error=NULL,updated_at=? WHERE repertoire_id=?",
        values,
    )


def current_run_sources(database, values):
    return database.execute(
        """SELECT run_id,total_sources FROM repertoire_integrity_jobs
                   WHERE repertoire_id=?""",
        values,
    ).fetchone()


def restart_changed_sources(database, values):
    return database.execute(
        """UPDATE repertoire_integrity_jobs
                       SET source_offset=0,total_sources=?,status='running',updated_at=?
                       WHERE repertoire_id=? AND run_id=?""",
        values,
    )


def clear_staged_sources(database, values):
    return database.execute(
        "DELETE FROM repertoire_integrity_source_runs WHERE run_id=?",
        values,
    )


def finish_source_scanning(database, values):
    return database.execute(
        """UPDATE repertoire_integrity_jobs
                       SET status='finalizing',updated_at=?
                       WHERE repertoire_id=? AND run_id=?""",
        values,
    )


def stage_source_observations(database, values):
    return database.execute(
        "INSERT OR REPLACE INTO repertoire_integrity_source_runs(run_id,source_offset,observations_json,invalid_json) VALUES(?,?,?,?)",
        values,
    )


def advance_scan_job(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_jobs SET status=?,source_offset=?,attempts=0,last_error=NULL,updated_at=? WHERE repertoire_id=? AND run_id=?",
        values,
    )


def advance_scan_state(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_state SET scan_status='running',scan_completed_sources=?,scan_total_sources=?,scan_error=NULL WHERE repertoire_id=?",
        values,
    )


def scan_attempts(database, values):
    return database.execute(
        "SELECT attempts FROM repertoire_integrity_jobs WHERE repertoire_id=? AND run_id=?",
        values,
    ).fetchone()


def fail_scan_job(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_jobs SET status='failed',last_error=?,updated_at=? WHERE repertoire_id=? AND run_id=?",
        values,
    )


def fail_scan_state(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_state SET scan_status='failed',scan_error=? WHERE repertoire_id=?",
        values,
    )


def requeue_scan_job(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_jobs SET status='queued',last_error=?,updated_at=? WHERE repertoire_id=? AND run_id=?",
        values,
    )


def requeue_scan_state(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_state SET scan_status='retrying',scan_error=? WHERE repertoire_id=?",
        values,
    )


def summary_state(database, values):
    return database.execute(
        """SELECT r.id,COALESCE(s.status,'unchecked') integrity_status,s.checked_at integrity_checked_at,
                  COALESCE(s.scan_status,'idle') scan_status,s.scan_generation,
                  COALESCE(s.scan_completed_sources,0) scan_completed_sources,
                  COALESCE(s.scan_total_sources,0) scan_total_sources,s.scan_error
           FROM repertoires r LEFT JOIN repertoire_integrity_state s ON s.repertoire_id=r.id
           WHERE r.id=?""",
        values,
    ).fetchone()


def first_issue(database, values):
    return database.execute(
        "SELECT id FROM repertoire_integrity_issues WHERE repertoire_id=? ORDER BY updated_at,id LIMIT 1",
        values,
    ).fetchone()


def issue_count(database, values):
    return database.execute(
        "SELECT COUNT(*) FROM repertoire_integrity_issues WHERE repertoire_id=?",
        values,
    ).fetchone()[0]


def issue_details(database, values):
    return database.execute(
        """SELECT id,kind,fen_key,fen,trained_color,signature,moves_json,sources_json
           FROM repertoire_integrity_issues WHERE repertoire_id=? ORDER BY updated_at,id""",
        values,
    ).fetchall()


def card_review_count(database, values):
    return database.execute(
        "SELECT COUNT(*) FROM reviews WHERE card_id=?",
        values,
    ).fetchone()[0]


def insert_replacement_line(database, values):
    return database.execute(
        """INSERT OR IGNORE INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        values,
    )


def delete_source_line(database, values):
    return database.execute(
        "DELETE FROM repertoire_lines WHERE id=?",
        values,
    )


def insert_replacement_card(database, columns, values):
    return database.execute(
        f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        tuple(values.get(column) for column in columns),
    )


def mark_card_authored(database, values):
    return database.execute(
        "UPDATE cards SET canonical_route_source=1 WHERE id=?",
        values,
    )


def upsert_authored_membership(database, values):
    return database.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1) ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1",
        values,
    )


def delete_membership(database, values):
    return database.execute(
        "DELETE FROM repertoire_cards WHERE repertoire_id=? AND card_id=?",
        values,
    )


def card_has_membership(database, values):
    return database.execute(
        "SELECT 1 FROM repertoire_cards WHERE card_id=? LIMIT 1",
        values,
    ).fetchone()


def archive_superseded_card(database, values):
    return database.execute(
        "UPDATE cards SET archived=1,superseded_by=? WHERE id=?",
        values,
    )


def line_routes(database, values):
    return database.execute(
        "SELECT start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=?",
        values,
    ).fetchall()


def derived_cards(database, values):
    return database.execute(
        """SELECT c.* FROM cards c LEFT JOIN repertoire_cards rc
             ON rc.card_id=c.id AND rc.repertoire_id=?
           WHERE c.content_type='opening' AND c.archived=0
             AND (c.repertoire_id=? OR rc.repertoire_id IS NOT NULL)""",
        values,
    ).fetchall()


def active_card_authority(database, values):
    return database.execute(
        "SELECT repertoire_id,canonical_route_source FROM cards WHERE id=? AND archived=0",
        values,
    ).fetchone()


def membership_authority(database, values):
    return database.execute(
        "SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?",
        values,
    ).fetchone()


def remaining_membership(database, values):
    return database.execute(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? AND repertoire_id<>? ORDER BY repertoire_id LIMIT 1",
        values,
    ).fetchone()


def reassign_card_owner(database, values):
    return database.execute(
        "UPDATE cards SET repertoire_id=? WHERE id=?",
        values,
    )


def archive_unsupported_card(database, values):
    return database.execute(
        "UPDATE cards SET archived=1,state='locked',superseded_by=NULL WHERE id=?",
        values,
    )


def issue(database, values):
    return database.execute(
        "SELECT * FROM repertoire_integrity_issues WHERE id=? AND repertoire_id=?",
        values,
    ).fetchone()


def source_line(database, values):
    return database.execute(
        "SELECT * FROM repertoire_lines WHERE id=?",
        values,
    ).fetchone()


def source_card(database, values):
    return database.execute(
        "SELECT * FROM cards WHERE id=?",
        values,
    ).fetchone()


def prepared_lines(database, values):
    return database.execute(
        "SELECT * FROM repertoire_lines WHERE repertoire_id=?",
        values,
    ).fetchall()


def prepared_cards(database, values):
    return database.execute(
        """SELECT DISTINCT c.* FROM cards c
                   LEFT JOIN repertoire_cards rc ON rc.card_id=c.id
                   WHERE c.content_type='opening' AND c.archived=0
                     AND (c.repertoire_id=? OR rc.repertoire_id=?)""",
        values,
    ).fetchall()


def issue_signature(database, values):
    return database.execute(
        "SELECT signature FROM repertoire_integrity_issues WHERE id=? AND repertoire_id=?",
        values,
    ).fetchone()
