"""Existing compatibility coverage SQL; caller owns decisions and transactions."""

from ..controls.claim_sql import claimable, control_order
from ..repertoire_scope.freshness import (
    coverage_scope_predicate, latest_coverage_attempt_predicate,
)


def recent_cohort_games(database, values):
    return database.execute(
        """SELECT player_rating,speed FROM imported_games
            WHERE played_at>=? AND adaptive_excluded=0
              AND speed IN ('blitz','rapid','classical')""",
        values,
    ).fetchall()


def repertoire_exists(database, values):
    return database.execute(
        "SELECT 1 FROM repertoires WHERE id=?",
        values,
    ).fetchone()


def active_run(database, values):
    return database.execute(
        """SELECT * FROM repertoire_coverage_runs
               WHERE repertoire_id=? AND status IN ('queued','running')
               ORDER BY created_at DESC LIMIT 1""",
        values,
    ).fetchone()


def coverage_settings(database):
    return database.execute(
        "SELECT * FROM settings WHERE id=1",
    ).fetchone()


def repertoire_lines(database, values):
    return database.execute(
        "SELECT * FROM repertoire_lines WHERE repertoire_id=?",
        values,
    )


def begin_write(database):
    return database.execute(
        "BEGIN IMMEDIATE",
    )


def insert_run(database, values):
    return database.execute(
        """INSERT INTO repertoire_coverage_runs(
                   id,repertoire_id,status,settings_json,total_nodes,created_at,updated_at,last_error
               ) VALUES(?,?,?, ?,?,?,?,?)""",
        values,
    )


def insert_node(database, values):
    return database.execute(
        """INSERT INTO repertoire_coverage_nodes(
                       id,run_id,repertoire_id,fen,fen_key,ply,trained_color,
                       routes_json,covered_replies_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        values,
    )


def fail_unattended_runs(database, reason, now):
    return database.execute(
        f"""UPDATE repertoire_coverage_runs AS r SET status='failed',last_error=?,updated_at=?
                       WHERE {coverage_scope_predicate(database, repertoire_id="r.repertoire_id")} AND id IN (SELECT run_id FROM repertoire_coverage_nodes WHERE explorer_status='queued')
                         AND COALESCE(last_error,'')!=?""",
        (reason, now(), reason),
    )


def next_explorer_node(database):
    return database.execute(
        f"""SELECT n.*,r.settings_json FROM repertoire_coverage_nodes n
               JOIN repertoire_coverage_runs r ON r.id=n.run_id
               WHERE n.explorer_status='queued' AND r.status IN ('queued','running','failed') AND {coverage_scope_predicate(database)} AND {latest_coverage_attempt_predicate(database)} AND {claimable('coverage', 'n.run_id')}
               ORDER BY {control_order('coverage', 'n.run_id')}r.created_at,n.ply,n.id LIMIT 1""",
    ).fetchone()


def mark_explorer_running(database, values):
    return database.execute(
        "UPDATE repertoire_coverage_nodes SET explorer_status='running',updated_at=? WHERE id=? AND explorer_status='queued'",
        values,
    ).rowcount


def mark_run_running(database, values):
    return database.execute(
        "UPDATE repertoire_coverage_runs SET status='running',updated_at=? WHERE id=?",
        values,
    )


def explorer_cache_entry(database, values):
    return database.execute(
        "SELECT response_json,fetched_at FROM explorer_position_cache WHERE cache_key=?",
        values,
    ).fetchone()


def node_candidates(database, values):
    return database.execute(
        "SELECT * FROM repertoire_coverage_candidates WHERE node_id=?",
        values,
    ).fetchall()


def node_explorer_games(database, values):
    return database.execute(
        "SELECT explorer_games FROM repertoire_coverage_nodes WHERE id=?",
        values,
    ).fetchone()[0]


def update_candidate_blend(database, values):
    return database.execute(
        """UPDATE repertoire_coverage_candidates
               SET blended_probability=?,required=?,source_state=?
               WHERE node_id=? AND move_uci=?""",
        values,
    )


def upsert_explorer_cache(database, values):
    return database.execute(
        """INSERT INTO explorer_position_cache(
                       cache_key,fen_key,speeds,ratings,response_json,fetched_at
                   ) VALUES(?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                       response_json=excluded.response_json,fetched_at=excluded.fetched_at""",
        values,
    )


def upsert_explorer_candidate(database, values):
    return database.execute(
        """INSERT INTO repertoire_coverage_candidates(
                           node_id,move_uci,explorer_probability,covered,source_state
                       ) VALUES(?,?,?,?,?) ON CONFLICT(node_id,move_uci) DO UPDATE SET
                           explorer_probability=excluded.explorer_probability,
                           covered=excluded.covered,source_state=excluded.source_state""",
        values,
    )


def complete_explorer_node(database, values):
    return database.execute(
        """UPDATE repertoire_coverage_nodes
                   SET explorer_status='complete',explorer_games=?,last_error=NULL,updated_at=?
                   WHERE id=?""",
        values,
    )


def completed_explorer_count(database, values):
    return database.execute(
        "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=? AND explorer_status='complete'",
        values,
    ).fetchone()[0]


def pending_explorer_count(database, values):
    return database.execute(
        "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=? AND explorer_status IN ('queued','running')",
        values,
    ).fetchone()[0]


def update_run_progress(database, values):
    return database.execute(
        """UPDATE repertoire_coverage_runs SET completed_nodes=?,status=?,updated_at=?
                   WHERE id=?""",
        values,
    )


def explorer_progress_counts(database, values):
    return database.execute(
        """SELECT COUNT(*) AS total,
                   SUM(CASE WHEN explorer_status='complete' THEN 1 ELSE 0 END) AS explorer_done,
                   SUM(CASE WHEN maia_status='complete' THEN 1 ELSE 0 END) AS maia_done
                   FROM repertoire_coverage_nodes WHERE run_id=?""",
        values,
    ).fetchone()


def requeue_explorer_node(database, values):
    return database.execute(
        "UPDATE repertoire_coverage_nodes SET explorer_status='queued',last_error=?,updated_at=? WHERE id=?",
        values,
    )


def requeue_run(database, values):
    return database.execute(
        "UPDATE repertoire_coverage_runs SET status='queued',last_error=?,updated_at=? WHERE id=?",
        values,
    )


def fail_explorer_node(database, values):
    return database.execute(
        "UPDATE repertoire_coverage_nodes SET explorer_status='failed',last_error=?,updated_at=? WHERE id=?",
        values,
    )


def fail_run(database, values):
    return database.execute(
        "UPDATE repertoire_coverage_runs SET status='failed',last_error=?,updated_at=? WHERE id=?",
        values,
    )


def latest_run(database, values):
    return database.execute(
        "SELECT * FROM repertoire_coverage_runs WHERE repertoire_id=? ORDER BY created_at DESC LIMIT 1",
        values,
    ).fetchone()


def required_coverage_totals(database, values):
    return database.execute(
        """SELECT COUNT(*) required_branches,
                      COALESCE(SUM(covered),0) covered_branches,
                      COALESCE(SUM(blended_probability),0) required_probability,
                      COALESCE(SUM(CASE WHEN covered=1 THEN blended_probability ELSE 0 END),0) covered_probability
               FROM repertoire_coverage_candidates c
               JOIN repertoire_coverage_nodes n ON n.id=c.node_id
               WHERE n.run_id=? AND c.required=1""",
        values,
    ).fetchone()


def incomplete_explorer_count(database, values):
    return database.execute(
        "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=? AND explorer_status!='complete'",
        values,
    ).fetchone()[0]


def latest_run_settings(database, values):
    return database.execute(
        "SELECT id,settings_json FROM repertoire_coverage_runs WHERE repertoire_id=? ORDER BY created_at DESC LIMIT 1",
        values,
    ).fetchone()


def uncovered_required_candidates(database, values):
    return database.execute(
        """SELECT c.*,n.fen,n.fen_key,n.explorer_games,n.trained_color
                   FROM repertoire_coverage_candidates c
                   JOIN repertoire_coverage_nodes n ON n.id=c.node_id
                   WHERE n.run_id=? AND c.required=1 AND c.covered=0
                   ORDER BY c.blended_probability DESC,c.move_uci""",
        values,
    )


def requeue_expired_maia_leases(database, values):
    return database.execute(
        """UPDATE repertoire_coverage_nodes SET maia_status='queued',lease_id=NULL,lease_expires_at=NULL
               WHERE maia_status='leased' AND lease_expires_at<?""",
        values,
    )


def next_maia_node(database):
    return database.execute(
        f"""SELECT n.*,r.settings_json FROM repertoire_coverage_nodes n
               JOIN repertoire_coverage_runs r ON r.id=n.run_id
               WHERE n.maia_status='queued'
               AND r.status IN ('queued','running','complete','failed') AND {coverage_scope_predicate(database)}
               AND {latest_coverage_attempt_predicate(database)}
               AND {claimable('coverage', 'n.run_id')}
               ORDER BY {control_order('coverage', 'n.run_id')}r.created_at,n.ply,n.id LIMIT 1""",
    ).fetchone()


def lease_maia_node(database, values):
    return database.execute(
        "UPDATE repertoire_coverage_nodes SET maia_status='leased',lease_id=?,lease_expires_at=?,updated_at=? WHERE id=?",
        values,
    )


def maia_node(database, values):
    return database.execute(
        """SELECT n.*,r.settings_json FROM repertoire_coverage_nodes n
               JOIN repertoire_coverage_runs r ON r.id=n.run_id WHERE n.id=?""",
        values,
    ).fetchone()


def upsert_maia_candidate(database, values):
    return database.execute(
        """INSERT INTO repertoire_coverage_candidates(
                       node_id,move_uci,maia_probability,covered,source_state
                   ) VALUES(?,?,?,?,?) ON CONFLICT(node_id,move_uci) DO UPDATE SET
                       maia_probability=excluded.maia_probability,
                       covered=excluded.covered""",
        values,
    )


def complete_maia_node(database, values):
    return database.execute(
        """UPDATE repertoire_coverage_nodes SET maia_status='complete',lease_id=NULL,
                      lease_expires_at=NULL,updated_at=? WHERE id=?""",
        values,
    )


def maia_progress_counts(database, values):
    return database.execute(
        """SELECT COUNT(*) AS total,
               SUM(CASE WHEN explorer_status='complete' THEN 1 ELSE 0 END) AS explorer_done,
               SUM(CASE WHEN maia_status='complete' THEN 1 ELSE 0 END) AS maia_done
               FROM repertoire_coverage_nodes WHERE run_id=?""",
        values,
    ).fetchone()
