"""Fixed run/command/user-state operations and atomic guards on the owner's write."""
from functools import partial

from psycopg.types.json import Jsonb
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import digest


class AnalysisRunOperations:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def receipt_row(self,command_id,payload):
        self._database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("analysis-command:"+command_id,))
        row=self._database.execute("SELECT * FROM analysis_commands WHERE command_id=%s",(command_id,)).fetchone()
        if row and row["digest"]!=digest(payload):
            raise DomainError("analysis_command_conflict")
        return row

    def write_receipt(self,command_id,payload,result):
        self._database.execute("INSERT INTO analysis_commands VALUES(%s,%s,%s)",(command_id,digest(payload),Jsonb(result)))

    @property
    def receipt(self):
        return partial(self._owner._receipt,self._database)

    @property
    def save_receipt(self):
        return partial(self._owner._save_receipt,self._database)

    def lock_run(self,value):
        self._database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("analysis:"+value.run_id,))

    def existing_run(self,value):
        return self._database.execute("SELECT * FROM analysis_runs WHERE run_id=%s AND revision=%s",(value.run_id,value.revision)).fetchone()

    def head_revision(self,value):
        return self._database.execute("SELECT revision FROM analysis_heads WHERE run_id=%s",(value.run_id,)).fetchone()

    def verify_submission(self,value,old,head,fingerprint):
        if old and old["digest"] != fingerprint():
            raise DomainError("analysis_run_conflict")
        if head and (not old or old["state"]=="staging") and (value.expected_revision != head["revision"] or value.revision<=head["revision"]):
            raise DomainError("stale_analysis_run")
        if not head and value.expected_revision is not None:
            raise DomainError("stale_analysis_run")

    def previous(self,value,head):
        return self._database.execute("SELECT requested_generation,last_complete_generation FROM analysis_runs WHERE run_id=%s AND revision=%s",(value.run_id,head["revision"])).fetchone()

    def create_run(self,value,payload,next_generation,previous,fingerprint):
        self._database.execute("INSERT INTO analysis_runs(run_id,revision,payload,digest,budget,requested_generation,last_complete_generation) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(value.run_id,value.revision,Jsonb(payload),fingerprint(),value.position_budget,next_generation,previous["last_complete_generation"] if previous else None))

    def position(self,value,ordinal,position_id,position,state):
        self._database.execute("INSERT INTO analysis_positions(run_id,revision,ordinal,position_id,payload,state) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(value.run_id,value.revision,ordinal,position_id,Jsonb(position),state()))

    def locked_head_revision(self,value):
        return self._database.execute("SELECT revision FROM analysis_heads WHERE run_id=%s FOR UPDATE",(value.run_id,)).fetchone()

    def newer_head(self,head,value):
        return head and head["revision"]>value.revision

    def promote(self,value):
        self._database.execute("UPDATE analysis_runs SET state='pending' WHERE run_id=%s AND revision=%s AND state='staging'",(value.run_id,value.revision))
        self._database.execute("INSERT INTO analysis_heads VALUES(%s,%s) ON CONFLICT(run_id) DO UPDATE SET revision=EXCLUDED.revision WHERE analysis_heads.revision<EXCLUDED.revision",(value.run_id,value.revision))

    @property
    def current_status(self):
        status=self._owner._status
        head=self._owner._head
        return lambda run_id:status(self._database,head(self._database,run_id))

    def head(self,run_id,lock):
        return self._owner._head(self._database,run_id,lock)

    def verify_command(self,run,command):
        if run["revision"]!=command.expected_revision:
            raise DomainError("stale_analysis_run")
        if run["state"]=="cancel_requested" and command.action!="cancel" or run["state"]=="cancelled" and command.action!="resume" and command.action!="cancel":
            raise DomainError("analysis_cancellation_incomplete")
        if command.action in {"continue","resume","retry"} and run["payload"].get("derivation_version",1)!=2:
            raise DomainError("legacy_analysis_revision_required")

    def extend_budget(self,command,run_id,run):
        self._database.execute("UPDATE analysis_runs SET budget=budget+%s WHERE run_id=%s AND revision=%s",(command.budget,run_id,run["revision"]))
        self._database.execute("UPDATE analysis_positions SET state='pending' WHERE run_id=%s AND revision=%s AND state='unbudgeted' AND ordinal<%s",(run_id,run["revision"],run["budget"]+command.budget))

    def retry_quality(self,run_id,run):
        self._database.execute("UPDATE analysis_positions SET state='pending',stage='root',accepted_root=NULL,accepted_played=NULL,attempts=0,next_attempt_at=NULL,error_code=NULL WHERE run_id=%s AND revision=%s AND state='complete' AND error_code='insufficient_quality'",(run_id,run["revision"]))

    def resume_failed(self,run_id,run):
        self._database.execute("UPDATE analysis_positions SET state='pending',attempts=0,next_attempt_at=NULL WHERE run_id=%s AND revision=%s AND state IN ('failed','cancelled') AND error_code IS DISTINCT FROM 'incompatible_engine_profile'",(run_id,run["revision"]))

    def queue_run(self,run_id,run):
        self._database.execute("UPDATE analysis_runs SET state='pending',requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))

    def reap_cancelled(self,run_id,run):
        self._database.execute("UPDATE analysis_positions SET state='cancelled',lease_token=NULL,leased_until=NULL WHERE run_id=%s AND revision=%s AND state='running' AND leased_until<=now()",(run_id,run["revision"]))

    def request_cancel(self,run_id,run):
        self._database.execute("UPDATE analysis_runs SET state='cancel_requested',requested_generation=requested_generation+1 WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))

    def cancel_pending(self,run_id,run):
        self._database.execute("UPDATE analysis_positions SET state='cancelled' WHERE run_id=%s AND revision=%s AND state IN ('pending','retry_wait')",(run_id,run["revision"]))

    def running(self,run_id,run):
        return self._database.execute("SELECT 1 FROM analysis_positions WHERE run_id=%s AND revision=%s AND state='running' LIMIT 1",(run_id,run["revision"])).fetchone()

    def finish_cancel(self,run_id,run):
        self._database.execute("UPDATE analysis_runs SET state='cancelled' WHERE run_id=%s AND revision=%s",(run_id,run["revision"]))

    def lock_user_state(self):
        self._database.execute("SELECT pg_advisory_xact_lock(hashtextextended('analysis-user-state',0))")

    def verify_dismissal(self,command,item,run,generation):
        if command.action not in {"dismiss","reopen"} or command.expected_revision!=item["finding_revision"] or run["published_generation"]!=generation:
            raise DomainError("stale_analysis_finding")

    def dismiss(self,finding_id,state,item,run_id):
        self._database.execute("INSERT INTO analysis_dismissals VALUES(%s,1,%s,%s) ON CONFLICT(finding_id) DO UPDATE SET revision=analysis_dismissals.revision+1,state=EXCLUDED.state,supporting_games=EXCLUDED.supporting_games",(finding_id,state,Jsonb(item["supporting_games"])))
        self._database.execute("UPDATE analysis_runs r SET requested_generation=requested_generation+1 FROM analysis_heads h WHERE r.run_id=h.run_id AND r.revision=h.revision AND (r.run_id=%s OR EXISTS(SELECT 1 FROM analysis_report_items i WHERE i.run_id=r.run_id AND i.revision=r.revision AND i.generation=r.published_generation AND i.finding_id=%s))",(run_id,finding_id))
