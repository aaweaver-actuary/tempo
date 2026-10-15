#!/usr/bin/env python3
"""One-time conditional Maia ordering. Default operation is read-only planning."""
from __future__ import annotations
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import csv
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.frozen_maia_plan import (create_plan, source_fingerprint, DEFAULT_DEFERRED_LIMIT,
                                         LOCALIZED_SCORING_GAPS, LINEAGE_UNCERTAINTY_POLICY)
from app.services.frozen_maia_math import validate_distribution, content_fingerprint, card_decisions, anchor, VERSION, READER_VERSION
from app.services.opening_segmentation import stable_key, opening_position_key
from app.services import frozen_opening_order as order
from app.database import read_connection, background_connection
from app.services.redis_admission_gate import BackgroundAdmissionDeferred

TABLES = {'repertoires':'repertoires','cards':'cards','memberships':'repertoire_cards','lines':'repertoire_lines',
          'graphs':'opening_graph_steps','publications':'opening_graph_publications','reviews':'reviews',
          'attempts':'opening_evidence_attempts','observations':'opening_evidence_observations',
          'presentations':'opening_evidence_presentations','origins':'queue_attempt_origins','queue':'daily_queue',
          'blocks':'repertoire_integrity_card_blocks','deleted':'deleted_cards','priorities':'current_repertoire_card_introduction_priorities',
          'generated':'current_repertoire_card_priority_generations','priority_publications':'repertoire_priority_publications',
          'gameplay':'current_gameplay_card_priorities','presented_teaching':'teaching_states'}
VIEW_KEY_SOURCES = {
    'current_repertoire_card_introduction_priorities': 'repertoire_card_introduction_priorities',
    'current_repertoire_card_priority_generations': 'repertoire_card_priority_generations',
    'current_gameplay_card_priorities': 'gameplay_card_priorities',
}


@contextmanager
def _ordering_write_connection(timeout_seconds=30.0):
    deadline=time.monotonic()+timeout_seconds
    wait_reported=False
    with ExitStack() as connections:
        while True:
            try:
                database=connections.enter_context(background_connection())
                break
            except BackgroundAdmissionDeferred:
                remaining_seconds=deadline-time.monotonic()
                if remaining_seconds<=0:raise
                if not wait_reported:
                    print('Waiting for foreground activity before the next write slice',file=sys.stderr,flush=True)
                    wait_reported=True
                time.sleep(min(.1,remaining_seconds))
        # The body is outside the retry loop: never replay a started write.
        yield database


def _capture_key_columns(database, table):
    key_source = VIEW_KEY_SOURCES.get(table, table)
    rows = database.execute_native("""SELECT a.attname
        FROM pg_catalog.pg_constraint AS pk
        JOIN pg_catalog.pg_class AS rel ON rel.oid = pk.conrelid
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = rel.relnamespace
        CROSS JOIN LATERAL unnest(pk.conkey) WITH ORDINALITY
            AS key_col(attnum, key_order)
        JOIN pg_catalog.pg_attribute AS a
          ON a.attrelid = rel.oid AND a.attnum = key_col.attnum
        WHERE pk.contype = 'p'
          AND ns.nspname = current_schema()
          AND rel.relname = %s
          AND rel.relkind IN ('r','p')
          AND NOT a.attisdropped
        ORDER BY key_col.key_order""", (key_source,)).fetchall()
    key_columns = [row[0] for row in rows]
    if not key_columns:
        raise ValueError(f'No stable primary key for capture relation {table} (key source {key_source})')
    if table in VIEW_KEY_SOURCES:
        projected = {row[0] for row in database.execute_native("""SELECT a.attname
            FROM pg_catalog.pg_class AS rel
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid=rel.relnamespace
            JOIN pg_catalog.pg_attribute AS a ON a.attrelid=rel.oid
            WHERE ns.nspname=current_schema() AND rel.relname=%s
              AND rel.relkind IN ('v','m') AND a.attnum>0 AND NOT a.attisdropped""", (table,)).fetchall()}
        missing = [column for column in key_columns if column not in projected]
        if missing:
            raise ValueError(f'Capture view {table} no longer projects its base primary key: {missing}')
    return key_columns


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
            key_columns = _capture_key_columns(database, table)
        ordering=','.join('"'+column+'"' for column in key_columns)
        previous_key_expressions=','.join('LAG("'+column+'") OVER capture_order' for column in key_columns)
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
            # Validate keys under PostgreSQL's native collation, preserving its
            # indexed seek order. Python string order can differ for text IDs.
            statement='SELECT row_to_json(snapshot_row)::text, CASE WHEN ROW_NUMBER() OVER capture_order=1 THEN TRUE ELSE ('+ordering+')>('+previous_key_expressions+') END AS capture_key_advances FROM (SELECT * FROM '+table+(' WHERE '+' AND '.join(predicates) if predicates else '')+' ORDER BY '+ordering+' LIMIT ?) snapshot_row WINDOW capture_order AS (ORDER BY '+ordering+') ORDER BY '+ordering
            with reader_factory() as database:
                captured_rows=database.execute(statement,(*last_key,128) if last_key else (128,)).fetchall()
            if any(row[1] is not True for row in captured_rows):
                raise ValueError(f'Repeated or nonadvancing pagination key for capture relation {table}')
            page=[json.loads(row[0]) for row in captured_rows]
            previous_key = last_key
            for row in page:
                current_key = tuple(row[column] for column in key_columns)
                if any(value is None for value in current_key):
                    raise ValueError(f'Null pagination key returned by capture relation {table}')
                if previous_key is not None and current_key == previous_key:
                    raise ValueError(f'Repeated or nonadvancing pagination key for capture relation {table}')
                previous_key = current_key
            rows.extend(page)
            if len(page)<128:break
            last_key=tuple(page[-1][column] for column in key_columns)
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
    fields = ['id','families','memberships','classification','status','final_disposition','scoring_status','deferred_reasons','diagnostic_refs','reasons','eligible','blocking_reasons','start_fen','source_ref','source_fen','line_ids','trained_color','effective_trained_colors','fingerprint']
    rank_by_id = {row['id']:row for row in plan['ranks']}
    with (directory/'inventory.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=[*fields,'rank','marginal_score']);writer.writeheader()
        for row in plan['inventory']:
            values={field:row.get(field) for field in fields}
            values.update({key:rank_by_id.get(row['id'],{}).get(key) for key in ('rank','marginal_score')})
            writer.writerow({key:json.dumps(value) if isinstance(value,(list,dict)) else value for key,value in values.items()})
    rank_fields=['rank','id','marginal_score','scoring_status','final_disposition','deferred_reasons','diagnostic_refs','families','fingerprint','eligible','blocking_reasons']
    preview={'allowances':plan['per_repertoire_allowances'], 'global':[{key:row.get(key) for key in ('id','rank','marginal_score','scoring_status','final_disposition','families','eligible','blocking_reasons')} for row in plan['ranks'][:100]],
             'families':{scenario['name']:[{key:row.get(key) for key in ('id','rank','marginal_score','scoring_status','final_disposition','eligible','blocking_reasons')} for row in plan['ranks'] if scenario['repertoire_id'] in row['families']][:10] for scenario in plan['configuration']['scenarios']}}
    preview['next_admissions_by_family']={scenario['name']:[{'id':row['id'],'rank':row['rank'],'quota_remaining':plan['per_repertoire_allowances'].get(scenario['repertoire_id'],{}).get('remaining')} for row in plan['ranks'] if scenario['repertoire_id'] in row['families'] and row['eligible'] and row['classification']=='not_admitted' and plan['per_repertoire_allowances'].get(scenario['repertoire_id'],{}).get('remaining',0)>0][:10] for scenario in plan['configuration']['scenarios']}
    for filename,status in (('excluded-cases.csv','excluded'),('unknown-cases.csv','unresolved')):
        with (directory/filename).open('w',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
            for row in plan['inventory']:
                if row['status']==status:writer.writerow({field:json.dumps(row.get(field)) if isinstance(row.get(field),(list,dict)) else row.get(field) for field in fields})
    with (directory/'ranks.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=rank_fields);writer.writeheader()
        for row in plan['ranks']:writer.writerow({field:json.dumps(row[field]) if isinstance(row.get(field),list) else row.get(field) for field in rank_fields})
    with (directory/'deferred-cards.csv').open('w',newline='') as handle:
        fields=['id','memberships','reason_codes','rank','scoring_status','marginal_score','diagnostic_refs']
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for row in plan['deferred_cards']:
            writer.writerow({field:json.dumps(row[field]) if isinstance(row.get(field),list) else row.get(field) for field in fields})
    diagnostics={'unknown_count':plan['unknown_count'],'lineage_diagnostic_count':plan['lineage_diagnostic_count'],
        'lineage_affected_card_ids':plan['lineage_affected_card_ids'],'scenario_failures':plan['scenario_failures'],
        'scored_count':plan['scored_count'],'deferred_count':plan['deferred_count'],'deferred_fraction':plan['deferred_fraction'],
        'deferred_limit':plan['deferred_limit'],'activation_safe':plan['activation_safe'],
        'summary':plan['diagnostic_summary'],
        'lineage':plan['lineage_diagnostics'],'unmapped_repertoires':plan['unmapped_repertoires']}
    for filename,value in (('previous-order.json',plan['previous_order']),('diagnostics.json',diagnostics),('checksums.json',{'plan':plan['checksum'],'source':plan['source_fingerprint'],'input':plan['input_checksum'],'provenance':plan['provenance'],'target':plan['target']})):
        (directory/filename).write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')
    preview['next_eligible']=[{key:row[key] for key in ('id','rank','families','classification')} for row in plan['ranks'] if row['eligible']][:50]
    (directory/'preview.json').write_text(json.dumps(preview,indent=2)+'\n')


def validate_plan(plan):
    body={key:value for key,value in plan.items() if key not in ('checksum','run_id')}
    if stable_key(body) != plan['checksum'] or plan['run_id'] != plan['checksum']: raise ValueError('Plan checksum mismatch')
    if plan['version'] != VERSION: raise ValueError('Incompatible reader contract')
    if plan.get('required_reader_version') != READER_VERSION:
        raise ValueError('Plan requires an incompatible or missing frozen-order reader capability')
    if plan.get('complete') != plan.get('probability_complete'):
        raise ValueError('Plan probability completeness is inconsistent')
    policy=plan.get('scoring_policy')
    if policy=='strict':
        if not plan['probability_complete']:
            raise ValueError('Plan incomplete; resolve UNKNOWN cases first')
    elif policy=='allow_unscored_tail':
        if not plan.get('activation_safe'):
            raise ValueError('Deferred-tail plan is not activation safe')
    else:
        raise ValueError('Unknown frozen-order scoring policy')

    target=plan.get('target') or {}
    if not target.get('cluster_id') or not target.get('database_oid') or not target.get('database') or not target.get('schema'):
        raise ValueError('Incomplete target identity; capture a fresh plan')
    if not plan.get('activation_safe'):
        raise ValueError('Plan is not activation safe')
    if target.get('database')!=plan.get('configuration',{}).get('expected_database') or target.get('schema')!=plan.get('configuration',{}).get('expected_schema'):
        raise ValueError('Plan target does not match its configuration')
    configured_scenarios=plan.get('configuration',{}).get('scenarios',[])
    scenarios={scenario['repertoire_id']:scenario for scenario in configured_scenarios}
    weights=[scenario.get('weight') for scenario in configured_scenarios]
    if (not scenarios or len(scenarios)!=len(configured_scenarios)
        or any(not isinstance(weight,(int,float)) or not math.isfinite(weight) or weight<0 for weight in weights)
        or abs(math.fsum(weights)-1)>1e-9
        or any(scenario.get('color') not in ('white','black') for scenario in configured_scenarios)):
        raise ValueError('Invalid scenario mapping or weights')
    for scenario in configured_scenarios:
        anchor(scenario)
    evidence=plan.get('decision_evidence',{})
    if set(evidence)!=set(scenarios) or any(result.get('error') or result.get('diagnostics')
        or not result.get('routes') or not result.get('reach') or not result.get('position_reach')
        for result in evidence.values()):
        raise ValueError('A required scenario has no effective scoring evidence')
    if plan.get('lineage_uncertainty_policy')!=LINEAGE_UNCERTAINTY_POLICY:
        raise ValueError('Obsolete lineage uncertainty policy; capture a fresh plan')
    provenance=plan.get('provenance') or {}
    model=provenance.get('model') or {}
    expected_model_files={'scripts/maia-inference.mjs','app/lib/all_moves_maia3.json','app/lib/all_moves_maia3_reversed.json',
        *(f'public/maia3/parts/part-{index:02d}' for index in range(6))}
    valid_digest=lambda value:isinstance(value,str) and len(value)==64 and all(character in '0123456789abcdef' for character in value.lower())
    if (not expected_model_files <= set(model.get('checksums',{}))
        or any(not valid_digest(model['checksums'][path]) for path in expected_model_files)
        or not model.get('runtime') or not model.get('node')):
        raise ValueError('Incomplete Maia model/runtime provenance')
    expected_implementation_files={'backend/app/services/frozen_maia_math.py','backend/app/services/frozen_maia_plan.py','scripts/frozen-maia-order.py'}
    implementation_checksums=provenance.get('implementation_checksums') or {}
    if not expected_implementation_files <= set(implementation_checksums) or any(not valid_digest(implementation_checksums[path]) for path in expected_implementation_files):
        raise ValueError('Incomplete scoring implementation provenance')
    rating_self=provenance.get('rating_self');rating_opponent=provenance.get('rating_opponent')
    configured_override=plan.get('configuration',{}).get('maia_elo_override')
    configured_rating=target.get('maia_elo') if configured_override is None else configured_override
    if (not isinstance(rating_self,(int,float)) or not isinstance(rating_opponent,(int,float))
        or not math.isfinite(rating_self) or not math.isfinite(rating_opponent)
        or rating_self<=0 or rating_opponent<=0 or rating_self!=configured_rating or rating_opponent!=configured_rating):
        raise ValueError('Incomplete scoring implementation/rating provenance')

    inventory=plan.get('inventory',[])
    by_id={row.get('id'):row for row in inventory}
    if len(by_id)!=len(inventory) or None in by_id:
        raise ValueError('Duplicate or missing physical card identity')
    allowed_dispositions={'scored','deferred','excluded','protected'}
    if any(row.get('final_disposition') not in allowed_dispositions for row in inventory):
        raise ValueError('Captured card has no safe final disposition')
    if any(row.get('final_disposition')=='excluded' and row.get('status')!='excluded' for row in inventory):
        raise ValueError('Excluded disposition does not match card lifecycle state')
    exclusion_reasons={'archived','superseded','deleted','pending_validation','unmapped','already_shown_attempted_reviewed'}
    for row in inventory:
        if row.get('final_disposition')!='excluded':
            continue
        reasons=row.get('reasons',[])
        if not reasons or any(reason not in exclusion_reasons and not reason.startswith('invalid_content:') for reason in reasons):
            raise ValueError('Unexplained exclusion of a captured card')
        if (('archived' in reasons and not row.get('archived'))
            or ('superseded' in reasons and not row.get('superseded_by'))
            or ('pending_validation' in reasons and not row.get('pending_validation'))
            or ('unmapped' in reasons and row.get('families'))
            or ('already_shown_attempted_reviewed' in reasons and row.get('classification')!='already_studied')):
            raise ValueError('Excluded disposition lacks its captured lifecycle/exposure evidence')
    if any(row.get('final_disposition')=='protected' and 'uncertain_legacy_exposure' not in row.get('reasons',[]) for row in inventory):
        raise ValueError('Protected disposition lacks its exposure reason')
    expected_probability_complete=not any(row.get('status')=='unresolved' for row in inventory) and not plan.get('lineage_diagnostics')
    if bool(plan.get('probability_complete'))!=expected_probability_complete:
        raise ValueError('Probability completeness does not match captured evidence')
    ranks=plan.get('ranks',[])
    rank_ids=[row.get('id') for row in ranks]
    ordinals=[row.get('rank') for row in ranks]
    if (len(set(rank_ids))!=len(rank_ids) or any(type(ordinal) is not int for ordinal in ordinals)
        or ordinals!=list(range(1,len(ranks)+1))):
        raise ValueError('Final ranking membership or ordinals are not unique and contiguous')
    if set(rank_ids)!={identifier for identifier,row in by_id.items() if row.get('final_disposition') in ('scored','deferred')}:
        raise ValueError('Final ranking omits a scored/deferred card or includes a protected card')
    if plan.get('target_candidate_count')!=len(ranks) or plan.get('scored_count')+plan.get('deferred_count')!=len(ranks):
        raise ValueError('Final ranking does not cover its declared safe target scope')
    seen_deferred=False
    deferred_rows=[]
    diagnostic_refs_by_card={identifier:sorted({item['line_id'] for item in plan.get('lineage_diagnostics',[])
        if identifier in item.get('affected_card_ids',[])}) for identifier in rank_ids}
    definition_fields=('revision','start_fen','moves_json','trained_color','source_ref','source_fen','kind','content_type')
    for row in ranks:
        inventory_row=by_id[row['id']]
        if (any(row.get(field)!=inventory_row.get(field) for field in
            (*definition_fields,'fingerprint','memberships','classification','status','reasons','baseline_order',
             'decisions','rank','scoring_status','final_disposition'))
            or content_fingerprint(inventory_row,inventory_row['memberships'])!=row.get('fingerprint')
            or inventory_row.get('classification') not in ('not_admitted','admitted_unstarted')
            or inventory_row.get('archived') or inventory_row.get('pending_validation') or inventory_row.get('superseded_by')
            or inventory_row.get('content_type')!='opening'):
            raise ValueError('Ranked card is unsafe or does not match its captured definition')
        families=set(inventory_row.get('families',[]))
        if families!=set(inventory_row['memberships']) & scenarios.keys() or not families:
            raise ValueError('Ranked card has an invalid scenario mapping')
        decisions=set()
        for family in families:
            if inventory_row.get('trained_color') not in (None,scenarios[family]['color']):
                raise ValueError('Ranked card has conflicting trained color')
            decisions.update(card_decisions(inventory_row,scenarios[family]))
        if decisions!=set(inventory_row.get('decisions',[])):
            raise ValueError('Ranked card decisions do not match its captured content')
        if row.get('final_disposition')=='deferred':
            seen_deferred=True
            deferred_rows.append(row)
            reasons=row.get('deferred_reasons') or []
            inventory_reasons=set(inventory_row.get('reasons',[]))
            if (policy!='allow_unscored_tail' or row.get('scoring_status')!='unscored_deferred'
                or row.get('marginal_score') is not None or inventory_row.get('classification') not in ('not_admitted','admitted_unstarted')
                or inventory_row.get('status')!='unresolved' or inventory_row.get('archived') or inventory_row.get('pending_validation')
                or inventory_row.get('superseded_by') or inventory_reasons & {'already_shown_attempted_reviewed','uncertain_legacy_exposure'}
                or any(reason.startswith('invalid_content:') for reason in inventory_reasons)
                or not reasons or inventory_reasons!=set(reasons)
                or any(reason not in LOCALIZED_SCORING_GAPS and not reason.startswith('missing_exact_card_lineage:') for reason in reasons)):
                raise ValueError('Deferred card is invalid, exposed, or has a non-local scoring failure')
            expected_diagnostics=sorted(set(inventory_row.get('diagnostic_refs',[])))
            if (row.get('diagnostic_refs')!=expected_diagnostics or expected_diagnostics!=diagnostic_refs_by_card[row['id']]
                or row.get('memberships')!=inventory_row.get('memberships')
                or row.get('deferred_reasons')!=inventory_row.get('deferred_reasons') or row.get('rank')!=inventory_row.get('rank')):
                raise ValueError('Deferred-card audit metadata does not match the immutable inventory')
        elif row.get('final_disposition')=='scored':
            score=row.get('marginal_score')
            if (seen_deferred or row.get('scoring_status')!='scored' or not isinstance(score,(int,float))
                or not math.isfinite(score) or score<0 or inventory_row.get('status')!='included'
                or inventory_row.get('reasons') or diagnostic_refs_by_card[row['id']]
                or any(not set(card_decisions(inventory_row,scenarios[family])) <= evidence[family]['reach'].keys() for family in families)):
                raise ValueError('Scored cards must precede the unscored tail and have a numerical score')
        else:
            raise ValueError('Unexpected final disposition in frozen ranks')
    if deferred_rows!=sorted(deferred_rows,key=lambda row:(tuple(row['baseline_order']),row['id'])):
        raise ValueError('Deferred tail does not follow deterministic baseline/card-ID order')
    if plan.get('deferred_count')!=sum(row.get('final_disposition')=='deferred' for row in ranks):
        raise ValueError('Deferred-card count does not match final ranks')
    deferred_by_id={row.get('id'):row for row in plan.get('deferred_cards',[])}
    if (len(plan.get('deferred_cards',[]))!=len(deferred_by_id) or len(deferred_by_id)!=plan.get('deferred_count')
        or set(deferred_by_id)!={row['id'] for row in ranks if row.get('final_disposition')=='deferred'}):
        raise ValueError('Deferred-card audit list does not match the final tail')
    for ranked_row in ranks:
        if ranked_row.get('final_disposition')!='deferred':
            continue
        audit=deferred_by_id[ranked_row['id']]
        if (audit.get('rank')!=ranked_row['rank'] or audit.get('memberships')!=ranked_row.get('memberships')
            or audit.get('reason_codes')!=ranked_row.get('deferred_reasons') or audit.get('diagnostic_refs')!=ranked_row.get('diagnostic_refs')
            or audit.get('scoring_status')!='unscored_deferred' or audit.get('marginal_score') is not None):
            raise ValueError('Deferred-card export metadata does not match the ranked tail')
    if plan.get('target_candidate_count')!=plan.get('scored_count')+plan.get('deferred_count'):
        raise ValueError('Target backlog denominator does not match scored and deferred cards')
    actual_fraction=len(deferred_rows)/len(ranks) if ranks else 0.0
    recorded_fraction=plan.get('deferred_fraction')
    if (not isinstance(recorded_fraction,(int,float)) or not math.isfinite(recorded_fraction)
        or not math.isclose(recorded_fraction,actual_fraction,rel_tol=0,abs_tol=1e-12)):
        raise ValueError('Deferred fraction does not match the ranked backlog')
    if actual_fraction>DEFAULT_DEFERRED_LIMIT or plan.get('deferred_limit')!=DEFAULT_DEFERRED_LIMIT:
        raise ValueError('Deferred-tail safety bound exceeded or changed')
    if plan.get('scenario_failures'):
        raise ValueError('A required scenario failed; incomplete scenario scoring cannot be deferred')
    if any(item.get('reason')!='Missing initial/anchor lineage' or item.get('uncertainty_error') for item in plan.get('lineage_diagnostics',[])):
        raise ValueError('Fatal or unexplained source-lineage diagnostics remain')
    if policy=='strict' and (plan.get('deferred_count') or not plan.get('probability_complete')):
        raise ValueError('Strict scoring policy cannot publish an incomplete probability plan')


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
    parser.add_argument('--allow-unscored-tail',action='store_true',help='Append only safely localized, valid scoring-unknown cards after scored ranks (1 percent maximum)')
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
            plan=create_plan(snapshot,config,inference,{'rating_self':rating,'rating_opponent':rating,'model':inference.provenance if inference else None,'application_revision':args.application_revision,'implementation_checksums':implementation_checksums},allow_unscored_tail=args.allow_unscored_tail)
            export(plan,args.output)
            (Path(args.output)/'execution.json').write_text(json.dumps({'seconds':round(time.monotonic()-started,3),'new_inference_positions':inference.calls if inference else 0,'effective_inference_positions':len(inference.memory) if inference else 0,'run_id':plan['run_id']},indent=2)+'\n')
            print(json.dumps({'state':'planned' if plan['activation_safe'] else 'blocked','run_id':plan['run_id'],'target':target_id(metadata),'inventory':len(plan['inventory']),'ranked':len(plan['ranks']),'scored':plan['scored_count'],'deferred':plan['deferred_count'],'deferred_fraction':plan['deferred_fraction'],'excluded':plan['excluded_count'],'protected':plan['protected_count'],'unknown':plan['unknown_count'],'probability_complete':plan['probability_complete'],'activation_safe':plan['activation_safe'],'lineage_diagnostics':plan['lineage_diagnostic_count'],'lineage_affected_cards':len(plan['lineage_affected_card_ids']),'scenarios_failed':len(plan['scenario_failures'])}))
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
        with _ordering_write_connection() as database:
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
            reader_confirmed=health.get('frozen_opening_reader_version')==plan['required_reader_version'] and health.get('frozen_opening_target_id')==args.target and health.get('frozen_opening_active_run')==plan['run_id']
        ready=bool(projection and projection[0]=='ready' and not projection[1])
        state=('active' if reader_confirmed else 'applied_reader_confirmation_pending') if ready and consumed==sorted(consumed) else 'applied_queue_refresh_pending'
        print(json.dumps({'state':state,'run_id':plan['run_id'],'stale_cards':stale,'reader_confirmed':reader_confirmed,'queued_unstarted':[row['card_id'] for row in unstarted]}));return
    if args.operation == 'verify':
        print(json.dumps({'state':'inactive','active_run':current[0],'planned_run':plan['run_id']}));return
    if not args.reader_url: raise ValueError('--reader-url required to prove the deployed reader consumes frozen ranks')
    import urllib.request
    with urllib.request.urlopen(args.reader_url.rstrip('/')+'/api/health',timeout=5) as response:
        health=json.load(response)
    if health.get('frozen_opening_reader_version') != plan['required_reader_version']: raise ValueError('Deployed reader lacks the required frozen ordering capability')
    if health.get('frozen_opening_target_id') != args.target: raise ValueError('Reader and writer point to different database instances')
    # Export exact previous metadata before staging any publication.
    backup=Path(args.plan).with_name('pre-apply-order.json')
    with read_connection() as database:
        previous={'activation':current,'queue':[dict(row) for row in database.execute('SELECT * FROM daily_queue ORDER BY id').fetchall()]}
    backup.write_text(json.dumps(previous,sort_keys=True,indent=2))
    with _ordering_write_connection() as database:
        order.stage_run(database,plan,datetime.now(timezone.utc).isoformat())
    for offset in range(0,len(plan['ranks']),16):
        with _ordering_write_connection() as database:
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
    with _ordering_write_connection() as database:
        # Writer lock shares the admission fence; activating never admits or reorders rows.
        request_queue_refresh_in_transaction(database,refresh_day)
        database.execute_native('SELECT id FROM frozen_opening_activation WHERE id=1 FOR UPDATE')
        order.activate_run(database,plan)
    print('Override applied. Reader deployment and normal queue refresh must be verified separately.')


if __name__ == '__main__':
    try: main()
    except (ValueError,RuntimeError) as error:
        print(str(error),file=sys.stderr);sys.exit(2)
