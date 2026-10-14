#!/usr/bin/env python3
"""One-time conditional Maia ordering. Default operation is read-only planning."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.frozen_maia_plan import create_plan, source_fingerprint
from app.services.frozen_maia_math import validate_distribution, VERSION
from app.services.opening_segmentation import stable_key, opening_position_key
from app.services import frozen_opening_order as order
from app.database import read_connection, background_connection

TABLES = {'repertoires':'repertoires','cards':'cards','memberships':'repertoire_cards','lines':'repertoire_lines',
          'graphs':'opening_graph_steps','publications':'opening_graph_publications','reviews':'reviews',
          'attempts':'opening_evidence_attempts','observations':'opening_evidence_observations',
          'presentations':'opening_evidence_presentations','origins':'queue_attempt_origins','queue':'daily_queue',
          'blocks':'repertoire_integrity_card_blocks','deleted':'deleted_cards','priorities':'current_repertoire_card_introduction_priorities',
          'generated':'current_repertoire_card_priority_generations','priority_publications':'repertoire_priority_publications',
          'gameplay':'current_gameplay_card_priorities','presented_teaching':'teaching_states'}


class DockerReadCursor:
    def __init__(self, rows):
        from app.postgres_store import TempoRow
        self.rows=[TempoRow(tuple(row.keys()),tuple(row.values())) for row in rows]
    def fetchall(self):return self.rows
    def fetchone(self):return self.rows[0] if self.rows else None


class DockerReadDatabase:
    """Explicit read-only bridge; database credentials remain inside the container."""
    def __init__(self, container):
        import re
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',container):raise ValueError('Invalid container name')
        self.container=container
    def execute_native(self, statement, parameters=()):
        from psycopg import sql
        chunks=statement.split('%s')
        if len(chunks)!=len(parameters)+1:raise ValueError('Read parameter mismatch')
        rendered=chunks[0]+''.join(sql.Literal(value).as_string()+chunk for value,chunk in zip(parameters,chunks[1:]))
        if not rendered.lstrip().upper().startswith(('SELECT','WITH')):raise ValueError('Docker bridge only permits read queries')
        query="BEGIN READ ONLY; SET LOCAL statement_timeout='3s'; SELECT row_to_json(frozen_read) FROM ("+rendered+") frozen_read; COMMIT;"
        result=subprocess.run(['docker','exec','-i',self.container,'sh','-c','exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -X -q -A -t -v ON_ERROR_STOP=1'],input=query,text=True,capture_output=True)
        if result.returncode:raise RuntimeError('Read-only container query failed; check target, schema, and PostgreSQL access')
        return DockerReadCursor([json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')])
    def execute(self, statement, parameters=()):
        from app.postgres_store import postgres_sql
        return self.execute_native(postgres_sql(statement),parameters)


@contextmanager
def docker_reader(container):
    yield DockerReadDatabase(container)


def _capture_once(reader_factory=read_connection):
    snapshot = {'cutoff': datetime.now(timezone.utc).isoformat()}
    with reader_factory() as database:
        snapshot['metadata'] = [dict(database.execute_native('SELECT (SELECT system_identifier::text FROM pg_control_system()) AS cluster_id,(SELECT oid::text FROM pg_database WHERE datname=current_database()) AS database_oid,current_database() AS database,current_schema() AS schema,(SELECT MAX(version) FROM tempo_schema_migrations) AS schema_version,(SELECT coverage_maia_elo FROM settings WHERE id=1) AS maia_elo,(SELECT study_only_mode FROM settings WHERE id=1) AS study_only_mode').fetchone())]
    for name, table in TABLES.items():
        with reader_factory() as database:
            key_rows=database.execute_native("""SELECT usage.column_name FROM information_schema.table_constraints constraint_row
                JOIN information_schema.key_column_usage usage USING(constraint_catalog,constraint_schema,constraint_name)
                WHERE constraint_row.table_schema=current_schema() AND constraint_row.table_name=%s
                  AND constraint_row.constraint_type='PRIMARY KEY' ORDER BY usage.ordinal_position""",(table,)).fetchall()
            if not key_rows:
                key_rows=database.execute_native('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position',(table,)).fetchall()
        if not key_rows: raise ValueError(f'Missing snapshot table {table}')
        key_columns=[row[0] for row in key_rows]
        ordering=','.join('"'+column+'"' for column in key_columns)
        filters=[]
        if name=='cards': filters.append("content_type='opening'")
        if name=='origins': filters.append("content_type='opening'")
        if name in ('reviews','queue','presented_teaching'):
            filters.append("card_id IN (SELECT id FROM cards WHERE content_type='opening')")
        if name=='graphs':filters.append('EXISTS(SELECT 1 FROM opening_graph_publications publication WHERE publication.repertoire_id=opening_graph_steps.repertoire_id AND publication.generation=opening_graph_steps.generation)')
        if name=='generated':filters.append('EXISTS(SELECT 1 FROM repertoire_priority_publications publication WHERE publication.repertoire_id=current_repertoire_card_priority_generations.repertoire_id AND publication.generation=current_repertoire_card_priority_generations.generation)')
        rows, last_key=[], None
        while True:
            cursor_filter=('('+ordering+')>('+','.join('?' for _ in key_columns)+')') if last_key else None
            predicates=[*filters,*([cursor_filter] if cursor_filter else [])]
            statement='SELECT row_to_json(snapshot_row)::text FROM (SELECT * FROM '+table+(' WHERE '+' AND '.join(predicates) if predicates else '')+' ORDER BY '+ordering+' LIMIT ?) snapshot_row'
            with reader_factory() as database:
                page=[json.loads(row[0]) for row in database.execute(statement,(*last_key,128) if last_key else (128,)).fetchall()]
            rows.extend(page)
            if len(page)<128:break
            last_key=tuple(page[-1][column] for column in key_columns)
            if any(value is None for value in last_key): raise ValueError(f'Nullable pagination key: {table}')
        snapshot[name]=rows
    with reader_factory() as database:
        snapshot['limits']=[dict(row) for row in database.execute('SELECT r.id,COALESCE(r.new_cards_per_day,s.new_cards_per_day) AS daily_limit FROM repertoires r CROSS JOIN settings s WHERE s.id=1').fetchall()]
    return snapshot


def capture(reader_factory=read_connection):
    # Reads close between every bounded page. A second complete pass fences
    # study/source changes without holding a long snapshot transaction open.
    first=_capture_once(reader_factory)
    confirmed=_capture_once(reader_factory)
    if stable_key({key:value for key,value in first.items() if key!='cutoff'}) != stable_key({key:value for key,value in confirmed.items() if key!='cutoff'}):
        raise ValueError('Incoherent capture: study or source inputs changed; retry when foreground work is quiet')
    return first


def cache_store(path, result):
    import tempfile
    import os
    descriptor,temporary=tempfile.mkstemp(prefix=path.stem+'-',suffix='.pending',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w') as handle:
            json.dump(result,handle,allow_nan=False)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


class Inference:
    def __init__(self, cache_directory, rating, foreground_url=None):
        self.rating = rating
        self.foreground_url = foreground_url
        self.next_gate_check = 0
        self.directory = Path(cache_directory);self.directory.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(['node', str(ROOT / 'scripts/frozen-maia-inference.mjs')],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        self.provenance = self.request({'operation':'identity'})['provenance']
        self.calls = 0
        self.memory = {}

    def request(self, request):
        self.process.stdin.write(json.dumps(request)+'\n');self.process.stdin.flush()
        reply = json.loads(self.process.stdout.readline())
        if reply.get('error'): raise RuntimeError(reply['error'])
        return reply

    def __call__(self, fen):
        effective_position = opening_position_key(fen)
        key = stable_key(VERSION,effective_position,self.rating,self.rating,self.provenance)
        legacy_key = stable_key(VERSION,fen,self.rating,self.rating,self.provenance)
        if key in self.memory: return self.memory[key]
        cached = self.directory / (key+'.json')
        legacy_cache = self.directory / (legacy_key+'.json')
        if cached.exists():
            result = json.loads(cached.read_text())
            if result.get('id') != key or result.get('provenance') != self.provenance: raise ValueError('Stale cache identity')
        elif legacy_cache.exists():
            result=json.loads(legacy_cache.read_text())
            if result.get('id') != legacy_key or result.get('provenance') != self.provenance: raise ValueError('Stale cache identity')
            result={**result,'id':key,'source_input_fen':fen,'effective_position':effective_position}
            cache_store(cached,result)
        else:
            if self.foreground_url and time.monotonic() >= self.next_gate_check:
                import urllib.request
                while True:
                    with urllib.request.urlopen(urllib.request.Request(self.foreground_url,headers={'X-Tempo-Work-Class':'background'}),timeout=5) as response:
                        busy=json.load(response)['active']
                    if not busy: break
                    time.sleep(.5)
                self.next_gate_check=time.monotonic()+1
            result = self.request({'id':key,'fen':fen,'elo':self.rating})
            if result.get('id') != key or result.get('provenance') != self.provenance: raise ValueError('Inference identity mismatch')
            validate_distribution(fen,result['moves'])
            result.update({'source_input_fen':fen,'effective_position':effective_position})
            cache_store(cached,result)
            self.calls += 1
            if self.calls % 100 == 0: print(f'Inferred {self.calls} unique positions',file=sys.stderr,flush=True)
            # Offline CPU work yields between positions and never runs in the live worker.
            time.sleep(.005)
        validate_distribution(fen,result['moves'])
        self.memory[key]=result['moves']
        return result['moves']

    def close(self):
        self.process.stdin.close();self.process.wait(timeout=30)


def export(plan, directory):
    directory = Path(directory);directory.mkdir(parents=True,exist_ok=True)
    (directory/'plan.json').write_text(json.dumps(plan,sort_keys=True,indent=2,allow_nan=False)+'\n')
    with (directory/'positions.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['scenario','canonical_fen','representative_full_fen','conditional_probability','anchor_fen','scenario_weight'])
        writer.writeheader()
        for scenario in plan['configuration']['scenarios']:
            evidence=plan['decision_evidence'].get(scenario['repertoire_id'],{})
            for position_key,probability in sorted(evidence.get('position_reach',{}).items()):
                writer.writerow({'scenario':scenario['name'],'canonical_fen':position_key,'representative_full_fen':evidence['position_fens'][position_key],'conditional_probability':probability,'anchor_fen':evidence['anchor_fen'],'scenario_weight':scenario['weight']})
    with (directory/'decision-reach.csv').open('w',newline='') as handle:
        fields=['scenario','decision_id','fen','expected_uci','trained_color','conditional_probability','scenario_weight','already_covered','within_assumed_prefix']
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        covered=set(plan['covered_decisions'])
        for scenario in plan['configuration']['scenarios']:
            evidence=plan['decision_evidence'].get(scenario['repertoire_id'],{})
            for identifier,probability in evidence.get('reach',{}).items():
                writer.writerow({'scenario':scenario['name'],'decision_id':identifier,**evidence['decisions'][identifier],'conditional_probability':probability,'scenario_weight':scenario['weight'],'already_covered':identifier in covered})
    fields = ['id','families','memberships','classification','status','reasons','eligible','blocking_reasons','start_fen','source_ref','source_fen','line_ids','trained_color','effective_trained_colors','fingerprint']
    rank_by_id = {row['id']:row for row in plan['ranks']}
    with (directory/'inventory.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=[*fields,'rank','marginal_score']);writer.writeheader()
        for row in plan['inventory']:
            values={field:row.get(field) for field in fields}
            values.update({key:rank_by_id.get(row['id'],{}).get(key) for key in ('rank','marginal_score')})
            writer.writerow({key:json.dumps(value) if isinstance(value,(list,dict)) else value for key,value in values.items()})
    preview={'allowances':plan['per_repertoire_allowances'], 'global':[{key:row[key] for key in ('id','rank','marginal_score','families','eligible','blocking_reasons')} for row in plan['ranks'][:100]],
             'families':{scenario['name']:[{key:row[key] for key in ('id','rank','marginal_score','eligible','blocking_reasons')} for row in plan['ranks'] if scenario['repertoire_id'] in row['families']][:10] for scenario in plan['configuration']['scenarios']}}
    preview['next_admissions_by_family']={scenario['name']:[{'id':row['id'],'rank':row['rank'],'quota_remaining':plan['per_repertoire_allowances'].get(scenario['repertoire_id'],{}).get('remaining')} for row in plan['ranks'] if scenario['repertoire_id'] in row['families'] and row['eligible'] and row['classification']=='not_admitted' and plan['per_repertoire_allowances'].get(scenario['repertoire_id'],{}).get('remaining',0)>0][:10] for scenario in plan['configuration']['scenarios']}
    for filename,status in (('excluded-cases.csv','excluded'),('unknown-cases.csv','unresolved')):
        with (directory/filename).open('w',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
            for row in plan['inventory']:
                if row['status']==status:writer.writerow({field:json.dumps(row.get(field)) if isinstance(row.get(field),(list,dict)) else row.get(field) for field in fields})
    with (directory/'ranks.csv').open('w',newline='') as handle:
        rank_fields=['rank','id','marginal_score','families','fingerprint','eligible','blocking_reasons']
        writer=csv.DictWriter(handle,fieldnames=rank_fields);writer.writeheader()
        for row in plan['ranks']:writer.writerow({field:json.dumps(row[field]) if isinstance(row[field],list) else row[field] for field in rank_fields})
    for filename,value in (('previous-order.json',plan['previous_order']),('diagnostics.json',{'unknown_count':plan['unknown_count'],'lineage':plan['lineage_diagnostics'],'unmapped_repertoires':plan['unmapped_repertoires']}),('checksums.json',{'plan':plan['checksum'],'source':plan['source_fingerprint'],'input':plan['input_checksum'],'provenance':plan['provenance'],'target':plan['target']})):
        (directory/filename).write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')
    preview['next_eligible']=[{key:row[key] for key in ('id','rank','families','classification')} for row in plan['ranks'] if row['eligible']][:50]
    (directory/'preview.json').write_text(json.dumps(preview,indent=2)+'\n')


def validate_plan(plan):
    body={key:value for key,value in plan.items() if key not in ('checksum','run_id')}
    if stable_key(body) != plan['checksum'] or plan['run_id'] != plan['checksum']: raise ValueError('Plan checksum mismatch')
    if not plan['complete']: raise ValueError('Plan incomplete; resolve UNKNOWN cases first')
    if plan['version'] != VERSION: raise ValueError('Incompatible reader contract')


def target_id(metadata):
    if not metadata.get('cluster_id') or not metadata.get('database_oid'):raise ValueError('Incomplete target identity; capture a fresh plan')
    return stable_key(metadata['cluster_id'],metadata['database_oid'],metadata['database'],metadata['schema'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['plan','apply','verify','rollback'],nargs='?',default='plan')
    parser.add_argument('--config',default=str(ROOT/'scripts/frozen-maia-order.json'))
    parser.add_argument('--output',default=str(ROOT/'test-results/frozen-maia-order'))
    parser.add_argument('--docker-container',help='Read-only plan bridge to the explicitly named PostgreSQL container')
    parser.add_argument('--snapshot-dir',help='Read an exported, opening-only snapshot; apply always rechecks the real target')
    parser.add_argument('--inventory-only',action='store_true',help='Export a blocked inventory without invoking Maia')
    parser.add_argument('--application-revision',help='Verified deployed revision captured alongside this snapshot')
    parser.add_argument('--reader-url',help='Deployed API base URL; required for apply compatibility proof')
    parser.add_argument('--foreground-url',default='http://127.0.0.1:3000/api/system/foreground-requests-active',help='Deployed foreground-requests-active URL to yield offline inference')
    parser.add_argument('--plan');parser.add_argument('--target');parser.add_argument('--run-id')
    args=parser.parse_args()
    if args.operation == 'plan':
        started=time.monotonic()
        if args.snapshot_dir:
            directory=Path(args.snapshot_dir)
            snapshot={path.stem:json.loads(path.read_text()) for path in directory.glob('*.json') if path.stem in {*TABLES,'metadata','limits'}}
            snapshot['cutoff']=datetime.fromtimestamp((directory/'metadata.json').stat().st_mtime,timezone.utc).isoformat()
        else:
            snapshot=capture((lambda:docker_reader(args.docker_container)) if args.docker_container else read_connection)
        config=json.loads(Path(args.config).read_text())
        metadata=snapshot['metadata'][0]
        if metadata['database'] != config['expected_database'] or metadata['schema'] != config['expected_schema']: raise ValueError('Unexpected database target')
        rating=metadata['maia_elo'] if config.get('maia_elo_override') is None else config['maia_elo_override']
        inference=None if args.inventory_only else Inference(Path(args.output)/'inference-cache',rating,args.foreground_url)
        try:
            from hashlib import sha256
            implementation_checksums={str(path.relative_to(ROOT)):sha256(path.read_bytes()).hexdigest() for path in (ROOT/'backend/app/services/frozen_maia_math.py',ROOT/'backend/app/services/frozen_maia_plan.py',Path(__file__))}
            plan=create_plan(snapshot,config,inference,{'rating_self':rating,'rating_opponent':rating,'model':inference.provenance if inference else None,'application_revision':args.application_revision,'implementation_checksums':implementation_checksums})
            export(plan,args.output)
            (Path(args.output)/'execution.json').write_text(json.dumps({'seconds':round(time.monotonic()-started,3),'new_inference_positions':inference.calls if inference else 0,'effective_inference_positions':len(inference.memory) if inference else 0,'run_id':plan['run_id']},indent=2)+'\n')
            print(json.dumps({'state':'planned' if plan['complete'] else 'blocked','run_id':plan['run_id'],'target':target_id(metadata),'inventory':len(plan['inventory']),'ranked':len(plan['ranks']),'unknown':plan['unknown_count'],'lineage_diagnostics':len(plan['lineage_diagnostics'])}))
        finally:
            if inference: inference.close()
        return
    if args.docker_container:raise ValueError('Docker bridge is read-only; writes require the existing owning database connection')
    if not args.target: raise ValueError('Explicit --target is required')
    with read_connection() as database:
        metadata=dict(database.execute_native('SELECT (SELECT system_identifier::text FROM pg_control_system()) AS cluster_id,(SELECT oid::text FROM pg_database WHERE datname=current_database()) AS database_oid,current_database() AS database,current_schema() AS schema').fetchone())
        if target_id(metadata) != args.target: raise ValueError('Target identity mismatch')
        if database.execute('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] != 48: raise ValueError('Reader migration/deployment required')
    from app.services.queue_application import request_queue_refresh_in_transaction
    from zoneinfo import ZoneInfo
    refresh_day=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    if args.operation == 'rollback':
        if not args.run_id: raise ValueError('--run-id required')
        with background_connection() as database:
            was_active=order.activation(database)[0]==args.run_id
            if was_active:request_queue_refresh_in_transaction(database,refresh_day)
            previous_run=order.rollback_run(database,args.run_id)
            restored=order.restore_queue_changes(database,args.run_id) if was_active else {}
        print(json.dumps({'state':'retired' if was_active else 'staging_aborted','active_run':previous_run,'study_preserved':True,**restored}));return
    if not args.plan: raise ValueError('--plan required')
    plan=json.loads(Path(args.plan).read_text());validate_plan(plan)
    if target_id(plan['target']) != args.target: raise ValueError('Plan belongs to another target')
    with read_connection() as database:
        current=order.activation(database)
    if current[0] == plan['run_id']:
        stale=[]
        for offset in range(0,len(plan['ranks']),16):
            with read_connection() as database:
                ranked=order.annotate(database,[{'card_id':row['id']} for row in plan['ranks'][offset:offset+16]])
            stale.extend(row['card_id'] for row in ranked if not row['frozen_valid'])
        with read_connection() as database:
            queued=[dict(row) for row in database.execute("SELECT q.card_id,q.gameplay_priority_reason,r.ordinal FROM daily_queue q JOIN frozen_opening_ranks r ON r.card_id=q.card_id AND r.run_id=? WHERE q.queue_date=? AND q.status='queued' ORDER BY q.position,q.id",(plan['run_id'],refresh_day)).fetchall()]
        unstarted=[]
        for row in queued:
            with read_connection() as database:
                if row['gameplay_priority_reason']!=order.MISS_REASON and not order.card_exposed(database,row['card_id']):
                    unstarted.append(row)
        consumed=[row['ordinal'] for row in unstarted]
        with read_connection() as database:
            projection=database.execute('SELECT state,refresh_pending FROM queue_projections WHERE queue_date=?',(refresh_day,)).fetchone()
        reader_confirmed=False
        if args.reader_url:
            import urllib.request
            with urllib.request.urlopen(args.reader_url.rstrip('/')+'/api/health',timeout=5) as response:
                health=json.load(response)
            reader_confirmed=health.get('frozen_opening_reader_version')==VERSION and health.get('frozen_opening_target_id')==args.target and health.get('frozen_opening_active_run')==plan['run_id']
        ready=bool(projection and projection[0]=='ready' and not projection[1])
        state=('active' if reader_confirmed else 'applied_reader_confirmation_pending') if ready and consumed==sorted(consumed) else 'applied_queue_refresh_pending'
        print(json.dumps({'state':state,'run_id':plan['run_id'],'stale_cards':stale,'reader_confirmed':reader_confirmed,'queued_unstarted':[row['card_id'] for row in unstarted]}));return
    if args.operation == 'verify':
        print(json.dumps({'state':'inactive','active_run':current[0],'planned_run':plan['run_id']}));return
    if not args.reader_url: raise ValueError('--reader-url required to prove the deployed reader consumes frozen ranks')
    import urllib.request
    with urllib.request.urlopen(args.reader_url.rstrip('/')+'/api/health',timeout=5) as response:
        health=json.load(response)
    if health.get('frozen_opening_reader_version') != VERSION: raise ValueError('Deployed reader lacks frozen ordering contract')
    if health.get('frozen_opening_target_id') != args.target: raise ValueError('Reader and writer point to different database instances')
    # Export exact previous metadata before staging any publication.
    backup=Path(args.plan).with_name('pre-apply-order.json')
    with read_connection() as database:
        previous={'activation':current,'queue':[dict(row) for row in database.execute('SELECT * FROM daily_queue ORDER BY id').fetchall()]}
    backup.write_text(json.dumps(previous,sort_keys=True,indent=2))
    with background_connection() as database:
        order.stage_run(database,plan,datetime.now(timezone.utc).isoformat())
    for offset in range(0,len(plan['ranks']),16):
        with background_connection() as database:
            order.stage_ranks(database,plan,plan['ranks'][offset:offset+16])
    # Recheck all candidate and source inputs; never infer or silently generate a replacement.
    fresh=capture()
    from app.services.frozen_maia_plan import inventory
    fresh_records={row['id']:row for row in inventory(fresh,plan['configuration'])}
    if source_fingerprint(fresh) != plan['source_fingerprint']: raise ValueError('Stale source/membership snapshot')
    for expected in plan['ranks']:
        actual=fresh_records.get(expected['id'])
        if not actual or actual['status'] != 'included' or actual['fingerprint'] != expected['fingerprint'] or actual['state'] != expected['state'] or actual['introduced_at'] != expected['introduced_at']:
            raise ValueError(f'Stale card or exposure: {expected["id"]}')
    with background_connection() as database:
        # Writer lock shares the admission fence; activating never admits or reorders rows.
        request_queue_refresh_in_transaction(database,refresh_day)
        database.execute_native('SELECT id FROM frozen_opening_activation WHERE id=1 FOR UPDATE')
        order.activate_run(database,plan)
    print('Override applied. Reader deployment and normal queue refresh must be verified separately.')


if __name__ == '__main__':
    try: main()
    except (ValueError,RuntimeError) as error:
        print(str(error),file=sys.stderr);sys.exit(2)
