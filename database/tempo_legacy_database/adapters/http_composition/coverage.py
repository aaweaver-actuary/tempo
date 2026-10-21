"""Existing HTTP coverage lease/availability statements on the caller transaction."""

from collections.abc import Callable
from ..repertoire_scope.freshness import coverage_scope_predicate, latest_coverage_attempt_predicate


def maia_availability(database, current_time: Callable[[], str]):
    return database.execute_native(
        "SELECT EXISTS(SELECT 1 FROM repertoire_coverage_nodes n "
            "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
            "LEFT JOIN background_activity control ON control.source='coverage' "
            "AND control.work_id=n.run_id "
            "WHERE r.status IN ('queued','running','complete','failed') "
            "AND (n.maia_status='queued' OR (n.maia_status='leased' AND n.lease_expires_at<%s)) "
            "AND COALESCE(control.paused,0)=0 "
            f"AND {coverage_scope_predicate(database, native=True)} "
            f"AND {latest_coverage_attempt_predicate(database, native=True)})",
        (current_time(),),
    ).fetchone()


def heartbeat(database, expires_at, updated_at, node_id, lease_id):
    return database.execute(
        """UPDATE repertoire_coverage_nodes SET lease_expires_at=?,updated_at=?
               WHERE id=? AND maia_status='leased' AND lease_id=?""",
        (expires_at, updated_at, node_id, lease_id),
    ).rowcount


def release(database, updated_at, node_id, lease_id):
    return database.execute(
        """UPDATE repertoire_coverage_nodes SET maia_status='queued',lease_id=NULL,
               lease_expires_at=NULL,updated_at=?
               WHERE id=? AND maia_status='leased' AND lease_id=?""",
        (updated_at, node_id, lease_id),
    ).rowcount


def failed_lease(database, node_id, lease_id):
    return database.execute(
        "SELECT run_id,repertoire_id FROM repertoire_coverage_nodes WHERE id=? AND maia_status='leased' AND lease_id=?",
        (node_id, lease_id),
    ).fetchone()


def fail_node(database, message, now, node_id):
    database.execute(
        """UPDATE repertoire_coverage_nodes SET maia_status='failed',lease_id=NULL,
               lease_expires_at=NULL,last_error=?,updated_at=? WHERE id=?""",
        (message, now, node_id),
    )


def fail_run(database, message, now, run_id):
    database.execute(
        "UPDATE repertoire_coverage_runs SET status='failed',last_error=?,updated_at=? WHERE id=?",
        (message, now, run_id),
    )
