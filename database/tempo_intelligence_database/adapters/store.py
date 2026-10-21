"""Intelligence owns every transaction here; no provider executes inside this boundary."""
from contextlib import contextmanager
from datetime import datetime, timezone
import uuid
import time

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from tempo_analysis_jobs_contracts.v1 import MaiaResult, JobStatus

from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import canonical_json, digest
from tempo_intelligence.maia_work import accept_maia, create_maia_job, maia_jobs, maia_status, require_profile
from tempo_intelligence_database.ddl.migrate import expected_migrations, ANALYSIS_IMMUTABLE_TABLES, ANALYSIS_MUTABLE_COLUMNS
from tempo_intelligence.runtime import BackgroundConnection, BACKGROUND_OPERATION_SECONDS, background_deadline
from tempo_intelligence.serialization import public
from tempo_intelligence.source_requests import (command_request, failure_transition, inspect_request,
    read_evidence, retry_eligible, submit_request)
from tempo_intelligence.provider_work import claim_source, publish_source
from tempo_intelligence_database.adapters.request_inspection import RequestInspection
from tempo_intelligence_database.adapters.request_commands import RequestCommands
from tempo_intelligence_database.adapters.maia_operations import MaiaOperations
from tempo_intelligence_database.adapters.request_admission import RequestAdmission
from tempo_intelligence_database.adapters.provider_operations import ProviderOperations


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
        return require_profile(descriptor,lambda:self.configuration.model_profiles,MaiaOperations(self,database))

    @contextmanager
    def admission_transaction(self):
        with self.transaction() as database:
            yield RequestAdmission(self,database)

    def submit(self,request):
        return submit_request(request,lambda:self.workspace_id,lambda:self.configuration,self.admission_transaction,
            lambda:self.inspect,lambda:utcnow())

    def _maia_job(self,database,cache):
        return create_maia_job(cache,lambda:self.workspace_id,MaiaOperations(self,database))

    def _new_generation(self,database,cache):
        database.execute("UPDATE caches SET generation=generation+1,state='queued',attempts=0,retryable=true,error_code=NULL,next_attempt_at=NULL,lease_token=NULL,leased_until=NULL,updated_at=now() WHERE cache_id=%s",(cache["cache_id"],))

    @contextmanager
    def inspection_transaction(self):
        with self.transaction(read_only=True) as database:
            yield RequestInspection(self,database)

    def inspect(self,request_id):
        return inspect_request(request_id,self.workspace_id,self.configuration,self.inspection_transaction,
            self._retry_eligible,self.credential_status,utcnow)

    @contextmanager
    def maia_read_transaction(self):
        with self.transaction(read_only=True) as database:
            yield MaiaOperations(self,database)

    @contextmanager
    def maia_write_transaction(self):
        with self.transaction() as database:
            yield MaiaOperations(self,database)

    def evidence(self,evidence_id):
        return read_evidence(evidence_id,lambda:self.workspace_id,self.maia_read_transaction)

    def _retry_eligible(self,cache,now):
        return retry_eligible(cache,now,self.configuration)

    @contextmanager
    def command_transaction(self):
        with self.transaction() as database:
            yield RequestCommands(self,database)

    def _command(self,request_id,command_id,expected_revision,retry):
        return command_request(request_id,command_id,expected_revision,retry,self.command_transaction,
            lambda cache:self._retry_eligible(cache,utcnow()))

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

    @contextmanager
    def provider_transaction(self):
        with self.transaction(background=True) as database:
            yield ProviderOperations(self,database)

    def claim(self):
        return claim_source(self.provider_transaction,lambda:utcnow(),lambda:uuid.uuid4().hex)

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
        return publish_source(claim,payload,self.provider_transaction,lambda:uuid.uuid4().hex,lambda:utcnow())

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
        return maia_jobs(request_id,self.maia_read_transaction)

    def accept_maia(self,result:MaiaResult):
        return accept_maia(result,lambda:self.workspace_id,self.maia_write_transaction)

    def maia_status(self,status:JobStatus):
        return maia_status(status,lambda:self.workspace_id,self.maia_write_transaction)
