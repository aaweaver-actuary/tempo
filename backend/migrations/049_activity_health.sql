-- Additive progress/health records; historical failures are never retried.

CREATE TABLE IF NOT EXISTS activity_health_metadata(id INTEGER PRIMARY KEY CHECK(id=1),workspace_id TEXT NOT NULL,collection_started_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS activity_work_progress(
      source TEXT NOT NULL,work_id TEXT NOT NULL,kind TEXT NOT NULL,generation_key TEXT NOT NULL,
      state TEXT NOT NULL,next_attempt_at TEXT,pending_since TEXT NOT NULL,manual_paused INTEGER NOT NULL DEFAULT 0,
      last_progress_at TEXT,progress_generation TEXT,progress_version INTEGER NOT NULL DEFAULT 0,
      admitted_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,execution_id TEXT,execution_admitted_at TEXT,execution_expires_at TEXT,
      eligible_idle_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,idle_observed_at TEXT,
      identical_timeouts INTEGER NOT NULL DEFAULT 0,timeout_signature TEXT,last_error_execution_id TEXT,
      eligible INTEGER,eligibility_checked_at TEXT,
      PRIMARY KEY(source,work_id));

CREATE INDEX IF NOT EXISTS activity_work_kind_state ON activity_work_progress(kind,state,manual_paused,pending_since,work_id);

CREATE INDEX IF NOT EXISTS activity_work_eligibility ON activity_work_progress(kind,eligible,state,manual_paused,pending_since,work_id);
CREATE INDEX IF NOT EXISTS activity_work_eligibility_refresh ON activity_work_progress(kind,(COALESCE(eligibility_checked_at,'')),work_id) WHERE state IN ('queued','retrying','leased','running') OR (state='failed' AND identical_timeouts>=5);
CREATE INDEX IF NOT EXISTS activity_work_retry ON activity_work_progress(kind,manual_paused,next_attempt_at,work_id) WHERE state IN ('queued','retrying');

CREATE TABLE IF NOT EXISTS activity_pipeline_health(kind TEXT PRIMARY KEY,checked_at TEXT,monitor_turn TEXT NOT NULL DEFAULT '',
      available INTEGER NOT NULL DEFAULT 0,health TEXT NOT NULL DEFAULT 'unknown',waiting_reason TEXT,
      bootstrap_cursor TEXT NOT NULL DEFAULT '',bootstrap_source INTEGER NOT NULL DEFAULT 0,bootstrap_ready INTEGER NOT NULL DEFAULT 0,
      source TEXT,work_id TEXT,diagnostics_json TEXT,diagnostics_at TEXT);

CREATE INDEX IF NOT EXISTS activity_pipeline_monitor_turn ON activity_pipeline_health(monitor_turn,kind);

CREATE TABLE IF NOT EXISTS activity_pipeline_counts(kind TEXT NOT NULL,source TEXT NOT NULL,state TEXT NOT NULL,
      shard INTEGER NOT NULL,count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(kind,source,state,shard));

CREATE TABLE IF NOT EXISTS activity_notification_incidents(id TEXT PRIMARY KEY,kind TEXT NOT NULL,
      source TEXT NOT NULL,work_id TEXT NOT NULL,generation_key TEXT NOT NULL,baseline_progress_version INTEGER NOT NULL,
      reason TEXT NOT NULL,opened_at TEXT NOT NULL,resolved_at TEXT,last_progress_at TEXT);

CREATE UNIQUE INDEX IF NOT EXISTS activity_one_open_incident ON activity_notification_incidents(kind) WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS activity_notification_changes(sequence BIGSERIAL PRIMARY KEY,
      incident_id TEXT NOT NULL REFERENCES activity_notification_incidents(id),event TEXT NOT NULL,
      occurred_at TEXT NOT NULL,UNIQUE(incident_id,event));

INSERT INTO activity_health_metadata VALUES(1,gen_random_uuid()::text,to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00');

INSERT INTO activity_pipeline_health(kind) VALUES ('canonical_prefix_preview'),('coverage_explorer'),('coverage_seed'),('daily_queue'),('daily_statistics'),('defensive_admission'),('defensive_rubric_audit'),('defensive_threat_backfill'),('defensive_threat_report_audit'),('defensive_threat_scan'),('defensive_threat_validate'),('discovery_admission'),('discovery_recommendation'),('engine_defense'),('engine_game'),('game_analysis_followup'),('game_analysis_publish'),('game_derivation_compare'),('game_derivation_events'),('game_derivation_features'),('game_derivation_findings'),('game_derivation_misses'),('game_derivation_positions'),('game_derivation_priorities'),('game_sync_record'),('game_sync_window'),('integrity_scan'),('opening_graph_rebuild'),('opening_segmentation'),('other'),('prefix_transition_application'),('priority_retention'),('repertoire_game_refresh'),('repertoire_opportunity'),('repertoire_priority');

CREATE INDEX activity_task_bootstrap_kind_id ON background_tasks(kind,id);

CREATE FUNCTION activity_durable_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE advancing BOOLEAN:=FALSE;
BEGIN
 IF TG_OP='DELETE' THEN DELETE FROM activity_work_progress WHERE source='durable' AND work_id=OLD.id; RETURN OLD; END IF;
 IF TG_OP='UPDATE' THEN advancing:=(OLD.generation=NEW.generation AND OLD.state='leased' AND (NEW.state='complete' OR (NEW.state='queued' AND (OLD.phase!=NEW.phase OR OLD.payload_json!=NEW.payload_json)))); END IF;
 INSERT INTO activity_work_progress(source,work_id,kind,generation_key,state,next_attempt_at,pending_since,manual_paused,last_progress_at,progress_generation,progress_version)
    VALUES('durable',NEW.id,NEW.kind,CAST(NEW.generation AS TEXT),NEW.state,NEW.next_attempt_at,COALESCE(NEW.pending_since,NEW.created_at),COALESCE((SELECT paused FROM background_activity WHERE source='durable' AND work_id=NEW.id),0),
      CASE WHEN advancing THEN NEW.updated_at END,CASE WHEN advancing THEN CAST(NEW.generation AS TEXT) END,CASE WHEN advancing THEN 1 ELSE 0 END)
    ON CONFLICT(source,work_id) DO UPDATE SET kind=excluded.kind,generation_key=excluded.generation_key,state=excluded.state,
      next_attempt_at=excluded.next_attempt_at,manual_paused=excluded.manual_paused,
      eligible=CASE WHEN excluded.state IN ('leased','running') THEN 1 WHEN activity_work_progress.generation_key!=excluded.generation_key OR activity_work_progress.state!=excluded.state THEN NULL ELSE activity_work_progress.eligible END,
      last_progress_at=CASE WHEN advancing THEN NEW.updated_at ELSE activity_work_progress.last_progress_at END,
      progress_generation=CASE WHEN advancing THEN CAST(NEW.generation AS TEXT) ELSE activity_work_progress.progress_generation END,
      progress_version=activity_work_progress.progress_version+CASE WHEN advancing THEN 1 ELSE 0 END,
      admitted_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.admitted_seconds END,
      eligible_idle_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.eligible_idle_seconds END,
      idle_observed_at=CASE WHEN advancing THEN NULL ELSE activity_work_progress.idle_observed_at END,
      identical_timeouts=CASE WHEN advancing THEN 0 ELSE activity_work_progress.identical_timeouts END,
      timeout_signature=CASE WHEN advancing THEN NULL ELSE activity_work_progress.timeout_signature END;
 RETURN NEW;
END; $$;
CREATE TRIGGER activity_durable_change AFTER INSERT OR UPDATE OR DELETE ON background_tasks FOR EACH ROW EXECUTE FUNCTION activity_durable_change();

CREATE FUNCTION activity_game_analysis_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE advancing BOOLEAN:=FALSE;
BEGIN
 IF TG_OP='DELETE' THEN DELETE FROM activity_work_progress WHERE source='game_analysis' AND work_id=OLD.game_id; RETURN OLD; END IF;
 IF TG_OP='UPDATE' THEN advancing:=(OLD.analysis_version=NEW.analysis_version AND OLD.analysis_evidence_version=NEW.analysis_evidence_version AND OLD.status='leased' AND NEW.status='complete'); END IF;
 INSERT INTO activity_work_progress(source,work_id,kind,generation_key,state,next_attempt_at,pending_since,manual_paused,last_progress_at,progress_generation,progress_version)
    VALUES('game_analysis',NEW.game_id,'engine_game',CAST(NEW.analysis_version AS TEXT)||':'||CAST(NEW.analysis_evidence_version AS TEXT),NEW.status,NULL,COALESCE(NEW.pending_since,NEW.updated_at),COALESCE((SELECT paused FROM background_activity WHERE source='game_analysis' AND work_id=NEW.game_id),0),
      CASE WHEN advancing THEN NEW.updated_at END,CASE WHEN advancing THEN CAST(NEW.analysis_version AS TEXT)||':'||CAST(NEW.analysis_evidence_version AS TEXT) END,CASE WHEN advancing THEN 1 ELSE 0 END)
    ON CONFLICT(source,work_id) DO UPDATE SET kind=excluded.kind,generation_key=excluded.generation_key,state=excluded.state,
      next_attempt_at=excluded.next_attempt_at,manual_paused=excluded.manual_paused,
      eligible=CASE WHEN excluded.state IN ('leased','running') THEN 1 WHEN activity_work_progress.generation_key!=excluded.generation_key OR activity_work_progress.state!=excluded.state THEN NULL ELSE activity_work_progress.eligible END,
      last_progress_at=CASE WHEN advancing THEN NEW.updated_at ELSE activity_work_progress.last_progress_at END,
      progress_generation=CASE WHEN advancing THEN CAST(NEW.analysis_version AS TEXT)||':'||CAST(NEW.analysis_evidence_version AS TEXT) ELSE activity_work_progress.progress_generation END,
      progress_version=activity_work_progress.progress_version+CASE WHEN advancing THEN 1 ELSE 0 END,
      admitted_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.admitted_seconds END,
      eligible_idle_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.eligible_idle_seconds END,
      idle_observed_at=CASE WHEN advancing THEN NULL ELSE activity_work_progress.idle_observed_at END,
      identical_timeouts=CASE WHEN advancing THEN 0 ELSE activity_work_progress.identical_timeouts END,
      timeout_signature=CASE WHEN advancing THEN NULL ELSE activity_work_progress.timeout_signature END;
 IF TG_OP='UPDATE' THEN IF NEW.status='leased' AND (OLD.status!='leased' OR COALESCE(OLD.lease_id,'')!=NEW.lease_id) THEN UPDATE activity_work_progress SET admitted_seconds=admitted_seconds+CASE WHEN execution_id IS NOT NULL AND execution_admitted_at IS NOT NULL AND execution_expires_at IS NOT NULL THEN GREATEST(0,EXTRACT(EPOCH FROM LEAST(NEW.updated_at::timestamptz,execution_expires_at::timestamptz)-GREATEST(execution_admitted_at::timestamptz,COALESCE(last_progress_at,execution_admitted_at)::timestamptz))) ELSE 0 END,execution_id=NEW.lease_id,execution_admitted_at=NEW.updated_at,execution_expires_at=NEW.lease_expires_at WHERE source='game_analysis' AND work_id=NEW.game_id; END IF; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER activity_game_analysis_change AFTER INSERT OR UPDATE OR DELETE ON game_analysis_jobs FOR EACH ROW EXECUTE FUNCTION activity_game_analysis_change();

CREATE FUNCTION activity_threat_analysis_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE advancing BOOLEAN:=FALSE;
BEGIN
 IF TG_OP='DELETE' THEN DELETE FROM activity_work_progress WHERE source='threat_analysis' AND work_id=OLD.id; RETURN OLD; END IF;
 IF TG_OP='UPDATE' THEN advancing:=(OLD.state='leased' AND NEW.state='complete' AND NEW.report_json IS NOT NULL); END IF;
 INSERT INTO activity_work_progress(source,work_id,kind,generation_key,state,next_attempt_at,pending_since,manual_paused,last_progress_at,progress_generation,progress_version)
    VALUES('threat_analysis',NEW.id,'engine_defense','1',NEW.state,NULL,NEW.created_at,COALESCE((SELECT paused FROM background_activity WHERE source='threat_analysis' AND work_id=NEW.id),0),
      CASE WHEN advancing THEN NEW.updated_at END,CASE WHEN advancing THEN '1' END,CASE WHEN advancing THEN 1 ELSE 0 END)
    ON CONFLICT(source,work_id) DO UPDATE SET kind=excluded.kind,generation_key=excluded.generation_key,state=excluded.state,
      next_attempt_at=excluded.next_attempt_at,manual_paused=excluded.manual_paused,
      eligible=CASE WHEN excluded.state IN ('leased','running') THEN 1 WHEN activity_work_progress.generation_key!=excluded.generation_key OR activity_work_progress.state!=excluded.state THEN NULL ELSE activity_work_progress.eligible END,
      last_progress_at=CASE WHEN advancing THEN NEW.updated_at ELSE activity_work_progress.last_progress_at END,
      progress_generation=CASE WHEN advancing THEN '1' ELSE activity_work_progress.progress_generation END,
      progress_version=activity_work_progress.progress_version+CASE WHEN advancing THEN 1 ELSE 0 END,
      admitted_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.admitted_seconds END,
      eligible_idle_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.eligible_idle_seconds END,
      idle_observed_at=CASE WHEN advancing THEN NULL ELSE activity_work_progress.idle_observed_at END,
      identical_timeouts=CASE WHEN advancing THEN 0 ELSE activity_work_progress.identical_timeouts END,
      timeout_signature=CASE WHEN advancing THEN NULL ELSE activity_work_progress.timeout_signature END;
 IF TG_OP='UPDATE' THEN IF NEW.state='leased' AND (OLD.state!='leased' OR COALESCE(OLD.lease_id,'')!=NEW.lease_id) THEN UPDATE activity_work_progress SET admitted_seconds=admitted_seconds+CASE WHEN execution_id IS NOT NULL AND execution_admitted_at IS NOT NULL AND execution_expires_at IS NOT NULL THEN GREATEST(0,EXTRACT(EPOCH FROM LEAST(NEW.updated_at::timestamptz,execution_expires_at::timestamptz)-GREATEST(execution_admitted_at::timestamptz,COALESCE(last_progress_at,execution_admitted_at)::timestamptz))) ELSE 0 END,execution_id=NEW.lease_id,execution_admitted_at=NEW.updated_at,execution_expires_at=NEW.lease_expires_at WHERE source='threat_analysis' AND work_id=NEW.id; END IF; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER activity_threat_analysis_change AFTER INSERT OR UPDATE OR DELETE ON threat_analysis_requests FOR EACH ROW EXECUTE FUNCTION activity_threat_analysis_change();

CREATE FUNCTION activity_coverage_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE advancing BOOLEAN:=FALSE;
BEGIN
 IF TG_OP='DELETE' THEN DELETE FROM activity_work_progress WHERE source='coverage' AND work_id=OLD.id; RETURN OLD; END IF;
 IF TG_OP='UPDATE' THEN advancing:=(NEW.completed_nodes>OLD.completed_nodes OR (OLD.status!='complete' AND NEW.status='complete')); END IF;
 INSERT INTO activity_work_progress(source,work_id,kind,generation_key,state,next_attempt_at,pending_since,manual_paused,last_progress_at,progress_generation,progress_version)
    VALUES('coverage',NEW.id,'coverage_explorer',NEW.id,NEW.status,NULL,NEW.created_at,COALESCE((SELECT paused FROM background_activity WHERE source='coverage' AND work_id=NEW.id),0),
      CASE WHEN advancing THEN NEW.updated_at END,CASE WHEN advancing THEN NEW.id END,CASE WHEN advancing THEN 1 ELSE 0 END)
    ON CONFLICT(source,work_id) DO UPDATE SET kind=excluded.kind,generation_key=excluded.generation_key,state=excluded.state,
      next_attempt_at=excluded.next_attempt_at,manual_paused=excluded.manual_paused,
      eligible=CASE WHEN excluded.state IN ('leased','running') THEN 1 WHEN activity_work_progress.generation_key!=excluded.generation_key OR activity_work_progress.state!=excluded.state THEN NULL ELSE activity_work_progress.eligible END,
      last_progress_at=CASE WHEN advancing THEN NEW.updated_at ELSE activity_work_progress.last_progress_at END,
      progress_generation=CASE WHEN advancing THEN NEW.id ELSE activity_work_progress.progress_generation END,
      progress_version=activity_work_progress.progress_version+CASE WHEN advancing THEN 1 ELSE 0 END,
      admitted_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.admitted_seconds END,
      eligible_idle_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.eligible_idle_seconds END,
      idle_observed_at=CASE WHEN advancing THEN NULL ELSE activity_work_progress.idle_observed_at END,
      identical_timeouts=CASE WHEN advancing THEN 0 ELSE activity_work_progress.identical_timeouts END,
      timeout_signature=CASE WHEN advancing THEN NULL ELSE activity_work_progress.timeout_signature END;
 RETURN NEW;
END; $$;
CREATE TRIGGER activity_coverage_change AFTER INSERT OR UPDATE OR DELETE ON repertoire_coverage_runs FOR EACH ROW EXECUTE FUNCTION activity_coverage_change();

CREATE FUNCTION activity_sync_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE advancing BOOLEAN:=FALSE;
BEGIN
 IF TG_OP='DELETE' THEN DELETE FROM activity_work_progress WHERE source='sync' AND work_id=OLD.id; RETURN OLD; END IF;
 IF TG_OP='UPDATE' THEN advancing:=(OLD.status!=NEW.status AND NEW.status='complete'); END IF;
 INSERT INTO activity_work_progress(source,work_id,kind,generation_key,state,next_attempt_at,pending_since,manual_paused,last_progress_at,progress_generation,progress_version)
    VALUES('sync',NEW.id,'game_sync_window',NEW.id,NEW.status,NULL,NEW.created_at,COALESCE((SELECT paused FROM background_activity WHERE source='sync' AND work_id=NEW.id),0),
      CASE WHEN advancing THEN NEW.updated_at END,CASE WHEN advancing THEN NEW.id END,CASE WHEN advancing THEN 1 ELSE 0 END)
    ON CONFLICT(source,work_id) DO UPDATE SET kind=excluded.kind,generation_key=excluded.generation_key,state=excluded.state,
      next_attempt_at=excluded.next_attempt_at,manual_paused=excluded.manual_paused,
      eligible=CASE WHEN excluded.state IN ('leased','running') THEN 1 WHEN activity_work_progress.generation_key!=excluded.generation_key OR activity_work_progress.state!=excluded.state THEN NULL ELSE activity_work_progress.eligible END,
      last_progress_at=CASE WHEN advancing THEN NEW.updated_at ELSE activity_work_progress.last_progress_at END,
      progress_generation=CASE WHEN advancing THEN NEW.id ELSE activity_work_progress.progress_generation END,
      progress_version=activity_work_progress.progress_version+CASE WHEN advancing THEN 1 ELSE 0 END,
      admitted_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.admitted_seconds END,
      eligible_idle_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.eligible_idle_seconds END,
      idle_observed_at=CASE WHEN advancing THEN NULL ELSE activity_work_progress.idle_observed_at END,
      identical_timeouts=CASE WHEN advancing THEN 0 ELSE activity_work_progress.identical_timeouts END,
      timeout_signature=CASE WHEN advancing THEN NULL ELSE activity_work_progress.timeout_signature END;
 RETURN NEW;
END; $$;
CREATE TRIGGER activity_sync_change AFTER INSERT OR UPDATE OR DELETE ON game_sync_jobs FOR EACH ROW EXECUTE FUNCTION activity_sync_change();

CREATE FUNCTION activity_derivation_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE advancing BOOLEAN:=FALSE;
BEGIN
 IF TG_OP='DELETE' THEN DELETE FROM activity_work_progress WHERE source='derivation' AND work_id=OLD.game_id; RETURN OLD; END IF;
 IF TG_OP='UPDATE' THEN advancing:=(OLD.derivation_version=NEW.derivation_version AND OLD.status!=NEW.status AND NEW.status='complete'); END IF;
 INSERT INTO activity_work_progress(source,work_id,kind,generation_key,state,next_attempt_at,pending_since,manual_paused,last_progress_at,progress_generation,progress_version)
    VALUES('derivation',NEW.game_id,'game_derivation_positions',CAST(NEW.derivation_version AS TEXT),NEW.status,NEW.next_attempt_at,NEW.updated_at,COALESCE((SELECT paused FROM background_activity WHERE source='derivation' AND work_id=NEW.game_id),0),
      CASE WHEN advancing THEN NEW.updated_at END,CASE WHEN advancing THEN CAST(NEW.derivation_version AS TEXT) END,CASE WHEN advancing THEN 1 ELSE 0 END)
    ON CONFLICT(source,work_id) DO UPDATE SET kind=excluded.kind,generation_key=excluded.generation_key,state=excluded.state,
      next_attempt_at=excluded.next_attempt_at,manual_paused=excluded.manual_paused,
      eligible=CASE WHEN excluded.state IN ('leased','running') THEN 1 WHEN activity_work_progress.generation_key!=excluded.generation_key OR activity_work_progress.state!=excluded.state THEN NULL ELSE activity_work_progress.eligible END,
      last_progress_at=CASE WHEN advancing THEN NEW.updated_at ELSE activity_work_progress.last_progress_at END,
      progress_generation=CASE WHEN advancing THEN CAST(NEW.derivation_version AS TEXT) ELSE activity_work_progress.progress_generation END,
      progress_version=activity_work_progress.progress_version+CASE WHEN advancing THEN 1 ELSE 0 END,
      admitted_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.admitted_seconds END,
      eligible_idle_seconds=CASE WHEN advancing THEN 0 ELSE activity_work_progress.eligible_idle_seconds END,
      idle_observed_at=CASE WHEN advancing THEN NULL ELSE activity_work_progress.idle_observed_at END,
      identical_timeouts=CASE WHEN advancing THEN 0 ELSE activity_work_progress.identical_timeouts END,
      timeout_signature=CASE WHEN advancing THEN NULL ELSE activity_work_progress.timeout_signature END;
 RETURN NEW;
END; $$;
CREATE TRIGGER activity_derivation_change AFTER INSERT OR UPDATE OR DELETE ON game_derivation_jobs FOR EACH ROW EXECUTE FUNCTION activity_derivation_change();

CREATE FUNCTION activity_control_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN UPDATE activity_work_progress SET manual_paused=NEW.paused WHERE source=NEW.source AND work_id=NEW.work_id; RETURN NEW; END; $$;
CREATE TRIGGER activity_control_change AFTER INSERT OR UPDATE OF paused ON background_activity FOR EACH ROW EXECUTE FUNCTION activity_control_change();
CREATE FUNCTION activity_count_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE classification TEXT;
BEGIN
 IF TG_OP='UPDATE' AND OLD.kind=NEW.kind AND OLD.source=NEW.source AND OLD.state=NEW.state AND OLD.manual_paused=NEW.manual_paused THEN RETURN NEW; END IF;
 IF TG_OP!='INSERT' THEN
  classification:=CASE WHEN OLD.manual_paused=1 AND OLD.state IN ('queued','retrying','leased','running') THEN 'manual:'||OLD.state ELSE OLD.state END;
  INSERT INTO activity_pipeline_counts VALUES(OLD.kind,OLD.source,classification,ascii(right(OLD.work_id,1))%16,-1)
    ON CONFLICT(kind,source,state,shard) DO UPDATE SET count=activity_pipeline_counts.count+excluded.count;
 END IF;
 IF TG_OP!='DELETE' THEN
  classification:=CASE WHEN NEW.manual_paused=1 AND NEW.state IN ('queued','retrying','leased','running') THEN 'manual:'||NEW.state ELSE NEW.state END;
  INSERT INTO activity_pipeline_counts VALUES(NEW.kind,NEW.source,classification,ascii(right(NEW.work_id,1))%16,1)
    ON CONFLICT(kind,source,state,shard) DO UPDATE SET count=activity_pipeline_counts.count+excluded.count;
  RETURN NEW;
 END IF;
 RETURN OLD;
END; $$;
CREATE TRIGGER activity_count_change AFTER INSERT OR UPDATE OR DELETE ON activity_work_progress FOR EACH ROW EXECUTE FUNCTION activity_count_change();

CREATE INDEX activity_metric_kind_window ON background_metric_buckets(kind,bucket_start);
CREATE FUNCTION activity_coverage_provider_progress() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (OLD.explorer_status!='complete' AND NEW.explorer_status='complete') OR (OLD.maia_status!='complete' AND NEW.maia_status='complete') THEN
  UPDATE activity_work_progress SET last_progress_at=NEW.updated_at,progress_generation=generation_key,progress_version=progress_version+1,
    admitted_seconds=0,eligible_idle_seconds=0,idle_observed_at=NULL,identical_timeouts=0,timeout_signature=NULL WHERE source='coverage' AND work_id=NEW.run_id;
 END IF; RETURN NEW;
END; $$;
CREATE TRIGGER activity_coverage_provider_progress AFTER UPDATE ON repertoire_coverage_nodes FOR EACH ROW EXECUTE FUNCTION activity_coverage_provider_progress();
INSERT INTO tempo_schema_migrations(version) VALUES(49);
