"""Intelligence-owned durable analysis. Network/engine work never holds a transaction."""
from contextlib import contextmanager
from tempo_intelligence.errors import DomainError
from tempo_intelligence.analysis.classify import source_findings
from tempo_intelligence.analysis.engine_work import claim_engine_work, accept_engine_result, fail_engine_work, mark_engine_work_unavailable
from tempo_intelligence.analysis.read_work import (require_workspace, read_inputs, read_input, run_status, read_runs,
    read_receipt, read_report, read_finding, export_finding, read_remaining, read_engine_result, read_input_status)
from tempo_intelligence_database.adapters.analysis_reads import AnalysisReads
from tempo_intelligence.analysis.input_work import admit_manifest, admit_page, finalize_input
from tempo_intelligence_database.adapters.analysis_inputs import AnalysisInputs
from tempo_intelligence.analysis.run_work import submit_run, receipt_result, save_receipt_result, command_run, dismiss_finding
from tempo_intelligence_database.adapters.analysis_runs import AnalysisRunOperations
from tempo_intelligence.analysis.publication_work import capture_run, derive_report, derive_next_report
from tempo_intelligence_database.adapters.analysis_publication import AnalysisPublicationOperations
from tempo_intelligence_database.adapters.analysis_engine import AnalysisEngineOperations


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

    @contextmanager
    def engine_transaction(self):
        with self.owner.transaction(background=True) as db:
            yield AnalysisEngineOperations(self,db)

    def claim(self,run_id,build_job,worker_identity=None):
        """build_job is a pure B client helper; it performs no process/network I/O."""
        return claim_engine_work(run_id,build_job,worker_identity,lambda:self.workspace,self.engine_transaction)

    def accept(self,claim,result):
        return accept_engine_result(claim,result,self.engine_transaction)

    def failure(self,claim,*,retryable=True,code="engine_unavailable",deferred=False,status_payload=None):
        return fail_engine_work(claim,retryable,code,deferred,status_payload,lambda:self.workspace,self.engine_transaction)

    def remaining(self,run_id,after=None,limit=64):
        return read_remaining(run_id,lambda:self.workspace,self.read_transaction,after,limit)

    def derive_next(self):
        return derive_next_report(lambda:self.derive,self.publication_read_transaction)

    def mark_engine_unavailable(self,run_id):
        return mark_engine_work_unavailable(run_id,self.engine_transaction)

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
