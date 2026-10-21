"""Fixed Maia/evidence operations and atomic guards on the owner's transaction."""
from functools import partial

from psycopg.types.json import Jsonb
from tempo_analysis_jobs_contracts.v1 import MaiaResult, serialize_contract

from tempo_intelligence.errors import DomainError


class MaiaOperations:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def require_profile(self,descriptor,configured):
        row=self._database.execute("SELECT * FROM model_profiles WHERE model_id=%s AND model_version=%s",(descriptor["model_id"],descriptor["model_version"])).fetchone()
        if not configured or not row or row["artifact_sha256"] != descriptor["artifact_sha256"] or descriptor["rating"] not in row["ratings"]:
            raise DomainError("unsupported_model_context",422)

    def previous_job(self,cache):
        return self._database.execute("SELECT payload FROM maia_jobs WHERE cache_id=%s AND request_revision=%s ORDER BY execution_generation DESC LIMIT 1",(cache["cache_id"],cache["generation"])).fetchone()

    def create_job(self,job,execution,cache,payload):
        self._database.execute("INSERT INTO maia_jobs(job_id,request_revision,execution_generation,cache_id,payload) VALUES(%s,%s,%s,%s,%s)",(job.job_id,job.request_revision,execution,cache["cache_id"],Jsonb(payload)))
        self._database.execute("UPDATE caches SET execution_generation=%s,state='awaiting_result',error_code=NULL,next_attempt_at=NULL,updated_at=now() WHERE cache_id=%s",(execution,cache["cache_id"]))

    def request(self,request_id):
        return self._owner._head(self._database,request_id)

    def pending_jobs(self,cache_id):
        return self._database.execute("SELECT j.payload FROM maia_jobs j JOIN caches c ON j.cache_id=c.cache_id AND j.request_revision=c.generation AND j.execution_generation=c.execution_generation WHERE j.cache_id=%s AND j.status IN ('pending','running')",(cache_id,)).fetchall()

    def evidence(self,evidence_id):
        row=self._database.execute("SELECT * FROM evidence WHERE evidence_id=%s",(evidence_id,)).fetchone()
        if not row:
            raise DomainError("evidence_not_found",404)
        return row

    def job(self,execution):
        job=self._database.execute("SELECT * FROM maia_jobs WHERE job_id=%s AND request_revision=%s AND execution_generation=%s FOR UPDATE",(execution.job_id,execution.request_revision,execution.execution_generation)).fetchone()
        if not job:
            raise DomainError("unknown_maia_execution")
        return job

    def verify_result_replay(self,job,result):
        if serialize_contract(MaiaResult.model_validate(job["result_payload"])) != serialize_contract(result):
            raise DomainError("maia_result_replay_conflict")

    def result_cache(self,job,result):
        cache=self._database.execute("SELECT * FROM caches WHERE cache_id=%s FOR UPDATE",(job["cache_id"],)).fetchone()
        if cache["generation"] != result.request_revision or cache["execution_generation"] != result.execution_generation or job["status"] in ("failed","cancelled") or cache["state"] != "awaiting_result":
            raise DomainError("stale_maia_execution")
        return cache

    @property
    def append(self):
        # Resolve the existing writer before the application builds its payload.
        return partial(self._owner._append,self._database)

    def complete_job(self,wire,evidence_id,result):
        self._database.execute("UPDATE maia_jobs SET status='completed',result_payload=%s,evidence_id=%s WHERE job_id=%s AND request_revision=%s AND execution_generation=%s",(Jsonb(wire),evidence_id,result.job_id,result.request_revision,result.execution_generation))

    def status_cache(self,job,status):
        cache=self._database.execute("SELECT * FROM caches WHERE cache_id=%s FOR UPDATE",(job["cache_id"],)).fetchone()
        if cache["generation"] != status.request_revision or cache["execution_generation"] != status.execution_generation or job["evidence_id"]:
            raise DomainError("stale_maia_execution")
        return cache

    def update_status(self,job,status,wire):
        if job["status"] in ("failed","cancelled") or job["status"]=="running" and status.status=="pending":
            raise DomainError("maia_status_transition_conflict")
        self._database.execute("UPDATE maia_jobs SET status=%s,status_payload=%s WHERE job_id=%s AND request_revision=%s AND execution_generation=%s",(status.status,Jsonb(wire),status.job_id,status.request_revision,status.execution_generation))

    def fail_cache(self,status,cache):
        self._database.execute("UPDATE caches SET state='failed',error_code=%s,retryable=%s WHERE cache_id=%s",("maia_"+status.status,status.retryable,cache["cache_id"]))
