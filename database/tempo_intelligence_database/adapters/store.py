"""Intelligence owns every transaction here; no provider executes inside this boundary."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import uuid
import time

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from tempo_analysis_jobs_contracts.v1 import MaiaJob, MaiaResult, JobStatus, serialize_contract

from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import canonical_json, digest
from tempo_intelligence.maia import build_maia_job, validate_maia_result
from tempo_intelligence_database.ddl.migrate import expected_migrations, ANALYSIS_IMMUTABLE_TABLES, ANALYSIS_MUTABLE_COLUMNS
from tempo_intelligence.runtime import BackgroundConnection, BACKGROUND_OPERATION_SECONDS, background_deadline
from tempo_intelligence.serialization import public
from tempo_intelligence.source_requests import (admit_request, cache_identity, failure_transition,
    request_descriptors, request_snapshot, retry_eligible, source_inspection, validate_request_revision)


def utcnow():
    return datetime.now(timezone.utc)




class Store:
    def __init__(self, configuration):
        self.configuration = configuration
        self.workspace_id = configuration.workspace_id
        self.foreground = ConnectionPool(configuration.database_url, min_size=0, max_size=configuration.pool_size,
            timeout=2, open=True, kwargs={"row_factory":dict_row,"connect_timeout":2,"application_name":"tempo-intelligence"})
        self.background = ConnectionPool(configuration.database_url, min_size=0, max_size=configuration.background_pool_size,
            timeout=1, open=True, connection_class=BackgroundConnection,
            kwargs={"row_factory":dict_row,"connect_timeout":2,"application_name":"tempo-intelligence-worker"})

    @property
    def read_only(self):
        return self.configuration.read_only

    @contextmanager
    def transaction(self, *, background=False, read_only=False):
        pool = self.background if background else self.foreground
        token = None
        if background and background_deadline.get() is None:
            token = background_deadline.set(time.monotonic() + BACKGROUND_OPERATION_SECONDS)
        try:
            with pool.connection() as database:
                if read_only or self.configuration.read_only:
                    database.execute("SET TRANSACTION READ ONLY")
                database.execute("SET LOCAL search_path=intelligence,pg_catalog")
                database.execute("SELECT set_config('statement_timeout',%s,true)",("1500ms",))
                database.execute("SELECT set_config('lock_timeout',%s,true)",("250ms",))
                database.execute("SELECT set_config('transaction_timeout',%s,true)",("3000ms",))
                yield database
        finally:
            if token is not None:
                background_deadline.reset(token)

    def validate_startup(self):
        try:
            with self.transaction(read_only=True) as database:
                migrations={row["id"]:row["checksum"] for row in database.execute("SELECT * FROM migration_history")}
                if migrations != expected_migrations():
                    raise DomainError("migration_state_mismatch",503)
                binding=database.execute("SELECT workspace_id FROM instance WHERE id=1").fetchone()
                if not binding or binding["workspace_id"] != self.workspace_id:
                    raise DomainError("workspace_mismatch",503)
                role=database.execute("SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls OR rolinherit AS elevated FROM pg_roles WHERE rolname=current_user").fetchone()
                privileges=database.execute("SELECT has_schema_privilege(current_user,'intelligence','CREATE') OR has_schema_privilege(current_user,'public','CREATE') OR has_database_privilege(current_user,current_database(),'CREATE,TEMP') OR has_table_privilege(current_user,'intelligence.migration_history','INSERT,UPDATE,DELETE') OR has_table_privilege(current_user,'intelligence.instance','INSERT,UPDATE,DELETE') OR has_table_privilege(current_user,'intelligence.evidence','UPDATE,DELETE') AS elevated").fetchone()
                if self.configuration.read_only:
                    required=database.execute("SELECT bool_and(has_table_privilege(current_user,'intelligence.'||name,'SELECT')) AS available FROM unnest(%s::text[]) AS table_names(name)",(["analysis_heads","analysis_runs","analysis_inputs","analysis_pages"],)).fetchone()
                    if not required["available"]:
                        raise DomainError("games_reader_grants_missing",503)
                else:
                    immutable_payload_tables=[*ANALYSIS_IMMUTABLE_TABLES,"analysis_inputs","analysis_runs","analysis_positions"]
                    integrity=database.execute("SELECT bool_or(has_column_privilege(current_user,'intelligence.'||name,'payload','UPDATE') OR has_table_privilege(current_user,'intelligence.'||name,'DELETE')) AS elevated FROM unnest(%s::text[]) AS table_names(name)",(immutable_payload_tables,)).fetchone()
                    tables=[*ANALYSIS_IMMUTABLE_TABLES,*ANALYSIS_MUTABLE_COLUMNS]
                    required=database.execute("SELECT bool_and(has_table_privilege(current_user,'intelligence.'||name,'SELECT') AND has_table_privilege(current_user,'intelligence.'||name,'INSERT')) AS available FROM unnest(%s::text[]) AS table_names(name)",(tables,)).fetchone()
                    column_tables=[table for table,columns in ANALYSIS_MUTABLE_COLUMNS.items() for column in columns]
                    columns=[column for table,columns in ANALYSIS_MUTABLE_COLUMNS.items() for column in columns]
                    updates=database.execute("SELECT bool_and(has_column_privilege(current_user,'intelligence.'||table_name,column_name,'UPDATE')) AS available FROM unnest(%s::text[],%s::text[]) AS permissions(table_name,column_name)",(column_tables,columns)).fetchone()
                    if integrity["elevated"]:
                        raise DomainError("restricted_runtime_required",503)
                    if not required["available"] or not updates["available"]:
                        raise DomainError("analysis_runtime_grants_missing",503)
                membership=database.execute("SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user) LIMIT 1").fetchone()
                foreign=database.execute("SELECT 1 FROM pg_database WHERE datallowconn AND datname<>current_database() AND has_database_privilege(current_user,oid,'CONNECT') LIMIT 1").fetchone()
                if role["elevated"] or privileges["elevated"] or membership or foreign:
                    raise DomainError("restricted_runtime_required",503)
            with self.transaction() as database:
                for profile in self.configuration.model_profiles:
                    database.execute("INSERT INTO model_profiles VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (profile["model_id"],profile["model_version"],profile["artifact_sha256"],Jsonb(profile["ratings"])))
                    existing=database.execute("SELECT * FROM model_profiles WHERE model_id=%s AND model_version=%s",(profile["model_id"],profile["model_version"])).fetchone()
                    if existing["artifact_sha256"] != profile["artifact_sha256"] or existing["ratings"] != profile["ratings"]:
                        raise DomainError("model_version_rebound",503)
        except DomainError:
            raise
        except Exception:
            raise DomainError("database_unavailable",503) from None

    def close(self):
        self.foreground.close()
        self.background.close()

    def health(self):
        with self.transaction(read_only=True) as database:
            row=database.execute("SELECT workspace_id FROM instance WHERE id=1").fetchone()
            if not row or row["workspace_id"] != self.workspace_id:
                raise DomainError("workspace_mismatch",503)

    def _head(self,database,request_id):
        row=database.execute("SELECT r.* FROM requests r JOIN request_heads h ON (r.request_id,r.revision)=(h.request_id,h.revision) WHERE h.request_id=%s",(request_id,)).fetchone()
        if not row:
            raise DomainError("request_not_found",404)
        return row

    def _profile(self,database,descriptor):
        configured=any(profile["model_id"]==descriptor["model_id"] and profile["model_version"]==descriptor["model_version"] and profile["artifact_sha256"]==descriptor["artifact_sha256"] and descriptor["rating"] in profile["ratings"] for profile in self.configuration.model_profiles)
        row=database.execute("SELECT * FROM model_profiles WHERE model_id=%s AND model_version=%s",(descriptor["model_id"],descriptor["model_version"])).fetchone()
        if not configured or not row or row["artifact_sha256"] != descriptor["artifact_sha256"] or descriptor["rating"] not in row["ratings"]:
            raise DomainError("unsupported_model_context",422)

    def submit(self,request):
        payload=admit_request(request,self.workspace_id)
        with self.transaction() as database:
            database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("request:"+request.request_id,))
            old=database.execute("SELECT payload FROM requests WHERE request_id=%s AND revision=%s",(request.request_id,request.request_revision)).fetchone()
            if old:
                if old["payload"] != payload:
                    raise DomainError("request_replay_conflict")
            else:
                head=database.execute("SELECT revision FROM request_heads WHERE request_id=%s",(request.request_id,)).fetchone()
                validate_request_revision(request,head)
                snapshot,immutable_snapshot,snapshot_identity=request_snapshot(request)
                database.execute("INSERT INTO snapshots VALUES(%s,%s) ON CONFLICT DO NOTHING",(snapshot_identity,Jsonb(immutable_snapshot)))
                existing_snapshot=database.execute("SELECT payload FROM snapshots WHERE identity=%s",(snapshot_identity,)).fetchone()
                if existing_snapshot["payload"] != immutable_snapshot:
                    raise DomainError("snapshot_replay_conflict")
                descriptors=request_descriptors(request,self.configuration)
                bindings=[]
                descriptors.sort(key=lambda descriptor:cache_identity(self.workspace_id,descriptor))
                for descriptor in descriptors:
                    cache_id=cache_identity(self.workspace_id,descriptor)
                    terminal=descriptor["terminal_reason"] is not None
                    if descriptor["kind"] == "maia" and not terminal:
                        self._profile(database,descriptor)
                    database.execute("INSERT INTO caches(cache_id,descriptor,input_snapshot,kind,state) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(cache_id,Jsonb(descriptor),Jsonb(snapshot),descriptor["kind"],"terminal" if terminal else "queued"))
                    bindings.append({"kind":descriptor["kind"],"cache_id":cache_id})
                    cache=database.execute("SELECT * FROM caches WHERE cache_id=%s FOR UPDATE",(cache_id,)).fetchone()
                    if not terminal and cache["state"] == "complete":
                        accepted=database.execute("SELECT accepted_at FROM evidence WHERE evidence_id=%s",(cache["accepted_id"],)).fetchone()
                        if accepted and accepted["accepted_at"] + timedelta(seconds=request.max_age_seconds) <= utcnow():
                            self._new_generation(database,cache)
                    cache=database.execute("SELECT * FROM caches WHERE cache_id=%s",(cache_id,)).fetchone()
                    if cache["kind"] == "maia" and cache["state"] == "queued":
                        self._maia_job(database,cache)
                database.execute("INSERT INTO requests(request_id,revision,payload,bindings) VALUES(%s,%s,%s,%s)",(request.request_id,request.request_revision,Jsonb(payload),Jsonb(bindings)))
                database.execute("INSERT INTO request_heads VALUES(%s,%s) ON CONFLICT(request_id) DO UPDATE SET revision=excluded.revision",(request.request_id,request.request_revision))
        return self.inspect(request.request_id)

    def _maia_job(self,database,cache):
        execution=cache["execution_generation"]+1
        previous=database.execute("SELECT payload FROM maia_jobs WHERE cache_id=%s AND request_revision=%s ORDER BY execution_generation DESC LIMIT 1",(cache["cache_id"],cache["generation"])).fetchone()
        # Worker request contents, including constraints, are immutable within
        # a work generation. New defaults apply only to a new work generation.
        job=(MaiaJob.model_validate({**previous["payload"],"execution_generation":execution}) if previous else
             build_maia_job(cache["descriptor"],self.workspace_id,cache["cache_id"],cache["generation"],execution))
        payload=job.model_dump(mode="json")
        database.execute("INSERT INTO maia_jobs(job_id,request_revision,execution_generation,cache_id,payload) VALUES(%s,%s,%s,%s,%s)",(job.job_id,job.request_revision,execution,cache["cache_id"],Jsonb(payload)))
        database.execute("UPDATE caches SET execution_generation=%s,state='awaiting_result',error_code=NULL,next_attempt_at=NULL,updated_at=now() WHERE cache_id=%s",(execution,cache["cache_id"]))

    def _new_generation(self,database,cache):
        database.execute("UPDATE caches SET generation=generation+1,state='queued',attempts=0,retryable=true,error_code=NULL,next_attempt_at=NULL,lease_token=NULL,leased_until=NULL,updated_at=now() WHERE cache_id=%s",(cache["cache_id"],))

    def inspect(self,request_id):
        with self.transaction(read_only=True) as database:
            request=self._head(database,request_id)
            sources=[]
            for binding in request["bindings"]:
                cache=database.execute("SELECT * FROM caches WHERE cache_id=%s",(binding["cache_id"],)).fetchone()
                evidence=database.execute("SELECT accepted_at FROM evidence WHERE evidence_id=%s",(cache["accepted_id"],)).fetchone() if cache["accepted_id"] else None
                source=source_inspection(cache,evidence,request,self._retry_eligible,utcnow)
                if cache["kind"] != "maia":
                    source["credential"]=self._credential_status(database)
                    source["provider_origin"]=cache["descriptor"]["provider_origin"]
                    source["provider_configuration_status"]="configured" if source["provider_origin"]==self.configuration.provider_base_url else "unavailable"
                    control=database.execute("SELECT next_attempt_at FROM provider_control WHERE id=1").fetchone()
                    source["provider_retry_at"]=control["next_attempt_at"]
                sources.append(source)
            response=public({"schema_version":1,"workspace_id":self.workspace_id,"request_id":request_id,"request_revision":request["revision"],"snapshot":request["payload"]["snapshot"],"sources":sources})
        if any(source["kind"] != "maia" for source in sources):
            credential=self.credential_status()
            for source in response["sources"]:
                if source["kind"] != "maia":
                    source["credential"]=credential
        return response

    def evidence(self,evidence_id):
        with self.transaction(read_only=True) as database:
            row=database.execute("SELECT * FROM evidence WHERE evidence_id=%s",(evidence_id,)).fetchone()
            if not row:
                raise DomainError("evidence_not_found",404)
            return public({"schema_version":1,"workspace_id":self.workspace_id,**row})

    def _retry_eligible(self,cache,now):
        return retry_eligible(cache,now,self.configuration)

    def _command(self,request_id,command_id,expected_revision,retry):
        with self.transaction() as database:
            database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("command:"+command_id,))
            payload={"request_id":request_id,"expected_revision":expected_revision,"operation":"retry" if retry else "refresh"}
            previous=database.execute("SELECT * FROM commands WHERE command_id=%s",(command_id,)).fetchone()
            if previous:
                if previous["payload"] != payload:
                    raise DomainError("command_replay_conflict")
                return previous["result"]
            request=self._head(database,request_id)
            if expected_revision is not None and expected_revision != request["revision"]:
                raise DomainError("request_revision_conflict")
            for binding in sorted(request["bindings"],key=lambda item:item["cache_id"]):
                cache=database.execute("SELECT * FROM caches WHERE cache_id=%s FOR UPDATE",(binding["cache_id"],)).fetchone()
                if cache["state"] == "terminal":
                    continue
                if retry:
                    if not self._retry_eligible(cache,utcnow()):
                        continue
                    database.execute("UPDATE caches SET state='queued',attempts=0,error_code=NULL,next_attempt_at=NULL WHERE cache_id=%s",(cache["cache_id"],))
                elif cache["state"] not in ("queued","running","retry_wait","waiting_credentials","awaiting_result"):
                    self._new_generation(database,cache)
                current=database.execute("SELECT * FROM caches WHERE cache_id=%s",(cache["cache_id"],)).fetchone()
                if current["kind"] == "maia" and current["state"] == "queued":
                    self._maia_job(database,current)
            result={"schema_version":1,"request_id":request_id,"request_revision":request["revision"],"command_id":command_id,"accepted":True}
            database.execute("INSERT INTO commands VALUES(%s,%s,%s)",(command_id,Jsonb(payload),Jsonb(result)))
            return result

    def refresh(self,request_id,command_id,expected_revision=None):
        return self._command(request_id,command_id,expected_revision,False)

    def retry(self,request_id,command_id,expected_revision=None):
        return self._command(request_id,command_id,expected_revision,True)

    def _credential_status(self,database):
        row=database.execute("SELECT h.generation,h.registration_id,r.status,r.expires_at FROM credential_head h LEFT JOIN credential_registrations r USING(registration_id) WHERE h.id=1").fetchone()
        if not row["registration_id"]:
            return {"generation":row["generation"],"registration_id":None,"status":"credential_missing"}
        if row["expires_at"] and row["expires_at"]<=utcnow() and row["status"] not in ("rejected","revoked"):
            row["status"]="expired"
        return public(row)

    def credential_status(self):
        with self.transaction(read_only=True) as database:
            status=self._credential_status(database)
        if status["status"] in ("registered","accepted"):
            from tempo_intelligence.credentials import CredentialError, PrivateCredentials
            try:
                material=PrivateCredentials(self.configuration.credential_file).load()
                if material.registration_id != status["registration_id"]:
                    status["status"]="credential_registration_mismatch"
                elif material.expires_at and material.expires_at<=utcnow():
                    status["status"]="expired"
            except CredentialError:
                status["status"]="credential_storage_unavailable"
        return status

    def credential_reload(self,material,expected_generation):
        with self.transaction() as database:
            head=database.execute("SELECT * FROM credential_head WHERE id=1 FOR UPDATE").fetchone()
            if head["generation"] != expected_generation:
                raise DomainError("credential_generation_conflict")
            old=database.execute("SELECT * FROM credential_registrations WHERE registration_id=%s",(material.registration_id,)).fetchone()
            if old:
                if head["registration_id"] == material.registration_id and old["status"] in ("registered","accepted"):
                    return self._credential_status(database)
                raise DomainError("registration_id_retired")
            if material.expires_at and material.expires_at<=utcnow():
                raise DomainError("credential_expired",422)
            generation=head["generation"]+1
            database.execute("INSERT INTO credential_registrations(registration_id,generation,status,expires_at) VALUES(%s,%s,'registered',%s)",(material.registration_id,generation,material.expires_at))
            database.execute("UPDATE credential_head SET generation=%s,registration_id=%s WHERE id=1",(generation,material.registration_id))
            database.execute("UPDATE caches SET state='queued',error_code=NULL,next_attempt_at=NULL WHERE state='waiting_credentials'")
            return self._credential_status(database)

    def credential_revoke(self,expected_generation):
        with self.transaction() as database:
            head=database.execute("SELECT * FROM credential_head WHERE id=1 FOR UPDATE").fetchone()
            if head["generation"] != expected_generation:
                raise DomainError("credential_generation_conflict")
            if head["registration_id"]:
                database.execute("UPDATE credential_registrations SET status='revoked' WHERE registration_id=%s",(head["registration_id"],))
            return self._credential_status(database)

    def claim(self):
        with self.transaction(background=True) as database:
            control=database.execute("SELECT * FROM provider_control WHERE id=1 FOR UPDATE SKIP LOCKED").fetchone()
            now=utcnow()
            if not control or (control["next_attempt_at"] and control["next_attempt_at"]>now) or (control["leased_until"] and control["leased_until"]>now):
                return None
            cache=database.execute("SELECT * FROM caches WHERE kind<>'maia' AND descriptor->>'provider_origin'=%s AND ((state IN ('queued','retry_wait','waiting_credentials') AND (next_attempt_at IS NULL OR next_attempt_at<=now())) OR (state='running' AND leased_until<=now())) ORDER BY updated_at,cache_id FOR UPDATE SKIP LOCKED LIMIT 1",(self.configuration.provider_base_url,)).fetchone()
            if not cache:
                return None
            credential=self._credential_status(database)
            if credential["status"] not in ("registered","accepted"):
                database.execute("UPDATE caches SET state='waiting_credentials',error_code=%s,next_attempt_at=now()+interval '60 seconds',lease_token=NULL,leased_until=NULL WHERE cache_id=%s",(credential["status"],cache["cache_id"]))
                return None
            lease=uuid.uuid4().hex
            row=database.execute("UPDATE caches SET state='running',execution_generation=execution_generation+1,lease_token=%s,leased_until=now()+interval '60 seconds',updated_at=now() WHERE cache_id=%s RETURNING *",(lease,cache["cache_id"])).fetchone()
            database.execute("UPDATE provider_control SET lease_token=%s,leased_until=now()+interval '60 seconds' WHERE id=1",(lease,))
            row["credential"]=credential
            return row

    def _fenced(self,database,claim):
        return database.execute("SELECT * FROM caches WHERE cache_id=%s AND generation=%s AND execution_generation=%s AND lease_token=%s AND leased_until>now() AND state='running' FOR UPDATE",(claim["cache_id"],claim["generation"],claim["execution_generation"],claim["lease_token"])).fetchone()

    def _release_provider(self,database,claim,retry_at=None):
        database.execute("UPDATE provider_control SET lease_token=NULL,leased_until=NULL,next_attempt_at=GREATEST(COALESCE(next_attempt_at,now()),COALESCE(%s,now()+interval '1 second')) WHERE id=1 AND lease_token=%s",(retry_at,claim["lease_token"]))

    def _append(self,database,cache,payload):
        evidence_id=uuid.uuid4().hex
        database.execute("INSERT INTO evidence(evidence_id,cache_id,generation,execution_generation,descriptor,input_snapshot,payload,input_digest,output_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(evidence_id,cache["cache_id"],cache["generation"],cache["execution_generation"],Jsonb(cache["descriptor"]),Jsonb(cache["input_snapshot"]),Jsonb(payload),digest(cache["descriptor"]),digest(payload)))
        database.execute("UPDATE caches SET accepted_id=%s,state='complete',retryable=true,error_code=NULL,next_attempt_at=NULL,lease_token=NULL,leased_until=NULL,attempts=0,updated_at=now() WHERE cache_id=%s",(evidence_id,cache["cache_id"]))
        return evidence_id

    def publish(self,claim,payload):
        with self.transaction(background=True) as database:
            # Provider control is always locked before a cache, matching claim order.
            database.execute("SELECT id FROM provider_control WHERE id=1 FOR UPDATE")
            cache=self._fenced(database,claim)
            if not cache:
                return False
            self._append(database,cache,{**payload,"retrieval_id":uuid.uuid4().hex,"retrieved_at":utcnow().isoformat()})
            credential=claim["credential"]
            database.execute("UPDATE credential_registrations SET status='accepted' WHERE registration_id=%s AND generation=%s AND status='registered'",(credential["registration_id"],credential["generation"]))
            self._release_provider(database,claim)
            return True

    def fail(self,claim,code,retryable=False,retry_at=None,auth_failed=False,cancelled=False):
        with self.transaction(background=True) as database:
            database.execute("SELECT id FROM provider_control WHERE id=1 FOR UPDATE")
            if retry_at:
                database.execute("UPDATE provider_control SET next_attempt_at=GREATEST(COALESCE(next_attempt_at,now()),%s) WHERE id=1",(retry_at,))
            cache=self._fenced(database,claim)
            if not cache:
                return False
            if auth_failed:
                credential=claim["credential"]
                database.execute("UPDATE credential_registrations r SET status='rejected' FROM credential_head h WHERE h.id=1 AND h.registration_id=r.registration_id AND h.generation=r.generation AND r.registration_id=%s AND r.generation=%s AND r.status IN ('registered','accepted')",(credential["registration_id"],credential["generation"]))
            attempts,state,deadline,can_retry=failure_transition(cache,code,retryable,retry_at,auth_failed,cancelled,utcnow)
            database.execute("UPDATE caches SET state=%s,attempts=%s,retryable=%s,error_code=%s,next_attempt_at=%s,lease_token=NULL,leased_until=NULL,updated_at=now() WHERE cache_id=%s",(state,attempts,can_retry,code,deadline,cache["cache_id"]))
            self._release_provider(database,claim,retry_at)
            return True

    def maia_jobs(self,request_id):
        with self.transaction(read_only=True) as database:
            request=self._head(database,request_id)
            result=[]
            for binding in request["bindings"]:
                if binding["kind"] == "maia":
                    rows=database.execute("SELECT j.payload FROM maia_jobs j JOIN caches c ON j.cache_id=c.cache_id AND j.request_revision=c.generation AND j.execution_generation=c.execution_generation WHERE j.cache_id=%s AND j.status IN ('pending','running')",(binding["cache_id"],)).fetchall()
                    result.extend(row["payload"] for row in rows)
            return {"schema_version":1,"jobs":result}

    def accept_maia(self,result:MaiaResult):
        if result.workspace_id != self.workspace_id:
            raise DomainError("workspace_mismatch",403)
        wire=result.model_dump(mode="json")
        with self.transaction() as database:
            job=database.execute("SELECT * FROM maia_jobs WHERE job_id=%s AND request_revision=%s AND execution_generation=%s FOR UPDATE",(result.job_id,result.request_revision,result.execution_generation)).fetchone()
            if not job:
                raise DomainError("unknown_maia_execution")
            if job["result_payload"] is not None:
                if serialize_contract(MaiaResult.model_validate(job["result_payload"])) != serialize_contract(result):
                    raise DomainError("maia_result_replay_conflict")
                return {"schema_version":1,"evidence_id":job["evidence_id"],"replayed":True}
            cache=database.execute("SELECT * FROM caches WHERE cache_id=%s FOR UPDATE",(job["cache_id"],)).fetchone()
            if cache["generation"] != result.request_revision or cache["execution_generation"] != result.execution_generation or job["status"] in ("failed","cancelled") or cache["state"] != "awaiting_result":
                raise DomainError("stale_maia_execution")
            try:
                validate_maia_result(MaiaJob.model_validate(job["payload"]),result)
            except ValueError:
                raise DomainError("maia_result_mismatch",422) from None
            evidence_id=self._append(database,cache,{"schema_version":1,"source":"maia","job":job["payload"],"result":wire,"artifact_sha256":cache["descriptor"]["artifact_sha256"],"limitations":["v1_artifact_identity_is_configured_version_binding"]})
            database.execute("UPDATE maia_jobs SET status='completed',result_payload=%s,evidence_id=%s WHERE job_id=%s AND request_revision=%s AND execution_generation=%s",(Jsonb(wire),evidence_id,result.job_id,result.request_revision,result.execution_generation))
            return {"schema_version":1,"evidence_id":evidence_id,"replayed":False}

    def maia_status(self,status:JobStatus):
        if status.workspace_id != self.workspace_id:
            raise DomainError("workspace_mismatch",403)
        with self.transaction() as database:
            job=database.execute("SELECT * FROM maia_jobs WHERE job_id=%s AND request_revision=%s AND execution_generation=%s FOR UPDATE",(status.job_id,status.request_revision,status.execution_generation)).fetchone()
            if not job:
                raise DomainError("unknown_maia_execution")
            wire=status.model_dump(mode="json")
            if job["status_payload"] == wire:
                return {"schema_version":1,"status":status.status}
            cache=database.execute("SELECT * FROM caches WHERE cache_id=%s FOR UPDATE",(job["cache_id"],)).fetchone()
            if cache["generation"] != status.request_revision or cache["execution_generation"] != status.execution_generation or job["evidence_id"]:
                raise DomainError("stale_maia_execution")
            if status.status not in ("pending","running","failed","cancelled"):
                raise DomainError("verified_result_required",422)
            if job["status"] in ("failed","cancelled") or job["status"]=="running" and status.status=="pending":
                raise DomainError("maia_status_transition_conflict")
            database.execute("UPDATE maia_jobs SET status=%s,status_payload=%s WHERE job_id=%s AND request_revision=%s AND execution_generation=%s",(status.status,Jsonb(wire),status.job_id,status.request_revision,status.execution_generation))
            if status.status in ("failed","cancelled"):
                database.execute("UPDATE caches SET state='failed',error_code=%s,retryable=%s WHERE cache_id=%s",("maia_"+status.status,status.retryable,cache["cache_id"]))
            return {"schema_version":1,"status":status.status}
