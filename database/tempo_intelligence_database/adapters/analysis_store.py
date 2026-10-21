"""Intelligence-owned durable analysis. Network/engine work never holds a transaction."""
from contextlib import contextmanager
from collections import defaultdict
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

    def submit(self,value):
        self._workspace(value.workspace_id)
        source,games=self._input(value.input_id,value.input_revision)
        if source["state"] != "accepted":
            raise DomainError("analysis_input_not_accepted")
        records=self.evidence_records(value.source_evidence_ids)
        for record in records:
            if value.ranking_evidence_id==record["evidence_id"] and record["descriptor"]["kind"]!=value.ranking_source:
                raise DomainError("ranking_population_mismatch",422)
        positions=ordered_positions(games)
        if len(positions)>10000:
            raise DomainError("analysis_scope_exceeds_position_limit",422)
        payload={**value.model_dump(mode="json"), "derivation_version":2}
        with self.owner.transaction() as db:
            # Per-run transaction lock prevents concurrent first submissions/head replacement.
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("analysis:"+value.run_id,))
            old=db.execute("SELECT * FROM analysis_runs WHERE run_id=%s AND revision=%s",(value.run_id,value.revision)).fetchone()
            head=db.execute("SELECT revision FROM analysis_heads WHERE run_id=%s",(value.run_id,)).fetchone()
            if old and old["digest"] != payload_digest(value):
                raise DomainError("analysis_run_conflict")
            if head and (not old or old["state"]=="staging") and (value.expected_revision != head["revision"] or value.revision<=head["revision"]):
                raise DomainError("stale_analysis_run")
            if not head and value.expected_revision is not None:
                raise DomainError("stale_analysis_run")
            previous=db.execute("SELECT requested_generation,last_complete_generation FROM analysis_runs WHERE run_id=%s AND revision=%s",(value.run_id,head["revision"])).fetchone() if head else None
            next_generation=previous["requested_generation"]+1 if previous else 1
            db.execute("INSERT INTO analysis_runs(run_id,revision,payload,digest,budget,requested_generation,last_complete_generation) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(value.run_id,value.revision,Jsonb(payload),payload_digest(value),value.position_budget,next_generation,previous["last_complete_generation"] if previous else None))
        for start in range(0,len(positions),64):
            with self.owner.transaction() as db:
                for ordinal,position in enumerate(positions[start:start+64],start):
                    position_id=digest({"game":position["game_id"],"revision":position["source_revision"],"ply":position["ply"]})
                    position.update(position_id=position_id,workspace_id=self.workspace)
                    db.execute("INSERT INTO analysis_positions(run_id,revision,ordinal,position_id,payload,state) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(value.run_id,value.revision,ordinal,position_id,Jsonb(position),"pending" if ordinal<value.position_budget else "unbudgeted"))
        with self.owner.transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("analysis:"+value.run_id,))
            head=db.execute("SELECT revision FROM analysis_heads WHERE run_id=%s FOR UPDATE",(value.run_id,)).fetchone()
            if head and head["revision"]>value.revision:
                return self._status(db,self._head(db,value.run_id))
            db.execute("UPDATE analysis_runs SET state='pending' WHERE run_id=%s AND revision=%s AND state='staging'",(value.run_id,value.revision))
            db.execute("INSERT INTO analysis_heads VALUES(%s,%s) ON CONFLICT(run_id) DO UPDATE SET revision=EXCLUDED.revision WHERE analysis_heads.revision<EXCLUDED.revision",(value.run_id,value.revision))
            return self._status(db,self._head(db,value.run_id))

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
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("analysis-command:"+command_id,))
        row=db.execute("SELECT * FROM analysis_commands WHERE command_id=%s",(command_id,)).fetchone()
        if row and row["digest"]!=digest(payload):
            raise DomainError("analysis_command_conflict")
        return {**row["payload"], "request_digest": row["digest"]} if row else None

    def _save_receipt(self,db,command_id,payload,result):
        result["request_digest"] = digest(payload)
        db.execute("INSERT INTO analysis_commands VALUES(%s,%s,%s)",(command_id,digest(payload),Jsonb(result)))

    def receipt(self,command_id):
        return read_receipt(command_id,self.read_transaction)

    def command(self,run_id,command):
        payload={"run_id":run_id,**command.model_dump(mode="json")}
        with self.owner.transaction() as db:
            run=self._head(db,run_id,True)
            saved=self._receipt(db,command.command_id,payload)
            if saved:
                return saved
            if run["revision"]!=command.expected_revision:
                raise DomainError("stale_analysis_run")
            if run["state"]=="cancel_requested" and command.action!="cancel" or run["state"]=="cancelled" and command.action!="resume" and command.action!="cancel":
                raise DomainError("analysis_cancellation_incomplete")
            if command.action in {"continue","resume","retry"} and run["payload"].get("derivation_version",1)!=2:
                raise DomainError("legacy_analysis_revision_required")
            if command.action in {"continue","resume","retry"}:
                if command.action=="continue":
                    db.execute("UPDATE analysis_runs SET budget=budget+%s WHERE run_id=%s AND revision=%s",(command.budget,run_id,run["revision"]))
                    db.execute("UPDATE analysis_positions SET state='pending' WHERE run_id=%s AND revision=%s AND state='unbudgeted' AND ordinal<%s",(run_id,run["revision"],run["budget"]+command.budget))
                if command.action=="retry":
                    db.execute("UPDATE analysis_positions SET state='pending',stage='root',accepted_root=NULL,accepted_played=NULL,attempts=0,next_attempt_at=NULL,error_code=NULL WHERE run_id=%s AND revision=%s AND state='complete' AND error_code='insufficient_quality'",(run_id,run["revision"]))
                if command.action in {"retry","resume"}:
                    db.execute("UPDATE analysis_positions SET state='pending',attempts=0,next_attempt_at=NULL WHERE run_id=%s AND revision=%s AND state IN ('failed','cancelled') AND error_code IS DISTINCT FROM 'incompatible_engine_profile'",(run_id,run["revision"]))
                db.execute("UPDATE analysis_runs SET state='pending',requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))
            elif command.action=="cancel":
                db.execute("UPDATE analysis_positions SET state='cancelled',lease_token=NULL,leased_until=NULL WHERE run_id=%s AND revision=%s AND state='running' AND leased_until<=now()",(run_id,run["revision"]))
                db.execute("UPDATE analysis_runs SET state='cancel_requested',requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))
                db.execute("UPDATE analysis_positions SET state='cancelled' WHERE run_id=%s AND revision=%s AND state IN ('pending','retry_wait')",(run_id,run["revision"]))
                running=db.execute("SELECT 1 FROM analysis_positions WHERE run_id=%s AND revision=%s AND state='running' LIMIT 1",(run_id,run["revision"])).fetchone()
                if not running:
                    db.execute("UPDATE analysis_runs SET state='cancelled' WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))
            else:
                raise DomainError("invalid_run_action",422)
            result=self._status(db,self._head(db,run_id)); result.update(accepted=True,command_id=command.command_id)
            self._save_receipt(db,command.command_id,payload,result)
            return result

    def capture(self,run_id):
        with self.owner.transaction(read_only=True) as db:
            run=self._head(db,run_id)
            rows=db.execute("SELECT * FROM analysis_positions WHERE run_id=%s AND revision=%s ORDER BY ordinal",(run_id,run["revision"])).fetchall()
            result_ids={r[k] for r in rows for k in ("accepted_root","accepted_played") if r[k]}
            results={r["result_id"]:r["payload"] for r in db.execute("SELECT * FROM analysis_results WHERE result_id=ANY(%s)",(list(result_ids),))} if result_ids else {}
            dismissals={r["finding_id"]:r for r in db.execute("SELECT * FROM analysis_dismissals")}
        source,games=self._input(run["payload"]["input_id"],run["payload"]["input_revision"])
        return run,rows,results,dismissals,source,games

    def _derive_evidence(self,run,rows,results,source,games):
        findings,unknown=source_findings(source["payload"],games)
        from tempo_intelligence.analysis.findings import merge_findings
        source_records=self.evidence_records(run["payload"]["source_evidence_ids"],background=True)
        return merge_findings(self.workspace,run,rows,results,source,games,findings,unknown,source_records)

    def derive(self,run_id,before_publish=None):
        run,rows,results,dismissals,source,games=self.capture(run_id)
        if run["published_generation"]==run["requested_generation"]:
            return False
        legacy_metadata=None
        if run["payload"].get("derivation_version",1)!=2:
            with self.owner.transaction(background=True,read_only=True) as db:
                saved=db.execute("SELECT payload FROM analysis_reports WHERE run_id=%s AND revision=%s AND generation=%s",(run_id,run["revision"],run["published_generation"])).fetchone()
                if not saved:
                    raise DomainError("legacy_analysis_revision_required")
                legacy_metadata=saved["payload"]
                findings=[row["payload"] for row in db.execute("SELECT payload FROM analysis_report_items WHERE run_id=%s AND revision=%s AND generation=%s ORDER BY ordinal",(run_id,run["revision"],run["published_generation"]))]
            unknown=legacy_metadata.get("unknown_coverage",[])
            incompatible_positions=set()
        else:
            findings,unknown,incompatible_positions=self._derive_evidence(run,rows,results,source,games)
        automatic_reopens=[]
        incident_findings=defaultdict(list)
        for item in findings:incident_findings[item["incident_id"]].append({"finding_id":item["finding_id"],"category":item["category"]})
        for item in findings:
            item["related_findings"]=[other for other in incident_findings[item["incident_id"]] if other["finding_id"]!=item["finding_id"]]
            item["dismissal_state"]="open"
            dismissed=dismissals.get(item["finding_id"])
            if legacy_metadata is None:
                item["evidence_revision"]=digest(item)
            item["finding_revision"]=run["requested_generation"]
            if dismissed and dismissed["state"]=="dismissed" and len(set(item["supporting_games"])-set(dismissed["supporting_games"]))<3:
                item["dismissal_state"]="dismissed"
            if dismissed and dismissed["state"]=="dismissed" and item["dismissal_state"]=="open":
                automatic_reopens.append((item["finding_id"],dismissed["revision"],item["supporting_games"]))
            item["actions"]["dismiss"]={"available":item["dismissal_state"]!="dismissed","reason":"already_dismissed" if item["dismissal_state"]=="dismissed" else None}
            item["actions"]["reopen"]={"available":item["dismissal_state"]=="dismissed","reason":"not_dismissed" if item["dismissal_state"]!="dismissed" else None}
            item["input_id"]=source["input_id"];item["input_revision"]=source["revision"]
        from tempo_intelligence.analysis.read_models import Finding
        findings=[Finding.model_validate(item).model_dump(mode="json") for item in findings]
        generation=run["requested_generation"]
        for start in range(0,len(findings),64):
            with self.owner.transaction(background=True) as db:
                for ordinal,item in enumerate(findings[start:start+64],start):
                    db.execute("INSERT INTO analysis_report_items VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(run_id,run["revision"],generation,item["finding_id"],ordinal,Jsonb(item)))
        complete=not incompatible_positions and source["payload"]["completeness"]=="complete" and all(r["state"]=="complete" and r["error_code"] is None for r in rows)
        metadata={"derivation_version":2,"ordering":"source-rank-semantic-v2","schema_version":1,"workspace_id":self.workspace,"run_id":run_id,"revision":run["revision"],"generation":generation,"input_id":source["input_id"],"input_revision":source["revision"],"input_producer":source["payload"]["producer"],"source_provenance":(source["payload"].get("producer_manifest") or {}).get("provenance",{"kind":"synthetic"}),"scope":{key:source["payload"][key] for key in ("game_ids","window_start","window_end","selections","producer_version","source_artifact_digest")},"source_completeness":source["payload"]["completeness"],"reasons":source["payload"]["reasons"]+(["incompatible_engine_profile"] if incompatible_positions or any(row["error_code"]=="incompatible_engine_profile" for row in rows) else []),"unknown_coverage":unknown[:100],"unknown_coverage_count":len(unknown),"engine_scan_complete":not incompatible_positions and all(r["state"]=="complete" for r in rows),"unprocessed_count":sum(r["state"]!="complete" or r["position_id"] in incompatible_positions for r in rows),"engine_availability":"unavailable" if any(r["error_code"]=="engine_unavailable" for r in rows) else "pending_or_available","insufficient_quality_count":sum(r["error_code"]=="insufficient_quality" for r in rows),"finding_count":len(findings),"complete":complete,"policy_version":"c-analysis-v1"}
        if legacy_metadata is not None:
            complete=legacy_metadata["complete"]
            metadata={**legacy_metadata,"generation":generation,"derivation_version":1,"ordering":legacy_metadata.get("ordering","category-source-rank-semantic-v1")}
        if before_publish:
            before_publish()
        with self.owner.transaction(background=True) as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('analysis-user-state',0))")
            current=self._head(db,run_id,True)
            if current["revision"]!=run["revision"] or current["requested_generation"]!=generation:
                return False
            # A sibling run can dismiss this identity before our first publication.
            revisions={row['finding_id']:row['revision'] for row in db.execute('SELECT finding_id,revision FROM analysis_dismissals WHERE finding_id=ANY(%s)',([item['finding_id'] for item in findings],))}
            captured_revisions={identity:dismissals[identity]['revision'] for identity in revisions if identity in dismissals}
            if revisions!=captured_revisions:
                db.execute('UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s',(run_id,run['revision']))
                return False
            for finding_id,dismissal_revision,supporting_games in automatic_reopens:
                changed=db.execute("UPDATE analysis_dismissals SET state='open',revision=revision+1,supporting_games=%s WHERE finding_id=%s AND revision=%s AND state='dismissed' RETURNING finding_id",(Jsonb(supporting_games),finding_id,dismissal_revision)).fetchone()
                if changed:
                    db.execute("UPDATE analysis_runs r SET requested_generation=requested_generation+1 FROM analysis_heads h WHERE r.run_id=h.run_id AND r.revision=h.revision AND r.run_id<>%s AND EXISTS(SELECT 1 FROM analysis_report_items i WHERE i.run_id=r.run_id AND i.revision=r.revision AND i.generation=r.published_generation AND i.finding_id=%s)",(run_id,finding_id))
            db.execute("INSERT INTO analysis_reports VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(run_id,run["revision"],generation,Jsonb(metadata)))
            pending=any(r["state"] in {"pending","running","retry_wait"} for r in rows)
            state="complete" if complete else "pending" if pending else "budget_exhausted" if any(r["state"]=="unbudgeted" for r in rows) else "partial"
            db.execute("UPDATE analysis_runs SET published_generation=%s,last_complete_generation=CASE WHEN %s THEN %s ELSE last_complete_generation END,state=CASE WHEN state IN ('cancel_requested','cancelled') THEN state ELSE %s END WHERE run_id=%s AND revision=%s",(generation,complete,generation,state,run_id,run["revision"]))
        return True

    def report(self,run_id,*,after=None,limit=50,category=None,dismissal=None,generation=None):
        return read_report(run_id,lambda:self.workspace,self.read_transaction,after=after,limit=limit,
            category=category,dismissal=dismissal,generation=generation)

    def finding(self,run_id,finding_id,generation):
        return read_finding(run_id,finding_id,generation,lambda:self.workspace,self.read_transaction)

    def dismiss(self,run_id,finding_id,generation,command):
        payload={"run_id":run_id,"finding_id":finding_id,"generation":generation,**command.model_dump(mode="json")}
        with self.owner.transaction() as db:
            saved=self._receipt(db,command.command_id,payload)
            if saved:
                return saved
        item=self.finding(run_id,finding_id,generation)["finding"]
        with self.owner.transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('analysis-user-state',0))")
            run=self._head(db,run_id,True)
            saved=self._receipt(db,command.command_id,payload)
            if saved:
                return saved
            if command.action not in {"dismiss","reopen"} or command.expected_revision!=item["finding_revision"] or run["published_generation"]!=generation:
                raise DomainError("stale_analysis_finding")
            state="dismissed" if command.action=="dismiss" else "open"
            db.execute("INSERT INTO analysis_dismissals VALUES(%s,1,%s,%s) ON CONFLICT(finding_id) DO UPDATE SET revision=analysis_dismissals.revision+1,state=EXCLUDED.state,supporting_games=EXCLUDED.supporting_games",(finding_id,state,Jsonb(item["supporting_games"])))
            db.execute("UPDATE analysis_runs r SET requested_generation=requested_generation+1 FROM analysis_heads h WHERE r.run_id=h.run_id AND r.revision=h.revision AND (r.run_id=%s OR EXISTS(SELECT 1 FROM analysis_report_items i WHERE i.run_id=r.run_id AND i.revision=r.revision AND i.generation=r.published_generation AND i.finding_id=%s))",(run_id,finding_id))
            result={"schema_version":1,"workspace_id":self.workspace,"accepted":True,"command_id":command.command_id,"dismissal_state":state,"publication_pending":True}
            self._save_receipt(db,command.command_id,payload,result)
        return result

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
        with self.owner.transaction(background=True,read_only=True) as db:
            row=db.execute("SELECT r.run_id FROM analysis_runs r JOIN analysis_heads h USING(run_id,revision) WHERE r.state<>'staging' AND (r.payload->>'derivation_version'='2' OR r.published_generation IS NOT NULL) AND r.requested_generation IS DISTINCT FROM r.published_generation ORDER BY r.created_at,r.run_id LIMIT 1").fetchone()
        return self.derive(row["run_id"]) if row else False

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
