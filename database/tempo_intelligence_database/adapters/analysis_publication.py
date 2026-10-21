"""Fixed report snapshot, staging, publication guards and writes on borrowed transactions."""
from psycopg.types.json import Jsonb


class AnalysisPublicationOperations:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def snapshot(self,run_id):
        run=self._owner._head(self._database,run_id)
        rows=self._database.execute("SELECT * FROM analysis_positions WHERE run_id=%s AND revision=%s ORDER BY ordinal",(run_id,run["revision"])).fetchall()
        result_ids={r[k] for r in rows for k in ("accepted_root","accepted_played") if r[k]}
        results={r["result_id"]:r["payload"] for r in self._database.execute("SELECT * FROM analysis_results WHERE result_id=ANY(%s)",(list(result_ids),))} if result_ids else {}
        dismissals={r["finding_id"]:r for r in self._database.execute("SELECT * FROM analysis_dismissals")}
        return run,rows,results,dismissals

    def legacy_report(self,run_id,run):
        return self._database.execute("SELECT payload FROM analysis_reports WHERE run_id=%s AND revision=%s AND generation=%s",(run_id,run["revision"],run["published_generation"])).fetchone()

    def legacy_findings(self,run_id,run):
        return [row["payload"] for row in self._database.execute("SELECT payload FROM analysis_report_items WHERE run_id=%s AND revision=%s AND generation=%s ORDER BY ordinal",(run_id,run["revision"],run["published_generation"]))]

    def stage_finding(self,run_id,run,generation,item,ordinal):
        self._database.execute("INSERT INTO analysis_report_items VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(run_id,run["revision"],generation,item["finding_id"],ordinal,Jsonb(item)))

    def admit_publication(self,run_id,run,generation,findings,dismissals):
        self._database.execute("SELECT pg_advisory_xact_lock(hashtextextended('analysis-user-state',0))")
        current=self._owner._head(self._database,run_id,True)
        if current["revision"]!=run["revision"] or current["requested_generation"]!=generation:
            return False
        # A sibling run can dismiss this identity before our first publication.
        revisions={row['finding_id']:row['revision'] for row in self._database.execute('SELECT finding_id,revision FROM analysis_dismissals WHERE finding_id=ANY(%s)',([item['finding_id'] for item in findings],))}
        captured_revisions={identity:dismissals[identity]['revision'] for identity in revisions if identity in dismissals}
        if revisions!=captured_revisions:
            self._database.execute('UPDATE analysis_runs SET requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s',(run_id,run['revision']))
            return False
        return True

    def reopen_findings(self,run_id,automatic_reopens):
        for finding_id,dismissal_revision,supporting_games in automatic_reopens:
            changed=self._database.execute("UPDATE analysis_dismissals SET state='open',revision=revision+1,supporting_games=%s WHERE finding_id=%s AND revision=%s AND state='dismissed' RETURNING finding_id",(Jsonb(supporting_games),finding_id,dismissal_revision)).fetchone()
            if changed:
                self._database.execute("UPDATE analysis_runs r SET requested_generation=requested_generation+1 FROM analysis_heads h WHERE r.run_id=h.run_id AND r.revision=h.revision AND r.run_id<>%s AND EXISTS(SELECT 1 FROM analysis_report_items i WHERE i.run_id=r.run_id AND i.revision=r.revision AND i.generation=r.published_generation AND i.finding_id=%s)",(run_id,finding_id))

    def save_report(self,run_id,run,generation,metadata):
        self._database.execute("INSERT INTO analysis_reports VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(run_id,run["revision"],generation,Jsonb(metadata)))

    def publish_generation(self,run_id,run,generation,complete,state):
        self._database.execute("UPDATE analysis_runs SET published_generation=%s,last_complete_generation=CASE WHEN %s THEN %s ELSE last_complete_generation END,state=CASE WHEN state IN ('cancel_requested','cancelled') THEN state ELSE %s END WHERE run_id=%s AND revision=%s",(generation,complete,generation,state,run_id,run["revision"]))

    def next_run(self):
        return self._database.execute("SELECT r.run_id FROM analysis_runs r JOIN analysis_heads h USING(run_id,revision) WHERE r.state<>'staging' AND (r.payload->>'derivation_version'='2' OR r.published_generation IS NOT NULL) AND r.requested_generation IS DISTINCT FROM r.published_generation ORDER BY r.created_at,r.run_id LIMIT 1").fetchone()
