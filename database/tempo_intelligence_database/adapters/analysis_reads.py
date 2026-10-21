"""Fixed Analysis read projections and storage guards on the caller's snapshot."""
from tempo_intelligence.errors import DomainError


class AnalysisReads:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def inputs(self):
        return self._database.execute("SELECT input_id,revision,state,payload->'completeness' AS completeness,payload->'game_ids' AS game_ids,payload->'selections' AS selections FROM analysis_inputs ORDER BY input_id,revision DESC LIMIT 100").fetchall()

    def input(self,input_id,revision):
        row=self._database.execute("SELECT * FROM analysis_inputs WHERE input_id=%s AND revision=%s",(input_id,revision)).fetchone()
        if not row:
            raise DomainError("analysis_input_not_found",404)
        return row

    def pages(self,input_id,revision):
        return self._database.execute("SELECT payload FROM analysis_pages WHERE input_id=%s AND revision=%s ORDER BY page_id",(input_id,revision)).fetchall()

    def counts(self,run):
        return {r["state"]:r["count"] for r in self._database.execute("SELECT state,count(*) FROM analysis_positions WHERE run_id=%s AND revision=%s GROUP BY state",(run["run_id"],run["revision"]))}

    def runs(self):
        return self._database.execute("SELECT r.* FROM analysis_runs r JOIN analysis_heads h USING(run_id,revision) ORDER BY r.created_at DESC,r.run_id LIMIT 100").fetchall()

    def head(self,run_id):
        return self._owner._head(self._database,run_id)

    def status(self,run):
        return self._owner._status(self._database,run)

    def receipt(self,command_id):
        row=self._database.execute("SELECT payload,digest FROM analysis_commands WHERE command_id=%s",(command_id,)).fetchone()
        if not row:
            raise DomainError("analysis_command_not_found",404)
        return row

    def previous_report(self,run_id,run):
        return self._database.execute("SELECT revision,published_generation FROM analysis_runs WHERE run_id=%s AND revision<%s AND published_generation IS NOT NULL ORDER BY revision DESC LIMIT 1",(run_id,run["revision"])).fetchone()

    def report_revision(self,run_id,selected):
        return self._database.execute("SELECT revision FROM analysis_reports WHERE run_id=%s AND generation=%s",(run_id,selected)).fetchone()

    def report(self,run_id,report_revision,selected):
        report=self._database.execute("SELECT payload FROM analysis_reports WHERE run_id=%s AND revision=%s AND generation=%s",(run_id,report_revision,selected)).fetchone()
        if not report:
            raise DomainError("analysis_generation_not_found",404)
        return report

    def report_items(self,run_id,report_revision,selected,last,category,dismissal,limit):
        return self._database.execute("SELECT ordinal,payload FROM analysis_report_items WHERE run_id=%s AND revision=%s AND generation=%s AND ordinal>%s AND (%s::text IS NULL OR payload->>'category'=%s) AND (%s::text IS NULL OR payload->>'dismissal_state'=%s) ORDER BY ordinal LIMIT %s",(run_id,report_revision,selected,last,category,category,dismissal,dismissal,limit+1)).fetchall()

    def previous_generation(self,run_id,run):
        return self._database.execute("SELECT published_generation FROM analysis_runs WHERE run_id=%s AND revision<%s AND published_generation IS NOT NULL ORDER BY revision DESC LIMIT 1",(run_id,run["revision"])).fetchone()

    def finding(self,run_id,generation,finding_id,visible):
        row=self._database.execute("SELECT payload FROM analysis_report_items WHERE run_id=%s AND generation=%s AND finding_id=%s",(run_id,generation,finding_id)).fetchone()
        if not row or generation!=visible:
            raise DomainError("stale_analysis_finding")
        return row

    def remaining(self,run_id,run,last,limit):
        return self._database.execute("SELECT ordinal,position_id,state,error_code,payload->'game_id' AS game_id,payload->'ply' AS ply FROM analysis_positions WHERE run_id=%s AND revision=%s AND ordinal>%s AND (state<>'complete' OR error_code='insufficient_quality') ORDER BY ordinal LIMIT %s",(run_id,run["revision"],last,limit+1)).fetchall()

    def engine_result(self,result_id):
        row=self._database.execute("SELECT r.payload AS result,e.payload AS job FROM analysis_results r JOIN analysis_executions e USING(job_id,execution_generation) WHERE r.result_id=%s",(result_id,)).fetchone()
        if not row:
            raise DomainError("analysis_result_not_found",404)
        return row

    def page_status(self,input_id,revision):
        return self._database.execute("SELECT page_id,digest FROM analysis_pages WHERE input_id=%s AND revision=%s ORDER BY page_id",(input_id,revision)).fetchall()
