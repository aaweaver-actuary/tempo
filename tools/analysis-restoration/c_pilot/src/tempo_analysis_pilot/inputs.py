"""Independent consumer of A analysis-inputs v1, checkpoint cae8280d+.

Only serialized public exports enter this adapter. No database or service imports.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import chess
from tempo_intelligence import analysis_v1
Manifest,Page=analysis_v1.Manifest,analysis_v1.Page

def canonical_json(value):return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
def digest(value):return "sha256:"+hashlib.sha256(canonical_json(value).encode()).hexdigest()

KINDS={'definitions','routes','legal_replies','games','positions','comparisons','deviations'}

def fingerprint(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()

def read_json(path,maximum):
    with Path(path).open('rb') as source:raw=source.read(maximum+1)
    if len(raw)>maximum:raise ValueError('A_export_byte_bound')
    return json.loads(raw)

def normalize(directory,*,workspace,selected_game_ids):
    directory=Path(directory).resolve();original=read_json(directory/'manifest.json',1024*1024)
    if original.get('kind')!='tempo-analysis-inputs' or type(original.get('schema_version')) is not int or original['schema_version']!=1 or original['workspace_id']!=workspace:
        raise ValueError('unsupported_A_input_identity')
    unsigned={k:v for k,v in original.items() if k!='manifest_digest'}
    if fingerprint(unsigned)!=original['manifest_digest']:raise ValueError('A_manifest_digest_mismatch')
    records={kind:[] for kind in KINDS};seen=set();total=0
    for reference in original['pages']:
        kind,index=reference['kind'],reference['page_index']
        if kind not in KINDS or type(index) is not int or index<0 or (kind,index) in seen:raise ValueError('A_page_identity_mismatch')
        seen.add((kind,index));path=(directory/reference['filename']).resolve()
        if path.parent!=directory:raise ValueError('A_page_path_escape')
        document=read_json(path,8*1024*1024);total+=path.stat().st_size
        if total>64*1024*1024:raise ValueError('A_export_byte_bound')
        if (document['schema_version'],document['workspace_id'],document['input_id'],document['revision'],document['kind'],document['page_index'])!=(1,workspace,original['input_id'],original['revision'],kind,index) or len(document['items'])>64 or len(document['items'])!=reference['count'] or fingerprint(document)!=reference['digest']:
            raise ValueError('A_page_digest_or_identity_mismatch')
        records[kind]+=document['items']
    if {kind:len(values) for kind,values in records.items()}!=original['counts']:raise ValueError('A_export_count_mismatch')
    ids=[g['game_id'] for g in records['games']]
    if not selected_game_ids or len(set(selected_game_ids))!=len(selected_game_ids) or set(ids)!=set(selected_game_ids) or len(set(ids))!=len(ids):raise ValueError('explicit_A_game_scope_required')
    if type(original['revision']) is not int or type(original['complete']) is not bool:raise ValueError('unsupported_A_revision_or_completeness')
    source=original['source'];publication=original['publication'];activation=original.get('activation') or {}
    activated=activation.get('status')=='activated' and (activation.get('workspace_id'),activation.get('publication_id'),activation.get('publication_generation'))==(workspace,publication['publication_id'],publication['publication_generation'])
    if source['source_revision']!=publication['source_revision']:raise ValueError('A_source_revision_mismatch')
    serving=original.get('current_serving') or {};current=activated and original['readiness']=='ready' and serving.get('status')=='active' and (serving.get('workspace_id'),serving.get('publication_id'),serving.get('publication_generation'),serving.get('activation_revision'))==(workspace,publication['publication_id'],publication['publication_generation'],activation.get('activation_revision'))
    if activated and not isinstance(activation.get('activation_revision'),int):raise ValueError('A_activation_revision_missing')
    lineage={'source_id':source['source_id'],'source_revision':source['source_revision'],'source_digest':source['source_digest'],'publication_id':publication['publication_id'],'publication_generation':publication['publication_generation'],'activation_id':activation.get('activation_id') if activated else None,'activation_revision':activation.get('activation_revision') if activated else None,'activation_status':'activated' if activated else 'unknown','serving_status':'current' if current else 'historical' if activated else 'unknown'}
    selections={}
    routes={(r['route_id'],r['policy_id'],r['repertoire_id']):r for r in records['routes']}
    if len(routes)!=len(records['routes']):raise ValueError('duplicate_A_route_policy_identity')
    for route in records['routes']:
        selection={'repertoire_id':route['repertoire_id'],'policy_id':route['policy_id'],'learner_color':route['learner_color'],'lineage':lineage}
        selections[(route['repertoire_id'],route['policy_id'],route['learner_color'])]=selection
    comparisons={r['game']['game_id']:r for r in records['comparisons']};observations=defaultdict(dict)
    for position in records['positions']:
        if position['ply'] in observations[position['game_id']]:raise ValueError('duplicate_A_position')
        observations[position['game_id']][position['ply']]=position
    games=[]
    for game in records['games']:
        report=comparisons.get(game['game_id'])
        if not report or report['game']['revision']!=game['revision']:raise ValueError('A_comparison_revision_mismatch')
        observed=observations[game['game_id']]
        if len(observed)!=len(game['moves'])+1:raise ValueError('A_position_inventory_incomplete')
        board=chess.Board(game['starting_fen'])
        for ply in range(len(game['moves'])+1):
            position=observed[ply]
            if position['game_revision']!=game['revision'] or position['fen']!=board.fen(en_passant='fen') or position['move']!=(game['moves'][ply] if ply<len(game['moves']) else None):raise ValueError('A_position_history_mismatch')
            if ply<len(game['moves']):board.push_uci(game['moves'][ply])
        encounters=[]
        for position in observed.values():
            fact=position.get('opponent_decision')
            if not fact:continue
            if fact['game_id']!=game['game_id'] or fact['game_revision']!=game['revision'] or fact['fen']!=position['fen'] or fact['history']!=game['moves'][:fact['decision_ply']]:raise ValueError('A_opponent_encounter_identity_mismatch')
            groups=defaultdict(list)
            for context in fact['route_contexts']:groups[(context['repertoire_id'],context['policy_id'])].append(context)
            for (repertoire,policy),contexts in groups.items():
                encounters.append({'ply':fact['ply'],'repertoire_id':repertoire,'policy_id':policy,'fen':fact['fen'],'route_ids':sorted({context['route_id'] for context in contexts}),'scope_complete':bool(current and fact['response_inventory']['complete'] and fact['status'] in {'covered_opponent_reply','established_opponent_deviation'}),'scope_revision':fact['response_inventory']['source_revision'],'evidence_id':digest(fact)})
        contexts=[]
        delivery=report.get('delivery') or {'status':'unknown','reason':'delivery_not_exported'}
        for finding in report['findings']:
            memberships=finding['memberships']
            supported=[s for s in selections.values() if s['policy_id']==finding['policy_id'] and s['learner_color']==game['observed_color'] and any(m['repertoire_id']==s['repertoire_id'] for m in memberships)]
            targets=supported or [s for s in selections.values() if s['learner_color']==game['observed_color']]
            for selected in targets:
                selection=selected if supported else None
                contexts.append({'ply':finding['ply'],'repertoire_id':selected['repertoire_id'],'policy_id':selected['policy_id'],'status':finding['status'] if selection else 'unknown','reason':finding['explanation'],'card_id':finding['card_id'] if selection else None,'decision_id':finding['decision_id'] if selection else None,'expected_moves':finding['expected_moves'] if selection else [],'route_ids':[m['route_id'] for m in memberships],'coverage':'covered' if selection and finding['status'] in {'matched','missed'} else 'unknown','route_established':selection is not None,'evidence_id':finding['encounter_id'],'delivery_status':delivery['status'],'delivery':delivery})
        for deviation in (d for d in records['deviations'] if d['game_id']==game['game_id']):
            if deviation['decision_ply']>=len(game['moves']):continue
            if deviation['game_revision']!=game['revision'] or deviation['history']!=game['moves'][:deviation['decision_ply']] or deviation['decision_fen']!=observed[deviation['decision_ply']]['fen']:raise ValueError('A_deviation_identity_mismatch')
            groups=defaultdict(list)
            for route_context in deviation['route_contexts']:groups[(route_context['repertoire_id'],route_context['policy_id'])].append(route_context)
            for (repertoire,policy),route_contexts in groups.items():
                valid=activated and current and deviation['status']=='established_opponent_deviation' and deviation['response_inventory']['complete'] and deviation['response_inventory']['accepted_responses']==[]
                anchor=None
                if len(route_contexts)==1:
                    context=route_contexts[0];route=routes[(context['route_id'],policy,repertoire)]
                    anchor={k:route[k] for k in ('repertoire_key','policy_key','route_key')};anchor.update(source_key=source['source_key'],anchor_ply=context['route_ply'])
                contexts.append({'ply':deviation['decision_ply'],'repertoire_id':repertoire,'policy_id':policy,'coverage':'uncovered' if valid else 'unknown','route_established':valid,'scope_complete':valid,'scope_revision':deviation['response_inventory']['source_revision'],'opponent_ply':deviation['ply'],'opponent_move':deviation['opponent_move'],'route_ids':[c['route_id'] for c in route_contexts],'reentry_authorized':bool(deviation['reentry_evidence']),'reason':deviation['reason'],'evidence_id':digest(deviation),'proposal_anchor':anchor})
        games.append({'game_id':game['game_id'],'learner_id':game.get('player'),'provider':game.get('provider'),'provider_game_id':game.get('provider_game_id'),'source_revision':game['revision'],'source_digest':game['source_digest'],'learner_color':game['observed_color'],'starting_fen':game['starting_fen'],'moves':game['moves'],'played_at':game['played_at'],'opponent_encounters_complete':bool(original['complete'] and current and all('opponent_decision' in position for position in observed.values())),'opponent_encounters':encounters,'contexts':contexts})
    scopes=[]
    for inventory in records['legal_replies']:
        for scope in inventory.get('scopes',[]):
            board=chess.Board(scope['starting_fen'])
            for move in scope['history']:board.push_uci(move)
            if ' '.join(board.fen(en_passant='legal').split()[:4])!=inventory['position_key']:raise ValueError('A_reply_scope_position_mismatch')
            legal={move.uci() for move in board.legal_moves};replies=scope['replies']
            if len({reply['move_uci'] for reply in replies})!=len(replies) or {reply['move_uci'] for reply in replies}!=legal:raise ValueError('A_reply_scope_legal_inventory_mismatch')
            complete=bool(activated and current and original['complete'] and inventory['complete'] and scope['complete'] and all(reply['status'] in {'covered','uncovered'} for reply in replies))
            scopes.append({'repertoire_id':scope['repertoire_id'],'policy_id':scope['policy_id'],'learner_color':scope['learner_color'],'fen':board.fen(en_passant='fen'),'complete':complete,'revision':scope['source_revision'],'covered_replies':sorted(reply['move_uci'] for reply in replies if reply['status']=='covered'),'legal_moves':inventory['moves'],'evidence_id':'sha256:'+scope['digest']})
    pages=[];batch=[]
    def flush():
        if batch:
            pages.append(Page.model_validate({'schema_version':1,'input_id':original['input_id'],'revision':original['revision'],'page_id':'games-'+str(len(pages)),'games':list(batch)}).model_dump(mode='json'));batch.clear()
    for game in games:
        if batch and (len(batch)>=64 or len(canonical_json(batch+[game]).encode())>900000):flush()
        batch.append(game)
    flush()
    manifest=Manifest.model_validate({'schema_version':1,'workspace_id':workspace,'input_id':original['input_id'],'revision':original['revision'],'producer':'a-analysis-export','producer_version':'tempo-analysis-inputs-v1','source_artifact_digest':'sha256:'+original['manifest_digest'],'completeness':'complete' if original['complete'] else 'partial','reasons':([] if current else ['current_activation_unknown'])+([] if original['complete'] else ['A_export_partial']),'game_ids':ids,'selections':list(selections.values()),'coverage_scopes':scopes,'producer_manifest':original,'pages':[{'page_id':p['page_id'],'digest':digest(p),'game_count':len(p['games'])} for p in pages]}).model_dump(mode='json')
    return manifest,pages
