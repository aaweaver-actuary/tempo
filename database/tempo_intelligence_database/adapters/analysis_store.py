"""Intelligence-owned durable analysis. Network/engine work never holds a transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import uuid
from psycopg.types.json import Jsonb
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import digest
from tempo_intelligence.analysis.models import Manifest, Page, RunRequest, payload_digest
from tempo_intelligence.analysis.classify import ordered_positions, source_findings
from tempo_intelligence.analysis.engine import validate_result, comparison, compatible_worker, validate_pair_jobs
from tempo_intelligence.analysis.read_work import (require_workspace, read_inputs, read_input, run_status, read_runs,
    read_receipt, read_report, read_finding, export_finding, read_remaining, read_engine_result, read_input_status)
from tempo_intelligence_database.adapters.analysis_reads import AnalysisReads
from tempo_intelligence.analysis.input_work import admit_manifest, admit_page, finalize_input
from tempo_intelligence_database.adapters.analysis_inputs import AnalysisInputs
from tempo_intelligence.analysis.run_work import submit_run, receipt_result, save_receipt_result, command_run, dismiss_finding
from tempo_intelligence_database.adapters.analysis_runs import AnalysisRunOperations
from tempo_intelligence.analysis.publication_work import capture_run, derive_report, derive_next_report
from tempo_intelligence_database.adapters.analysis_publication import AnalysisPublicationOperations


class AnalysisStore:
    def __init__(self, owner):
        self.owner = owner
        self.workspace = owner.workspace_id

    def _workspace(self, workspace):
        return require_workspace(workspace,lambda:self.workspace)

    @contextmanager
    def input_transaction(self):
        with self.owner.transaction() as db:
            yield AnalysisInputs(self,db)

    def manifest(self, value):
        return admit_manifest(value,lambda:self._workspace,lambda:self.workspace,self.input_transaction)

    def page(self, value):
        return admit_page(value,lambda:self.workspace,self.input_transaction)

    @contextmanager
    def read_transaction(self):
        with self.owner.transaction(read_only=True) as db:
            yield AnalysisReads(self,db)

    def inputs(self):
        return read_inputs(lambda:self.workspace,self.read_transaction)

    def _input(self,input_id,revision):
        return read_input(input_id,revision,self.read_transaction)

    def finalize(self,input_id,command):
        return finalize_input(input_id,command,lambda:self._input,lambda:self.workspace,self.input_transaction)

    def _head(self,db,run_id,lock=False):
        row=db.execute("SELECT r.* FROM analysis_runs r JOIN analysis_heads h USING(run_id,revision) WHERE run_id=%s"+(" FOR UPDATE OF r" if lock else ""),(run_id,)).fetchone()
        if not row:
            raise DomainError("analysis_run_not_found",404)
        return row

    @contextmanager
    def run_transaction(self):
        with self.owner.transaction() as db:
            yield AnalysisRunOperations(self,db)

    def submit(self,value):
        return submit_run(value,lambda:self._workspace,lambda:self._input,lambda:self.evidence_records,
            lambda:self.workspace,self.run_transaction)

    def _status(self,db,run):
        return run_status(run,lambda:self.workspace,AnalysisReads(self,db))

    def status(self,run_id):
        with self.owner.transaction(read_only=True) as db:
            return self._status(db,self._head(db,run_id))

    def runs(self):
        return read_runs(lambda:self.workspace,self.read_transaction)

    def games(self,run_id,after=None,limit=10):
        from tempo_intelligence.analysis.games_reader import GamesReader
        return GamesReader(self.owner).games(run_id,after,limit)

    def _receipt(self,db,command_id,payload):
        return receipt_result(command_id,payload,AnalysisRunOperations(self,db))

    def _save_receipt(self,db,command_id,payload,result):
        return save_receipt_result(command_id,payload,result,AnalysisRunOperations(self,db))

    def receipt(self,command_id):
        return read_receipt(command_id,self.read_transaction)

    def command(self,run_id,command):
        return command_run(run_id,command,self.run_transaction)

    @contextmanager
    def capture_transaction(self):
        with self.owner.transaction(read_only=True) as db:
            yield AnalysisPublicationOperations(self,db)

    @contextmanager
    def publication_read_transaction(self):
        with self.owner.transaction(background=True,read_only=True) as db:
            yield AnalysisPublicationOperations(self,db)

    @contextmanager
    def publication_transaction(self):
        with self.owner.transaction(background=True) as db:
            yield AnalysisPublicationOperations(self,db)

    def capture(self,run_id):
        return capture_run(run_id,lambda:self._input,self.capture_transaction)

    def _derive_evidence(self,run,rows,results,source,games):
        findings,unknown=source_findings(source["payload"],games)
        from tempo_intelligence.analysis.findings import merge_findings
        source_records=self.evidence_records(run["payload"]["source_evidence_ids"],background=True)
        return merge_findings(self.workspace,run,rows,results,source,games,findings,unknown,source_records)

    def derive(self,run_id,before_publish=None):
        return derive_report(run_id,before_publish,lambda:self.capture,lambda:self._derive_evidence,
            lambda:self.workspace,self.publication_read_transaction,self.publication_transaction)

    def report(self,run_id,*,after=None,limit=50,category=None,dismissal=None,generation=None):
        return read_report(run_id,lambda:self.workspace,self.read_transaction,after=after,limit=limit,
            category=category,dismissal=dismissal,generation=generation)

    def finding(self,run_id,finding_id,generation):
        return read_finding(run_id,finding_id,generation,lambda:self.workspace,self.read_transaction)

    def dismiss(self,run_id,finding_id,generation,command):
        return dismiss_finding(run_id,finding_id,generation,command,lambda:self.finding,lambda:self.workspace,self.run_transaction)

    def export(self,run_id,finding_id,generation,kind):
        return export_finding(run_id,finding_id,generation,kind,lambda:self.finding,lambda:self.report,lambda:self.workspace)

    def claim(self,run_id,build_job,worker_identity=None):
        """build_job is a pure B client helper; it performs no process/network I/O."""
        with self.owner.transaction(background=True) as db:
            run=self._head(db,run_id,True)
            if run["state"] in {"cancel_requested","cancelled"}:
                # Expired executions have lost their owning session; late results remain fenced.
                db.execute("UPDATE analysis_positions SET state='cancelled',lease_token=NULL,leased_until=NULL WHERE run_id=%s AND revision=%s AND state='running' AND leased_until<=now()",(run_id,run["revision"]))
                running=db.execute("SELECT 1 FROM analysis_positions WHERE run_id=%s AND revision=%s AND state='running' LIMIT 1",(run_id,run["revision"])).fetchone()
                if not running and run["state"]=="cancel_requested":
                    db.execute("UPDATE analysis_runs SET state='cancelled',requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))
                return None
            if run["payload"].get("derivation_version",1)!=2:
                raise DomainError("legacy_analysis_revision_required")
            row=db.execute("SELECT * FROM analysis_positions WHERE run_id=%s AND revision=%s AND ((state IN ('pending','retry_wait') AND (next_attempt_at IS NULL OR next_attempt_at<=now())) OR (state='running' AND leased_until<=now())) ORDER BY created_at,ordinal,position_id FOR UPDATE SKIP LOCKED LIMIT 1",(run_id,run["revision"])).fetchone()
            if not row:
                return None
            # One live engine lease across this workspace, even with multiple dispatchers/runs.
            if db.execute("SELECT 1 FROM analysis_positions WHERE state='running' AND leased_until>now() LIMIT 1").fetchone():
                return None
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('analysis-engine-capacity',0))")
            if db.execute("SELECT 1 FROM analysis_positions WHERE state='running' AND leased_until>now() LIMIT 1").fetchone():
                return None
            generation=row["execution_generation"]+1
            depth=20
            if row["accepted_root"]:
                root=db.execute("SELECT payload FROM analysis_results WHERE result_id=%s",(row["accepted_root"],)).fetchone()["payload"]
                depth=root["variations"][0]["depth"]
                if worker_identity is not None and not compatible_worker(root, worker_identity):
                    db.execute("UPDATE analysis_positions SET state='failed',error_code='incompatible_engine_profile',lease_token=NULL,leased_until=NULL WHERE run_id=%s AND revision=%s AND position_id=%s",(run_id,run["revision"],row["position_id"]))
                    db.execute("UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))
                    return {"error":"incompatible_engine_profile"}
            job=build_job(row["payload"],run_id,run["revision"],generation,row["stage"],depth)
            from tempo_analysis_jobs_contracts.v1 import StockfishJob
            job=StockfishJob.model_validate(job).model_dump(mode="json")
            position=row["payload"]
            expected_roots=[] if row["stage"]=="root" else [position["actual_move"]]
            if (job["workspace_id"],job["request_revision"],job["execution_generation"],job["fen"],job["moves"],job["search_moves"],job["max_depth"],job["multi_pv"])!=(self.workspace,run["revision"],generation,position["starting_fen"],position["history"],expected_roots,depth,1):
                raise DomainError("analysis_job_input_mismatch",422)
            token=uuid.uuid4().hex
            db.execute("INSERT INTO analysis_executions VALUES(%s,%s,%s,%s,%s,%s,%s)",(job["job_id"],generation,run_id,run["revision"],row["position_id"],row["stage"],Jsonb(job)))
            db.execute("UPDATE analysis_positions SET state='running',execution_generation=%s,lease_token=%s,leased_until=now()+interval '60 seconds',error_code=NULL WHERE run_id=%s AND revision=%s AND position_id=%s",(generation,token,run_id,run["revision"],row["position_id"]))
            return {**row,"execution_generation":generation,"lease_token":token,"job":job}

    def accept(self,claim,result):
        wire=validate_result(claim["job"],result)
        result_id=digest(wire)
        with self.owner.transaction(background=True) as db:
            run=self._head(db,claim["run_id"],True)
            old=db.execute("SELECT * FROM analysis_results WHERE job_id=%s AND execution_generation=%s",(wire["job_id"],wire["execution_generation"])).fetchone()
            if old:
                if old["payload"]!=wire:
                    raise DomainError("analysis_result_replay_conflict")
                return {"result_id":old["result_id"],"replayed":True}
            row=db.execute("SELECT * FROM analysis_positions WHERE run_id=%s AND revision=%s AND position_id=%s FOR UPDATE",(claim["run_id"],claim["revision"],claim["position_id"])).fetchone()
            if run["revision"]!=claim["revision"] or run["state"] in {"cancel_requested","cancelled"} or not row or row["state"]!="running" or row["execution_generation"]!=wire["execution_generation"] or row["lease_token"]!=claim["lease_token"] or row["leased_until"]<=datetime.now(timezone.utc):
                raise DomainError("stale_analysis_execution")
            execution=db.execute("SELECT payload FROM analysis_executions WHERE job_id=%s AND execution_generation=%s",(wire["job_id"],wire["execution_generation"])).fetchone()
            if not execution or execution["payload"]!=claim["job"]:
                raise DomainError("analysis_execution_mismatch")
            db.execute("INSERT INTO analysis_results VALUES(%s,%s,%s,%s)",(result_id,wire["job_id"],wire["execution_generation"],Jsonb(wire)))
            if row["stage"]=="root":
                complete=wire["variations"][0]["moves"][0]==row["payload"]["actual_move"] or wire["variations"][0]["depth"]<18
                db.execute("UPDATE analysis_positions SET accepted_root=%s,state=%s,stage=%s,attempts=0,lease_token=NULL,leased_until=NULL,error_code=%s WHERE run_id=%s AND revision=%s AND position_id=%s",(result_id,"complete" if complete else "pending","root" if complete else "played","insufficient_quality" if wire["variations"][0]["depth"]<18 else None,claim["run_id"],claim["revision"],claim["position_id"]))
            else:
                root=db.execute("SELECT payload FROM analysis_results WHERE result_id=%s",(row["accepted_root"],)).fetchone()["payload"]
                root_job=db.execute("SELECT e.payload FROM analysis_results r JOIN analysis_executions e USING(job_id,execution_generation) WHERE r.result_id=%s",(row["accepted_root"],)).fetchone()["payload"]
                validate_pair_jobs(root_job, claim["job"], row["payload"]["actual_move"])
                compared=comparison(root,wire,row["payload"]["actual_move"])
                db.execute("UPDATE analysis_positions SET accepted_played=%s,state='complete',lease_token=NULL,leased_until=NULL,error_code=%s WHERE run_id=%s AND revision=%s AND position_id=%s",(result_id,"insufficient_quality" if compared["status"]=="insufficient_quality" else None,claim["run_id"],claim["revision"],claim["position_id"]))
            db.execute("UPDATE analysis_positions SET error_code=NULL WHERE run_id=%s AND revision=%s AND state='pending' AND error_code='engine_unavailable'",(claim["run_id"],claim["revision"]))
            db.execute("UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(claim["run_id"],claim["revision"]))
        return {"result_id":result_id,"replayed":False}

    def failure(self,claim,*,retryable=True,code="engine_unavailable",deferred=False,status_payload=None):
        allowed={"engine_unavailable","worker_session_lost","worker_busy","execution_failed","cancelled","incompatible_engine_profile"}
        if code not in allowed:
            code="execution_failed"
        with self.owner.transaction(background=True) as db:
            run=self._head(db,claim["run_id"],True)
            row=db.execute("SELECT * FROM analysis_positions WHERE run_id=%s AND revision=%s AND position_id=%s FOR UPDATE",(claim["run_id"],claim["revision"],claim["position_id"])).fetchone()
            if not row or row["lease_token"]!=claim["lease_token"] or row["execution_generation"]!=claim["execution_generation"] or row["state"]!="running":
                return False
            if status_payload is not None:
                from tempo_analysis_jobs_contracts.v1 import JobStatus
                status=JobStatus.model_validate(status_payload)
                if (status.workspace_id,status.job_id,status.request_revision,status.execution_generation)!=(self.workspace,claim["job"]["job_id"],claim["revision"],claim["execution_generation"]) or status.status not in {"failed","cancelled"}:
                    raise DomainError("analysis_status_identity_mismatch",422)
                event={"model":"JobStatus","payload":status.model_dump(mode="json")}
            else:
                event={"model":"IntelligenceExecutionFailure","reason":code}
            db.execute("INSERT INTO analysis_execution_events VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(digest({"job":claim["job"]["job_id"],"generation":claim["execution_generation"],"event":event}),claim["job"]["job_id"],claim["execution_generation"],Jsonb(event)))
            attempts=row["attempts"]+(0 if deferred else 1)
            cancelled=run["state"] in {"cancel_requested","cancelled"} or code=="cancelled"
            state="cancelled" if cancelled else "retry_wait" if retryable and attempts<3 else "failed"
            db.execute("UPDATE analysis_positions SET state=%s,attempts=%s,error_code=%s,next_attempt_at=now()+(%s * interval '1 second'),lease_token=NULL,leased_until=NULL WHERE run_id=%s AND revision=%s AND position_id=%s",(state,attempts,code,min(60,2**attempts),claim["run_id"],claim["revision"],claim["position_id"]))
            db.execute("UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(claim["run_id"],claim["revision"]))
            if cancelled and not db.execute("SELECT 1 FROM analysis_positions WHERE run_id=%s AND revision=%s AND state='running' LIMIT 1",(claim["run_id"],claim["revision"])).fetchone():
                db.execute("UPDATE analysis_runs SET state='cancelled' WHERE run_id=%s AND revision=%s",(claim["run_id"],claim["revision"]))
        return True

    def remaining(self,run_id,after=None,limit=64):
        return read_remaining(run_id,lambda:self.workspace,self.read_transaction,after,limit)

    def derive_next(self):
        return derive_next_report(lambda:self.derive,self.publication_read_transaction)

    def mark_engine_unavailable(self,run_id):
        with self.owner.transaction(background=True) as db:
            run=self._head(db,run_id,True)
            if run["payload"].get("derivation_version",1)!=2:
                raise DomainError("legacy_analysis_revision_required")
            changed=db.execute("UPDATE analysis_positions SET error_code='engine_unavailable' WHERE run_id=%s AND revision=%s AND state='pending' AND error_code IS NULL RETURNING position_id",(run_id,run["revision"])).fetchall()
            if changed:
                db.execute("UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))

    def engine_result(self,result_id):
        return read_engine_result(result_id,lambda:self.workspace,self.read_transaction)

    def evidence_records(self,identities,background=False):
        if not identities:return []
        with self.owner.transaction(background=background,read_only=True) as db:
            rows=db.execute("SELECT * FROM evidence WHERE evidence_id=ANY(%s) ORDER BY evidence_id",(identities,)).fetchall()
        if {r["evidence_id"] for r in rows}!=set(identities):
            raise DomainError("analysis_source_evidence_missing",404)
        return rows

    def input_status(self,input_id,revision):
        return read_input_status(input_id,revision,lambda:self.workspace,self.read_transaction)
