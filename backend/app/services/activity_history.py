"""Logical activity projection and durable visibility preferences; no job deletion."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import hmac
from fastapi import HTTPException
from ..database import read_connection
from .. import postgres_store
from .canonical_scope_freshness import latest_coverage_attempt_predicate
from .defensive_analysis import analysis_enabled, task_admission_sql, search_admission_sql, recommendation_sql

GROUPS = ('progressing','needs_attention','waiting','disabled','paused','finished','history')


def activity_timestamp(value: str) -> datetime:
    parsed=datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def snapshot_signature(cutoff: str, signing_key: str) -> str:
    return hmac.new(signing_key.encode(), cutoff.encode(), hashlib.sha256).hexdigest()


def clear_finished_in_transaction(database, payload):
    cutoff, signature = payload.get('completion_cutoff'), payload.get('completion_snapshot')
    if not isinstance(cutoff,str) or not isinstance(signature,str):
        raise HTTPException(422,'A displayed finished-work snapshot is required')
    lock = ' FOR UPDATE' if postgres_store.configured() else ''
    preferences = database.execute('SELECT cleared_through,signing_key FROM activity_history_preferences WHERE id=1'+lock).fetchone()
    if not preferences or not hmac.compare_digest(snapshot_signature(cutoff,preferences['signing_key']),signature):
        raise HTTPException(409,'The finished-work snapshot is invalid. Refresh activity and try again.')
    try:
        completed_at = datetime.fromisoformat(cutoff)
        if completed_at.tzinfo is None or completed_at > datetime.now(timezone.utc):
            raise ValueError()
    except ValueError:
        raise HTTPException(422,'The completion cutoff is invalid. Refresh activity and try again.') from None
    cleared = preferences['cleared_through']
    if cleared is None or datetime.fromisoformat(cleared) < completed_at:
        database.execute('UPDATE activity_history_preferences SET cleared_through=? WHERE id=1',(cutoff,))
    return {'ok':True,'cleared_through':cutoff if cleared is None or datetime.fromisoformat(cleared)<completed_at else cleared}


def native_stages(database):
    """One projection of indexed job identities; never aggregate raw engine history."""
    latest = latest_coverage_attempt_predicate(database,native=True)
    # PostgreSQL generations own the pipelines. Old SQLite/import-era job rows
    # are preserved below as history, with no claim about current execution.
    statement = f"""WITH stages AS (
      SELECT 'durable'::text source,t.id,t.kind title,
        CASE WHEN t.kind LIKE 'game_derivation_%%' THEN 'game:'||t.deduplication_key
             WHEN t.kind IN ('game_analysis_publish','game_analysis_followup') THEN 'game:'||COALESCE(t.payload_json::jsonb->>'game_id',t.deduplication_key)
             WHEN t.kind IN ('coverage_seed','coverage_explorer') THEN 'coverage:'||COALESCE(t.payload_json::jsonb->>'run_id',t.id)
             WHEN t.kind IN ('game_sync_record','game_sync_window') THEN 'sync:'||COALESCE(t.payload_json::jsonb->>'job_id',t.id)
             ELSE 'durable:'||t.id END logical_id,
        t.state,t.phase,t.generation::text generation_key,t.updated_at,t.completed_at,t.next_attempt_at,
        t.last_error error,NOT {task_admission_sql('t.kind')} settings_disabled,
        CASE WHEN t.kind LIKE 'game_derivation_%%' THEN NOT EXISTS(SELECT 1 FROM game_derivation_jobs j WHERE j.game_id=t.deduplication_key AND j.derivation_version::text=t.payload_json::jsonb->>'derivation_version')
             WHEN t.kind IN ('game_analysis_publish','game_analysis_followup') THEN NOT EXISTS(SELECT 1 FROM game_analysis_jobs j WHERE j.game_id=t.payload_json::jsonb->>'game_id' AND j.analysis_version::text=t.payload_json::jsonb->>'analysis_version')
             WHEN t.kind IN ('coverage_seed','coverage_explorer') THEN NOT EXISTS(SELECT 1 FROM repertoire_coverage_runs r WHERE r.id=t.payload_json::jsonb->>'run_id' AND {latest})
             WHEN t.kind='opening_graph_rebuild' THEN EXISTS(SELECT 1 FROM opening_graph_publications p WHERE p.repertoire_id=t.deduplication_key AND p.generation>t.generation)
             WHEN t.kind='integrity_scan' THEN EXISTS(SELECT 1 FROM opening_graph_publications p WHERE p.repertoire_id=t.deduplication_key AND p.generation::text!=t.payload_json::jsonb->>'graph_generation')
             ELSE t.state='superseded' END historical
      FROM background_tasks t
      UNION ALL SELECT 'sync',id,'Game sync','sync:'||id,status,status,id,updated_at,completed_at,NULL,error,FALSE,FALSE FROM game_sync_jobs
      UNION ALL SELECT 'derivation',j.game_id,initcap(g.provider)||' game '||substring(g.played_at,1,10)||' findings','game:'||j.game_id,
        j.status,COALESCE(j.phase,j.status),j.derivation_version::text,j.updated_at,CASE WHEN j.status='complete' THEN j.updated_at END,j.next_attempt_at,j.last_error,FALSE,FALSE
        FROM game_derivation_jobs j JOIN imported_games g ON g.id=j.game_id
      UNION ALL SELECT 'game_analysis',j.game_id,initcap(g.provider)||' game '||substring(g.played_at,1,10)||' analysis','game:'||j.game_id,
        j.status,j.status,j.analysis_version::text||':'||j.analysis_evidence_version::text,j.updated_at,CASE WHEN j.status='complete' THEN j.updated_at END,NULL,j.last_error,FALSE,FALSE
        FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id
      UNION ALL SELECT 'threat_analysis',request.id,CASE WHEN {recommendation_sql('request.id')} THEN 'Repertoire recommendation search' ELSE 'Defensive engine search' END,'threat_analysis:'||request.id,
        request.state,request.state,request.attempts::text,request.updated_at,CASE WHEN request.state='complete' THEN request.updated_at END,NULL,request.last_error,NOT {search_admission_sql('request.id')},FALSE FROM threat_analysis_requests request
      UNION ALL SELECT 'coverage',r.id,rep.name||' coverage','coverage:'||r.id,
        CASE WHEN r.status='failed' THEN 'failed' WHEN EXISTS(SELECT 1 FROM repertoire_coverage_nodes n WHERE n.run_id=r.id AND (n.maia_status='leased' OR n.explorer_status='running')) THEN 'running'
             WHEN EXISTS(SELECT 1 FROM repertoire_coverage_nodes n WHERE n.run_id=r.id AND (n.maia_status!='complete' OR n.explorer_status!='complete')) THEN 'queued' ELSE r.status END,
        'Checking positions',r.id,r.updated_at,CASE WHEN r.status='complete' AND NOT EXISTS(SELECT 1 FROM repertoire_coverage_nodes n WHERE n.run_id=r.id AND (n.maia_status!='complete' OR n.explorer_status!='complete')) THEN r.updated_at END,NULL,r.last_error,FALSE,NOT ({latest})
        FROM repertoire_coverage_runs r JOIN repertoires rep ON rep.id=r.repertoire_id
      UNION ALL SELECT 'integrity',j.repertoire_id,rep.name||' legacy integrity','legacy:integrity:'||j.repertoire_id,j.status,j.status,j.run_id,j.updated_at,CASE WHEN j.status='complete' THEN j.updated_at END,NULL,j.last_error,FALSE,TRUE
        FROM repertoire_integrity_jobs j JOIN repertoires rep ON rep.id=j.repertoire_id
      UNION ALL SELECT 'priority',j.repertoire_id,rep.name||' legacy priorities','legacy:priority:'||j.repertoire_id,j.status,j.status,j.generation::text,j.updated_at,CASE WHEN j.status='complete' THEN j.updated_at END,j.next_attempt_at,j.last_error,FALSE,TRUE
        FROM repertoire_priority_jobs j JOIN repertoires rep ON rep.id=j.repertoire_id
      UNION ALL SELECT 'statistics',local_day,'Legacy daily insights '||local_day,'legacy:statistics:'||local_day,status,status,local_day,updated_at,CASE WHEN status='complete' THEN updated_at END,NULL,last_error,FALSE,TRUE FROM daily_statistics_jobs
    ) SELECT stages.*,COALESCE(a.paused,0) manual_paused,COALESCE(a.promoted,0) promoted,
        CASE WHEN a.generation_key=stages.generation_key THEN a.completed_units END completed,
        CASE WHEN a.generation_key=stages.generation_key THEN a.total_units END total,
        CASE WHEN a.generation_key=stages.generation_key THEN a.phase END reported_phase
      FROM stages LEFT JOIN background_activity a ON a.source=stages.source AND a.work_id=stages.id"""
    rows=database.execute_native(statement).fetchall()
    return [dict(row) for row in rows]


def project_stages(stages, preferences, *, offset=0, limit=50, group='all'):
    now=datetime.now(timezone.utc)
    grouped=defaultdict(list)
    for stage in stages:
        stage=dict(stage)
        stage['manual_paused']=bool(stage.get('manual_paused',bool(stage.get('paused',False)) and not stage.get('paused_by_settings',False)))
        stage['paused_by_settings']=stage['state'] != 'complete' and bool(stage.get('settings_disabled',stage.get('paused_by_settings',False)))
        stage['paused']=stage['manual_paused'] or stage['paused_by_settings']
        stage['historical']=bool(stage.get('historical',False))
        stage['phase']=stage.get('reported_phase') or stage.get('phase') or stage['state']
        stage['title']=stage['title'].replace('_',' ').title() if stage['source']=='durable' else stage['title']
        stage['state']='running' if stage['state']=='leased' else stage['state']
        if stage['state']=='complete' and stage.get('total') is not None: stage['completed']=stage['total']
        stage['updated_at']=activity_timestamp(stage['updated_at']).isoformat()
        completion=stage.get('completed_at') or (stage['updated_at'] if stage['state']=='complete' else None)
        stage['completed_at']=activity_timestamp(completion).isoformat() if completion else None
        logical_id=stage.get('logical_id') or ('game:'+stage['id'] if stage['source'] in {'derivation','game_analysis'} else stage['source']+':'+stage['id'])
        # Obsolete stages cannot influence their current parent's classification.
        if stage['historical']: logical_id='history:'+logical_id+':'+stage['source']+':'+stage['id']
        grouped[logical_id].append(stage)
    items=[]
    for logical_id,members in grouped.items():
        failed=[stage for stage in members if stage['state']=='failed']
        active=[stage for stage in members if not stage['paused'] and stage['state'] in {'running','finalizing','pausing'}]
        pending=[stage for stage in members if not stage['paused'] and stage['state'] in {'queued','retrying'}]
        all_complete=all(stage['state']=='complete' for stage in members)
        completed_at=max((stage['completed_at'] for stage in members if stage['completed_at']),default=None) if all_complete else None
        archived=bool(all_complete and not any(stage['paused'] or stage['error'] for stage in members) and completed_at and preferences['cleared_through'] and activity_timestamp(completed_at)<=activity_timestamp(preferences['cleared_through']))
        classification=('history' if members[0]['historical'] or archived else 'needs_attention' if failed else 'disabled' if all(stage['paused_by_settings'] for stage in members) and not all_complete else 'progressing' if active else 'waiting' if pending else 'paused' if any(stage['paused'] for stage in members) else 'finished' if all_complete else 'history')
        leader=(failed or active or pending or members)[0]
        item={key:leader.get(key) for key in ('source','id','title','phase','generation_key','completed','total','error')}
        item.update(logical_id=logical_id,classification=classification,
                    state='complete' if all_complete else 'failed' if failed else 'running' if active else 'queued' if pending else 'paused' if classification in {'disabled','paused'} else leader['state'],
                    updated_at=max(stage['updated_at'] for stage in members),completed_at=completed_at,
                    paused=classification in {'disabled','paused'},paused_by_settings=classification=='disabled',
                    promoted=any(stage['promoted'] for stage in members),archived=archived,
                    last_progress_at=None,health='unknown',waiting_reason='settings_disabled' if classification=='disabled' else 'manual_pause' if classification=='paused' else 'retry_delay' if leader.get('next_attempt_at') and activity_timestamp(leader['next_attempt_at'])>now else 'eligible_queue' if pending else None,
                    stages=[{key:stage.get(key) for key in ('source','id','title','state','phase','completed','total','updated_at','error','paused','paused_by_settings','promoted')} for stage in members] if len(members)>1 else [],stage_count=len(members))
        if logical_id.startswith('game:'):
            parent=next((stage for stage in members if stage['source'] in {'derivation','game_analysis'}),leader)
            item['title']=parent['title']+' · '+logical_id[-8:]
        items.append(item)
    counts={name:sum(item['classification']==name for item in items) for name in GROUPS}
    counts['manual_paused']=counts['paused']
    counts.update(running=sum(item['classification']=='progressing' for item in items),queued=counts['waiting'],paused=counts['paused']+counts['disabled'],failed=counts['needs_attention'])
    cutoff=max((item['completed_at'] for item in items if item['classification']=='finished'),default=None)
    items.sort(key=lambda item:(GROUPS.index(item['classification']),not item['promoted'],item['updated_at'],item['logical_id']))
    selected=[item for item in items if group=='all' or item['classification']==group]
    return {'items':selected[offset:offset+limit],'counts':counts,'total':len(selected),'next_offset':offset+limit if offset+limit<len(selected) else None,
            'clearable_finished':counts['finished'],'completion_cutoff':cutoff,'completion_snapshot':snapshot_signature(cutoff,preferences['signing_key']) if cutoff else None,
            'cleared_through':preferences['cleared_through'],'generated_at':now.isoformat(),'available':True}


def native_activity(*, offset=0, limit=50, group='all'):
    if group!='all' and group not in GROUPS: raise HTTPException(422,'Unknown activity group')
    with read_connection() as database:
        analysis_enabled(database)
        preference_row=database.execute('SELECT cleared_through,signing_key FROM activity_history_preferences WHERE id=1').fetchone()
        if preference_row is None: raise HTTPException(503,'Activity history preferences are unavailable. Restore the database and retry.')
        preferences=dict(preference_row)
        stages=native_stages(database)
    return project_stages(stages,preferences,offset=max(0,offset),limit=max(1,min(limit,100)),group=group)
