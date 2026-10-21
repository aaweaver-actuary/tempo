"""Fixed defensive-analysis claim/callback operations; caller owns scheduling policy."""
from ..defensive_analysis import recommendation_request_ids_sql
from ..scheduling.engine import ELIGIBLE_THREAT_REQUEST_SQL as _ELIGIBLE_THREAT_REQUEST


_REQUEST_PRIORITIES = (
        ("AND request.id IN (SELECT request_id FROM threat_candidate_requests "
         "WHERE role='attempt') ",
         "COALESCE(control.promoted,0) DESC,request.created_at,request.id"),
        ("AND COALESCE(control.promoted,0)>0 ",
         "control.promoted DESC,request.created_at,request.id"),
        ("AND COALESCE(control.promoted,0)=0 ", "request.created_at,request.id"),
        ("AND control.promoted<0 ",
         "control.promoted DESC,request.created_at,request.id"),
    )


def analysis_setting(database):
    return database.execute_native("SELECT defensive_analysis_enabled FROM settings WHERE id=1").fetchone()


def request_reader(database, enabled):
    # Capture the existing recommendation predicate at its original call point.
    recommendation_filter = ("" if enabled else
        "AND request.id IN (" + recommendation_request_ids_sql() + ") ")

    def read_priority(priority_index):
        priority_filter, ordering = _REQUEST_PRIORITIES[priority_index]
        return database.execute_native(
            _ELIGIBLE_THREAT_REQUEST + recommendation_filter + priority_filter + "ORDER BY " + ordering +
            " LIMIT 1 FOR UPDATE OF request SKIP LOCKED",
        ).fetchone()

    return read_priority


def reclaim_expired(database, now, as_of):
    return database.execute_native(
        "UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s "
        "WHERE id=(SELECT id FROM threat_analysis_requests "
        "WHERE state='leased' AND lease_expires_at<=%s "
        "ORDER BY lease_expires_at,id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id",
        (now, as_of),
    ).fetchone()


def promoted_work(database):
    return database.execute_native(
        "SELECT 1 FROM background_activity WHERE source='threat_analysis' "
        "AND promoted>0 AND paused=0 LIMIT 1",
    ).fetchone()


def accept_claim(database, lease_id, expires_at, now, request_id):
    return database.execute_native(
        "UPDATE threat_analysis_requests SET state='leased',lease_id=%s,"
        "lease_expires_at=%s,attempts=attempts+1,updated_at=%s WHERE id=%s AND state='queued'",
        (lease_id, expires_at, now, request_id),
    ).rowcount


def locked_request(database, request_id):
    return database.execute_native(
        "SELECT state,lease_id,request_json FROM threat_analysis_requests "
        "WHERE id=%s FOR UPDATE",
        (request_id,),
    ).fetchone()


def complete_request(database, report_json, now, request_id):
    database.execute_native(
        "UPDATE threat_analysis_requests SET state='complete',report_json=%s,"
        "lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=%s WHERE id=%s",
        (report_json, now, request_id),
    )


def candidate_rows(database, request_id):
    rows = database.execute_native(
        "SELECT DISTINCT candidate_id FROM threat_candidate_requests WHERE request_id=%s",
        (request_id,),
    )
    return (row for row in rows)


def fail(database, message, now, request_id, lease_id):
    return database.execute_native(
        "UPDATE threat_analysis_requests SET "
        "state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,"
        "last_error=%s,lease_id=NULL,lease_expires_at=NULL,updated_at=%s "
        "WHERE id=%s AND state='leased' AND lease_id=%s RETURNING state",
        (message, now, request_id, lease_id),
    ).fetchone()


def release(database, now, request_id, lease_id):
    return database.execute_native(
        "UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,"
        "attempts=GREATEST(0,attempts-1),"
        "lease_expires_at=NULL,updated_at=%s "
        "WHERE id=%s AND state='leased' AND lease_id=%s RETURNING id",
        (now, request_id, lease_id),
    ).fetchone()


def retry(database, now, request_id):
    return database.execute_native(
        "UPDATE threat_analysis_requests SET state='queued',attempts=0,last_error=NULL,"
        "updated_at=%s WHERE id=%s AND state='failed' RETURNING id",
        (now, request_id),
    ).fetchone()
