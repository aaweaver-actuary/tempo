"""Shared additive health schema and SQLite compatibility triggers."""

from .background_metric_kinds import KINDS

TABLES = (
    "CREATE TABLE IF NOT EXISTS activity_health_metadata(id INTEGER PRIMARY KEY CHECK(id=1),workspace_id TEXT NOT NULL,collection_started_at TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS activity_work_progress(
      source TEXT NOT NULL,work_id TEXT NOT NULL,kind TEXT NOT NULL,generation_key TEXT NOT NULL,
      state TEXT NOT NULL,next_attempt_at TEXT,pending_since TEXT NOT NULL,manual_paused INTEGER NOT NULL DEFAULT 0,
      last_progress_at TEXT,progress_generation TEXT,progress_version INTEGER NOT NULL DEFAULT 0,
      admitted_seconds REAL NOT NULL DEFAULT 0,execution_id TEXT,execution_admitted_at TEXT,execution_expires_at TEXT,
      eligible_idle_seconds REAL NOT NULL DEFAULT 0,idle_observed_at TEXT,
      identical_timeouts INTEGER NOT NULL DEFAULT 0,timeout_signature TEXT,last_error_execution_id TEXT,
      eligible INTEGER,eligibility_checked_at TEXT,
      PRIMARY KEY(source,work_id))""",
    "CREATE INDEX IF NOT EXISTS activity_work_kind_state ON activity_work_progress(kind,state,manual_paused,pending_since,work_id)",
    "CREATE INDEX IF NOT EXISTS activity_work_eligibility ON activity_work_progress(kind,eligible,state,manual_paused,pending_since,work_id)",
    "CREATE INDEX IF NOT EXISTS activity_work_eligibility_refresh ON activity_work_progress(kind,(COALESCE(eligibility_checked_at,'')),work_id) WHERE state IN ('queued','retrying','leased','running') OR (state='failed' AND identical_timeouts>=5)",
    "CREATE INDEX IF NOT EXISTS activity_work_retry ON activity_work_progress(kind,manual_paused,next_attempt_at,work_id) WHERE state IN ('queued','retrying')",
    """CREATE TABLE IF NOT EXISTS activity_pipeline_health(kind TEXT PRIMARY KEY,checked_at TEXT,monitor_turn TEXT NOT NULL DEFAULT '',
      available INTEGER NOT NULL DEFAULT 0,health TEXT NOT NULL DEFAULT 'unknown',waiting_reason TEXT,
      bootstrap_cursor TEXT NOT NULL DEFAULT '',bootstrap_source INTEGER NOT NULL DEFAULT 0,bootstrap_ready INTEGER NOT NULL DEFAULT 0,
      source TEXT,work_id TEXT,diagnostics_json TEXT,diagnostics_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS activity_pipeline_monitor_turn ON activity_pipeline_health(monitor_turn,kind)",
    """CREATE TABLE IF NOT EXISTS activity_pipeline_counts(kind TEXT NOT NULL,source TEXT NOT NULL,state TEXT NOT NULL,
      shard INTEGER NOT NULL,count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(kind,source,state,shard))""",
    """CREATE TABLE IF NOT EXISTS activity_notification_incidents(id TEXT PRIMARY KEY,kind TEXT NOT NULL,
      source TEXT NOT NULL,work_id TEXT NOT NULL,generation_key TEXT NOT NULL,baseline_progress_version INTEGER NOT NULL,
      reason TEXT NOT NULL,opened_at TEXT NOT NULL,resolved_at TEXT,last_progress_at TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS activity_one_open_incident ON activity_notification_incidents(kind) WHERE resolved_at IS NULL",
    """CREATE TABLE IF NOT EXISTS activity_notification_changes(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
      incident_id TEXT NOT NULL REFERENCES activity_notification_incidents(id),event TEXT NOT NULL,
      occurred_at TEXT NOT NULL,UNIQUE(incident_id,event))""",
)

# Configuration contains identifiers and expressions owned by this module only.
WORK_SOURCES = (
    (
        "background_tasks",
        "durable",
        "id",
        "kind",
        "CAST(NEW.generation AS TEXT)",
        "state",
        "next_attempt_at",
        "COALESCE(NEW.pending_since,NEW.created_at)",
        "OLD.generation=NEW.generation AND OLD.state='leased' AND (NEW.state='complete' OR (NEW.state='queued' AND (OLD.phase!=NEW.phase OR OLD.payload_json!=NEW.payload_json)))",
    ),
    (
        "game_analysis_jobs",
        "game_analysis",
        "game_id",
        "'engine_game'",
        "CAST(NEW.analysis_version AS TEXT)||':'||CAST(NEW.analysis_evidence_version AS TEXT)",
        "status",
        "NULL",
        "COALESCE(NEW.pending_since,NEW.updated_at)",
        "OLD.analysis_version=NEW.analysis_version AND OLD.analysis_evidence_version=NEW.analysis_evidence_version AND OLD.status='leased' AND NEW.status='complete'",
    ),
    (
        "threat_analysis_requests",
        "threat_analysis",
        "id",
        "'engine_defense'",
        "'1'",
        "state",
        "NULL",
        "NEW.created_at",
        "OLD.state='leased' AND NEW.state='complete' AND NEW.report_json IS NOT NULL",
    ),
    (
        "repertoire_coverage_runs",
        "coverage",
        "id",
        "'coverage_explorer'",
        "NEW.id",
        "status",
        "NULL",
        "NEW.created_at",
        "NEW.completed_nodes>OLD.completed_nodes OR (OLD.status!='complete' AND NEW.status='complete')",
    ),
    (
        "game_sync_jobs",
        "sync",
        "id",
        "'game_sync_window'",
        "NEW.id",
        "status",
        "NULL",
        "NEW.created_at",
        "OLD.status!=NEW.status AND NEW.status='complete'",
    ),
    (
        "game_derivation_jobs",
        "derivation",
        "game_id",
        "'game_derivation_positions'",
        "CAST(NEW.derivation_version AS TEXT)",
        "status",
        "next_attempt_at",
        "NEW.updated_at",
        "OLD.derivation_version=NEW.derivation_version AND OLD.status!=NEW.status AND NEW.status='complete'",
    ),
)


def track_sql(source, work_id, kind, generation, state, retry, pending, progress, at):
    return f"""INSERT INTO activity_work_progress(source,work_id,kind,generation_key,state,next_attempt_at,pending_since,manual_paused,last_progress_at,progress_generation,progress_version)
    VALUES('{source}',{work_id},{kind},{generation},{state},{retry},{pending},COALESCE((SELECT paused FROM background_activity WHERE source='{source}' AND work_id={work_id}),0),
      CASE WHEN {progress} THEN {at} END,CASE WHEN {progress} THEN {generation} END,CASE WHEN {progress} THEN 1 ELSE 0 END)
    ON CONFLICT(source,work_id) DO UPDATE SET kind=excluded.kind,generation_key=excluded.generation_key,state=excluded.state,
      next_attempt_at=excluded.next_attempt_at,manual_paused=excluded.manual_paused,
      eligible=CASE WHEN excluded.state IN ('leased','running') THEN 1 WHEN activity_work_progress.generation_key!=excluded.generation_key OR activity_work_progress.state!=excluded.state THEN NULL ELSE activity_work_progress.eligible END,
      last_progress_at=CASE WHEN {progress} THEN {at} ELSE activity_work_progress.last_progress_at END,
      progress_generation=CASE WHEN {progress} THEN {generation} ELSE activity_work_progress.progress_generation END,
      progress_version=activity_work_progress.progress_version+CASE WHEN {progress} THEN 1 ELSE 0 END,
      admitted_seconds=CASE WHEN {progress} THEN 0 ELSE activity_work_progress.admitted_seconds END,
      eligible_idle_seconds=CASE WHEN {progress} THEN 0 ELSE activity_work_progress.eligible_idle_seconds END,
      idle_observed_at=CASE WHEN {progress} THEN NULL ELSE activity_work_progress.idle_observed_at END,
      identical_timeouts=CASE WHEN {progress} THEN 0 ELSE activity_work_progress.identical_timeouts END,
      timeout_signature=CASE WHEN {progress} THEN NULL ELSE activity_work_progress.timeout_signature END;"""


def install_sqlite(database):
    import uuid
    from datetime import datetime, timezone

    for statement in TABLES:
        database.execute(statement)
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        "INSERT OR IGNORE INTO activity_health_metadata VALUES(1,?,?)",
        (str(uuid.uuid4()), now),
    )
    for kind in sorted(KINDS):
        database.execute(
            "INSERT OR IGNORE INTO activity_pipeline_health(kind) VALUES(?)", (kind,)
        )
    for (
        table,
        source,
        identity,
        kind,
        generation,
        state,
        retry,
        pending,
        progress,
    ) in WORK_SOURCES:
        kind_expression = "NEW.kind" if kind == "kind" else kind
        retry_expression = "NULL" if retry == "NULL" else "NEW." + retry
        for event in ("INSERT", "UPDATE"):
            advancing = progress if event == "UPDATE" else "0"
            body = track_sql(
                source,
                "NEW." + identity,
                kind_expression,
                generation,
                "NEW." + state,
                retry_expression,
                pending,
                advancing,
                "NEW.updated_at",
            )
            if source in {"game_analysis", "threat_analysis"} and event == "UPDATE":
                body += f"UPDATE activity_work_progress SET admitted_seconds=admitted_seconds+CASE WHEN execution_id IS NOT NULL AND execution_admitted_at IS NOT NULL AND execution_expires_at IS NOT NULL THEN MAX(0,(JULIANDAY(MIN(NEW.updated_at,execution_expires_at))-JULIANDAY(MAX(execution_admitted_at,COALESCE(last_progress_at,execution_admitted_at))))*86400) ELSE 0 END,execution_id=NEW.lease_id,execution_admitted_at=NEW.updated_at,execution_expires_at=NEW.lease_expires_at WHERE source='{source}' AND work_id=NEW.{identity} AND NEW.{state}='leased' AND (OLD.{state}!='leased' OR COALESCE(OLD.lease_id,'')!=NEW.lease_id);"
            database.execute(
                f"CREATE TRIGGER IF NOT EXISTS activity_{source}_{event.lower()} AFTER {event} ON {table} BEGIN {body} END"
            )
        database.execute(
            f"CREATE TRIGGER IF NOT EXISTS activity_{source}_delete AFTER DELETE ON {table} BEGIN DELETE FROM activity_work_progress WHERE source='{source}' AND work_id=OLD.{identity}; END"
        )
    database.execute("""CREATE TRIGGER IF NOT EXISTS activity_control_health_update AFTER UPDATE OF paused ON background_activity BEGIN
      UPDATE activity_work_progress SET manual_paused=NEW.paused WHERE source=NEW.source AND work_id=NEW.work_id; END""")
    database.execute("""CREATE TRIGGER IF NOT EXISTS activity_control_health_insert AFTER INSERT ON background_activity BEGIN
      UPDATE activity_work_progress SET manual_paused=NEW.paused WHERE source=NEW.source AND work_id=NEW.work_id; END""")
    for event in ("INSERT", "UPDATE", "DELETE"):
        statements = []
        for record, delta in (
            [("OLD", -1), ("NEW", 1)]
            if event == "UPDATE"
            else [("NEW", 1)]
            if event == "INSERT"
            else [("OLD", -1)]
        ):
            classified = f"CASE WHEN {record}.manual_paused=1 AND {record}.state IN ('queued','retrying','leased','running') THEN 'manual:'||{record}.state ELSE {record}.state END"
            statements.append(
                f"INSERT INTO activity_pipeline_counts(kind,source,state,shard,count) VALUES({record}.kind,{record}.source,{classified},unicode(substr({record}.work_id,-1))%16,{delta}) ON CONFLICT(kind,source,state,shard) DO UPDATE SET count=count+excluded.count;"
            )
        database.execute(
            f"CREATE TRIGGER IF NOT EXISTS activity_count_{event.lower()} AFTER {event} ON activity_work_progress BEGIN {''.join(statements)} END"
        )

    database.execute("""CREATE TRIGGER IF NOT EXISTS activity_coverage_provider_progress AFTER UPDATE ON repertoire_coverage_nodes
      WHEN (OLD.explorer_status!='complete' AND NEW.explorer_status='complete') OR (OLD.maia_status!='complete' AND NEW.maia_status='complete') BEGIN
      UPDATE activity_work_progress SET last_progress_at=NEW.updated_at,progress_generation=generation_key,progress_version=progress_version+1,
        admitted_seconds=0,eligible_idle_seconds=0,idle_observed_at=NULL,identical_timeouts=0,timeout_signature=NULL
        WHERE source='coverage' AND work_id=NEW.run_id; END""")
