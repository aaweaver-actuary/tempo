"""Fixed engine-work queries, lease/replay guards and writes on one owner transaction."""
from datetime import datetime, timezone
from functools import partial

from psycopg.types.json import Jsonb
from tempo_intelligence.errors import DomainError


class AnalysisEngineOperations:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    @property
    def head(self):
        return partial(self._owner._head,self._database)

    def reap_cancelled(self,run_id,run):
        self._database.execute("UPDATE analysis_positions SET state='cancelled',lease_token=NULL,leased_until=NULL WHERE run_id=%s AND revision=%s AND state='running' AND leased_until<=now()",(run_id,run["revision"]))
        return self._database.execute("SELECT 1 FROM analysis_positions WHERE run_id=%s AND revision=%s AND state='running' LIMIT 1",(run_id,run["revision"])).fetchone()

    def finish_cancelled_claim(self,run_id,run):
        self._database.execute("UPDATE analysis_runs SET state='cancelled',requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))

    def eligible_position(self,run_id,run):
        return self._database.execute("SELECT * FROM analysis_positions WHERE run_id=%s AND revision=%s AND ((state IN ('pending','retry_wait') AND (next_attempt_at IS NULL OR next_attempt_at<=now())) OR (state='running' AND leased_until<=now())) ORDER BY created_at,ordinal,position_id FOR UPDATE SKIP LOCKED LIMIT 1",(run_id,run["revision"])).fetchone()

    def reserve_capacity(self):
        # One live engine lease across this workspace, even with multiple dispatchers/runs.
        if self._database.execute("SELECT 1 FROM analysis_positions WHERE state='running' AND leased_until>now() LIMIT 1").fetchone():
            return None
        self._database.execute("SELECT pg_advisory_xact_lock(hashtextextended('analysis-engine-capacity',0))")
        if self._database.execute("SELECT 1 FROM analysis_positions WHERE state='running' AND leased_until>now() LIMIT 1").fetchone():
            return None
        return True

    def claim_root(self,row):
        return self._database.execute("SELECT payload FROM analysis_results WHERE result_id=%s",(row["accepted_root"],)).fetchone()["payload"]

    def accepted_root(self,row):
        return self._database.execute("SELECT payload FROM analysis_results WHERE result_id=%s",(row["accepted_root"],)).fetchone()["payload"]

    def incompatible_profile(self,run_id,run,row):
        self._database.execute("UPDATE analysis_positions SET state='failed',error_code='incompatible_engine_profile',lease_token=NULL,leased_until=NULL WHERE run_id=%s AND revision=%s AND position_id=%s",(run_id,run["revision"],row["position_id"]))
        self._database.execute("UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))

    def lease(self,job,generation,run_id,run,row,token):
        self._database.execute("INSERT INTO analysis_executions VALUES(%s,%s,%s,%s,%s,%s,%s)",(job["job_id"],generation,run_id,run["revision"],row["position_id"],row["stage"],Jsonb(job)))
        self._database.execute("UPDATE analysis_positions SET state='running',execution_generation=%s,lease_token=%s,leased_until=now()+interval '60 seconds',error_code=NULL WHERE run_id=%s AND revision=%s AND position_id=%s",(generation,token,run_id,run["revision"],row["position_id"]))

    def existing_result(self,wire):
        return self._database.execute("SELECT * FROM analysis_results WHERE job_id=%s AND execution_generation=%s",(wire["job_id"],wire["execution_generation"])).fetchone()

    def verify_replay(self,old,wire):
        if old["payload"]!=wire:
            raise DomainError("analysis_result_replay_conflict")

    def locked_position(self,claim):
        return self._database.execute("SELECT * FROM analysis_positions WHERE run_id=%s AND revision=%s AND position_id=%s FOR UPDATE",(claim["run_id"],claim["revision"],claim["position_id"])).fetchone()

    def failure_position(self,claim):
        return self._database.execute("SELECT * FROM analysis_positions WHERE run_id=%s AND revision=%s AND position_id=%s FOR UPDATE",(claim["run_id"],claim["revision"],claim["position_id"])).fetchone()

    def verify_acceptance(self,run,claim,wire,row):
        if run["revision"]!=claim["revision"] or run["state"] in {"cancel_requested","cancelled"} or not row or row["state"]!="running" or row["execution_generation"]!=wire["execution_generation"] or row["lease_token"]!=claim["lease_token"] or row["leased_until"]<=datetime.now(timezone.utc):
            raise DomainError("stale_analysis_execution")

    def verify_execution(self,wire,claim):
        execution=self._database.execute("SELECT payload FROM analysis_executions WHERE job_id=%s AND execution_generation=%s",(wire["job_id"],wire["execution_generation"])).fetchone()
        if not execution or execution["payload"]!=claim["job"]:
            raise DomainError("analysis_execution_mismatch")

    def save_result(self,result_id,wire):
        self._database.execute("INSERT INTO analysis_results VALUES(%s,%s,%s,%s)",(result_id,wire["job_id"],wire["execution_generation"],Jsonb(wire)))

    def accept_root(self,result_id,transition,claim):
        self._database.execute("UPDATE analysis_positions SET accepted_root=%s,state=%s,stage=%s,attempts=0,lease_token=NULL,leased_until=NULL,error_code=%s WHERE run_id=%s AND revision=%s AND position_id=%s",(result_id,*transition(),claim["run_id"],claim["revision"],claim["position_id"]))

    def root_job(self,row):
        return self._database.execute("SELECT e.payload FROM analysis_results r JOIN analysis_executions e USING(job_id,execution_generation) WHERE r.result_id=%s",(row["accepted_root"],)).fetchone()["payload"]

    def accept_played(self,result_id,quality,claim):
        self._database.execute("UPDATE analysis_positions SET accepted_played=%s,state='complete',lease_token=NULL,leased_until=NULL,error_code=%s WHERE run_id=%s AND revision=%s AND position_id=%s",(result_id,quality(),claim["run_id"],claim["revision"],claim["position_id"]))

    def finish_acceptance(self,claim):
        self._database.execute("UPDATE analysis_positions SET error_code=NULL WHERE run_id=%s AND revision=%s AND state='pending' AND error_code='engine_unavailable'",(claim["run_id"],claim["revision"]))
        self._database.execute("UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(claim["run_id"],claim["revision"]))

    def active_failure_claim(self,row,claim):
        if not row or row["lease_token"]!=claim["lease_token"] or row["execution_generation"]!=claim["execution_generation"] or row["state"]!="running":
            return False
        return True

    def save_event(self,fingerprint,claim,event):
        self._database.execute("INSERT INTO analysis_execution_events VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(fingerprint(),claim["job"]["job_id"],claim["execution_generation"],Jsonb(event)))

    def fail_position(self,state,attempts,code,delay,claim):
        self._database.execute("UPDATE analysis_positions SET state=%s,attempts=%s,error_code=%s,next_attempt_at=now()+(%s * interval '1 second'),lease_token=NULL,leased_until=NULL WHERE run_id=%s AND revision=%s AND position_id=%s",(state,attempts,code,delay(),claim["run_id"],claim["revision"],claim["position_id"]))
        self._database.execute("UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(claim["run_id"],claim["revision"]))

    def running_after_failure(self,claim):
        return self._database.execute("SELECT 1 FROM analysis_positions WHERE run_id=%s AND revision=%s AND state='running' LIMIT 1",(claim["run_id"],claim["revision"])).fetchone()

    def finish_cancelled_failure(self,claim):
        self._database.execute("UPDATE analysis_runs SET state='cancelled' WHERE run_id=%s AND revision=%s",(claim["run_id"],claim["revision"]))

    def mark_unavailable(self,run_id,run):
        return self._database.execute("UPDATE analysis_positions SET error_code='engine_unavailable' WHERE run_id=%s AND revision=%s AND state='pending' AND error_code IS NULL RETURNING position_id",(run_id,run["revision"])).fetchall()

    def request_unavailable_generation(self,run_id,run):
        self._database.execute("UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))
