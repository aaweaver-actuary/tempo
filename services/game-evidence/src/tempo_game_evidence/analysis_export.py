"""Immutable serialized analysis inputs. No foreign DB or service internals."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import chess
from psycopg.types.json import Jsonb
from tempo_chess_core import canonical_fen_key
from .snapshot import parse_snapshot, projected_definition
from .source import INDEX_VERSION
from .delivery import inspect_delivery

PAGE_SIZE = 64
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_EXPORT_BYTES = 64 * 1024 * 1024
EXPORT_DERIVATION_VERSION = 3
KINDS = ("definitions", "routes", "legal_replies", "games", "positions", "comparisons", "deviations")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def pack_pages(records,workspace,input_id):
    """Keep every item, bounded by both bytes and the existing 64-item ceiling."""
    pages=[]
    for kind in KINDS:
        page_index=0;batch=[];item_bytes=0
        def document(items):
            return {'schema_version':1,'input_id':input_id,'workspace_id':workspace,
                'revision':1,'kind':kind,'page_index':page_index,'items':items}
        def append_batch():
            value=document(batch)
            pages.append((kind,page_index,digest(value),value))
        for item in records[kind]:
            size=len(canonical(item).encode())
            required=1+len(canonical(document([])).encode())+item_bytes+size+len(batch)
            if batch and (len(batch)==PAGE_SIZE or required>MAX_PAGE_BYTES):
                append_batch();page_index+=1;batch=[];item_bytes=0
                required=1+len(canonical(document([])).encode())+size
            if required>MAX_PAGE_BYTES:
                raise ValueError('analysis_page_byte_bound')
            batch.append(item);item_bytes+=size
        if batch:append_batch()
    if sum(1+len(canonical(page[3]).encode()) for page in pages)>MAX_EXPORT_BYTES:
        raise ValueError('analysis_export_byte_bound')
    return pages


def applicable_inventories(inventories,position_keys):
    """Retain all scopes/replies for every encountered opponent position."""
    return [inventory for inventory in inventories if inventory['position_key'] in position_keys]


def deviations(game, positions, snapshot, *, all_opponent_decisions=False,compiled_routes=None):
    routes = [item for item in (snapshot.compiled_routes() if compiled_routes is None else compiled_routes) if item[0].learner_color == game["observed_color"]]
    active = {(index, 0) for index, (_, keys, _) in enumerate(routes) if positions and keys[0] == positions[0]["comparison_key"]}
    entry_evidence={item:[] for item in active}
    inventories = {item["position_key"]: item for item in snapshot.content_export["legal_replies"]}
    result = []
    for position in positions:
        if position["move"] is None:
            break
        candidates = set(active)
        for index, (_, keys, bindings) in enumerate(routes):
            for ply, binding in bindings.items():
                if binding.reentry and keys[ply] == position["comparison_key"]:
                    candidates.add((index, ply))
                    if (index,ply) not in active:
                        entry_evidence[(index,ply)]=[{'route_id':routes[index][0].route_id,'binding_ply':ply,'game_ply':position['ply'],'card_id':binding.card_id,'authorization':'explicit_reentry'}]
        candidates = {(index, ply) for index, ply in candidates if routes[index][1][ply] == position["comparison_key"]}
        if position["turn"] != game["observed_color"] and candidates:
            authored = {route.moves[ply] for index, ply in candidates for route in [routes[index][0]] if ply < len(route.moves)}
            # A route ending here is explicitly incomplete context, not proof of a gap.
            inventory = inventories.get(position["comparison_key"])
            complete = bool(snapshot.complete and snapshot.serving_current() and inventory and all(ply < len(routes[index][0].moves) for index, ply in candidates))
            is_deviation=position['move'] not in authored
            if is_deviation or all_opponent_decisions:
                board = chess.Board(position["fen"])
                board.push_uci(position["move"])
                contexts = [{"repertoire_id": routes[index][0].repertoire_id, "policy_id": routes[index][0].policy_id,
                    "route_id": routes[index][0].route_id, "route_ply": ply, "learner_color": game["observed_color"],
                    "entry": "explicit_reentry" if entry_evidence.get((index,ply)) else "continuous_route",
                    'reentry_evidence':entry_evidence.get((index,ply),[])}
                    for index, ply in sorted(candidates)]
                scopes=[scope for scope in (inventory or {}).get('scopes',[]) if any(scope['policy_id']==context['policy_id'] and scope['repertoire_id']==context['repertoire_id'] and any(node['route_id']==context['route_id'] and node['ply']==context['route_ply'] for node in scope['route_contexts']) for context in contexts)]
                selected=[reply for scope in scopes for reply in scope['replies'] if reply['move_uci']==position['move']]
                complete=bool(complete and scopes and all(scope['complete'] for scope in scopes) and all(reply['status']!='ambiguous' for reply in selected))
                # An uncovered ordered route can arrive at content already
                # authored under this policy. FEN equality cannot authorize
                # re-entry, and it cannot establish that new content is needed.
                existing_position_ids=[]
                if complete and is_deviation and not any(reply['responses'] for reply in selected):
                    policies={context['policy_id'] for context in contexts}
                    position_key=canonical_fen_key(board.fen(en_passant='legal'))
                    existing_position_ids={definition.card_id for definition in snapshot.publication.definitions
                        if definition.policy_id in policies and canonical_fen_key(chess.Board(definition.starting_fen).fen(en_passant='legal'))==position_key}
                    definitions_by_id={definition.card_id:definition for definition in snapshot.publication.definitions}
                    checked=set()
                    for route,_,bindings in routes:
                        if route.policy_id not in policies:continue
                        for binding in bindings.values():
                            if not binding.exercise_ply:continue
                            identity=(binding.card_id,binding.exercise_ply,binding.decision_id)
                            if identity in checked:continue
                            checked.add(identity)
                            projected=projected_definition(definitions_by_id[binding.card_id].model_dump(mode='json'),binding.model_dump(mode='json'))
                            if canonical_fen_key(chess.Board(projected['starting_fen']).fen(en_passant='legal'))==position_key:
                                existing_position_ids.add(binding.card_id)
                    existing_position_ids=sorted(existing_position_ids)
                    if existing_position_ids:
                        complete=False

                result.append({"game_id": game["game_id"], "game_revision": game["revision"], "ply": position["ply"],
                    "fen": position["fen"], "opponent_move": position["move"], "route_contexts": contexts,
                    "decision_ply": position["ply"] + 1, "decision_fen": board.fen(en_passant="fen"),
                    "history": game["moves"][:position["ply"] + 1], "authored_opponent_moves": sorted(authored),
                    "is_deviation":is_deviation,
                    "status": ("established_opponent_deviation" if is_deviation else 'covered_opponent_reply') if complete else "unknown", "reason": "existing_authored_position_reentry_unverified" if existing_position_ids else "complete_route_scope" if complete else "incomplete_or_unavailable_scope",
                    "response_inventory": {"complete": complete, "source_revision": snapshot.publication.source_revision,
                        "publication_generation": snapshot.publication.publication_generation,
                        "digest": snapshot.content_export["export_digest"], "accepted_responses": [response for reply in selected for response in reply['responses']],
                        "scope_digests":[scope['digest'] for scope in scopes],"selected_reply_states":[reply['status'] for reply in selected],**({'existing_position_content_ids':existing_position_ids} if existing_position_ids else {})},
                    "legal_reply_inventory": inventory, "reentry_evidence": [evidence for item in contexts for evidence in item['reentry_evidence']]})
        successors={(index,ply+1):entry_evidence.get((index,ply),[]) for index,ply in candidates if ply<len(routes[index][0].moves) and routes[index][0].moves[ply]==position['move']}
        active=set(successors)
        entry_evidence=successors
    return result


def create_export(store, workspace, game_ids=None):
    if game_ids is not None and (not game_ids or len(game_ids) > 100 or len(set(game_ids)) != len(game_ids)):
        raise ValueError("export_game_selection_bound")
    with store.connect() as database:
        database.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = database.execute("SELECT snapshot_digest FROM workspace_inputs WHERE workspace_id=%s", (workspace,)).fetchone()
        snapshot_row = database.execute("SELECT document FROM snapshots WHERE digest=%s", (row["snapshot_digest"],)).fetchone() if row else None
        if not snapshot_row:
            raise ValueError("analysis_snapshot_unavailable")
        rows = database.execute("SELECT game.*,comparison.report,comparison.input_token FROM games game LEFT JOIN comparisons comparison ON comparison.game_id=game.game_id AND comparison.revision=game.report_revision WHERE game.workspace_id=%s AND (%s::text[] IS NULL OR game.game_id=ANY(%s::text[])) ORDER BY game.game_id LIMIT 101", (workspace, game_ids, game_ids)).fetchall()
        if not rows or len(rows) > 100 or (game_ids is not None and len(rows) != len(game_ids)):
            raise ValueError("analysis_export_requires_1_to_100_exact_games")
        deliveries={}
        for game in rows:
            report=game['report']
            if report is None or report['game']['revision']!=game['revision'] or report.get('snapshot',{}).get('digest')!=row['snapshot_digest']:
                raise ValueError('analysis_comparison_pending_or_stale')
            if game['index_receipt']!={'input_digest':game['input_digest'],'version':INDEX_VERSION,
                    'position_count':len(game['document'].get('moves',[]))+1,'complete':True} or game['positions'] is None:
                raise ValueError('analysis_position_inventory_unavailable')
            deliveries[game['game_id']]=inspect_delivery(database,game['game_id'],game['report_revision'],game['reconciliation_status'],game['reconciliation_reason'])
    # The consistent source cut is now copied. Chess traversal, validation and
    # serialization must not hold the producer's read transaction open.
    snapshot=parse_snapshot(snapshot_row['document'])
    if snapshot.schema_version!=2:
        raise ValueError('analysis_export_requires_content_snapshot_v2')
    records={kind:[] for kind in KINDS}
    records['definitions']=[item.model_dump(mode='json') for item in snapshot.publication.definitions]
    records['routes']=snapshot.content_export['routes']
    revisions=[];opponent_positions=set();compiled_routes=snapshot.compiled_routes()
    for game in rows:
        positions=game['positions'];delivery=deliveries[game['game_id']]
        report={**game['report'],'delivery':{'inspection_source':'game_evidence_outbox','observed_comparison_revision':game['report_revision'],**delivery}}
        revisions.append([game['game_id'],game['revision'],game['report_revision'],game['input_digest'],game['input_token'],digest(delivery)])
        records['games'].append({**game['document'],'input_digest':game['input_digest'],'source_digest':game['source_digest'],'comparison_revision':game['report_revision']})
        opponent_positions.update(position['comparison_key'] for position in positions
            if position['move'] is not None and position['turn']!=game['document']['observed_color'])
        opponent_decisions=deviations(game['document'],positions,snapshot,all_opponent_decisions=True,compiled_routes=compiled_routes)
        by_ply={item['ply']:item for item in opponent_decisions}
        records['positions'].extend({**position,'game_id':game['game_id'],'game_revision':game['revision'],
            'comparison_revision':game['report_revision'],'opponent_decision':by_ply.get(position['ply'])} for position in positions)
        records['comparisons'].append(report)
        records['deviations'].extend(item for item in opponent_decisions if item['is_deviation'])
    records['legal_replies']=applicable_inventories(snapshot.content_export['legal_replies'],opponent_positions)
    input_id=digest([workspace,row['snapshot_digest'],revisions,EXPORT_DERIVATION_VERSION])
    pages=pack_pages(records,workspace,input_id)
    manifest = {"schema_version": 1, "kind": "tempo-analysis-inputs", "input_id": input_id, "revision": 1,
        "workspace_id": workspace, "source": snapshot.content_export["source"],
        "publication": {key: getattr(snapshot.publication,key) for key in ("publication_id", "publication_generation", "source_revision")},
        "publication_digest": snapshot.content_export["publication_digest"], "content_export_digest": snapshot.content_export["export_digest"],
        "snapshot": {"digest": row["snapshot_digest"], "snapshot_id": snapshot.snapshot_id, "revision": snapshot.revision},
        "activation": snapshot.activation.model_dump(mode="json"), "activation_origin": snapshot.activation_origin,
        'acceptance':snapshot.acceptance,
        "current_serving": snapshot.current_serving, "complete": snapshot.complete,
        "readiness": "ready" if snapshot.complete and snapshot.serving_current() else "unknown",
        "provenance": snapshot.content_export["provenance"], "counts": {kind: len(records[kind]) for kind in KINDS},
        "pages": [{"kind": kind, "page_index": index, "digest": fingerprint, "filename": f"{kind}-{index:06d}.json", "count": len(document["items"])} for kind,index,fingerprint,document in pages]}
    manifest["manifest_digest"] = digest(manifest)
    from .analysis_schema import AnalysisManifest,AnalysisPage
    AnalysisManifest.model_validate(manifest)
    for _,_,_,page in pages:
        AnalysisPage.model_validate(page)
    # The input identity is deterministic. Commit the manifest and pages atomically;
    # failed creation exposes no completed manifest or partial generation.
    with store.connect() as database:
        database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("analysis-export:" + input_id,))
        old = database.execute("SELECT manifest FROM analysis_exports WHERE input_id=%s", (input_id,)).fetchone()
        if old:
            if old["manifest"] != manifest:
                raise ValueError("immutable_analysis_export_conflict")
            return manifest
        database.execute("INSERT INTO analysis_exports VALUES(%s,%s,%s)", (input_id, workspace, Jsonb(manifest)))
        with database.cursor() as cursor:
            cursor.executemany("INSERT INTO analysis_export_pages VALUES(%s,%s,%s,%s,%s)", [(input_id, kind,index,fingerprint,Jsonb(document)) for kind,index,fingerprint,document in pages])
    return manifest


def export_page(store, workspace, input_id, kind, after=None):
    if kind not in KINDS:
        raise ValueError("unknown_analysis_page_kind")
    with store.connect() as database:
        row = database.execute("SELECT manifest FROM analysis_exports WHERE input_id=%s AND workspace_id=%s", (input_id,workspace)).fetchone()
        if row is None:
            raise ValueError("analysis_export_not_found")
        manifest = row["manifest"]
        expected = {"version": 1,"input_id": input_id,"workspace_id":workspace,"kind":kind,"revision":1,"manifest_digest":manifest["manifest_digest"]}
        index = 0
        if after is not None:
            cursor = json.loads(after)
            if set(cursor) != set(expected) | {"page_index"} or any(type(cursor[key]) is not type(value) or cursor[key] != value for key,value in expected.items()) or type(cursor["page_index"]) is not int or cursor["page_index"] < 0:
                raise ValueError("invalid_analysis_cursor")
            index = cursor["page_index"]
        page = database.execute("SELECT document,digest FROM analysis_export_pages WHERE input_id=%s AND kind=%s AND page_index=%s", (input_id,kind,index)).fetchone()
    references = [item for item in manifest["pages"] if item["kind"] == kind]
    if not references and index == 0:
        return {"input_id":input_id,"manifest_digest":manifest["manifest_digest"],"items":[],"next_cursor":None}
    if index>=len(references) or page is None or digest(page["document"]) != references[index]["digest"] or page["digest"] != references[index]["digest"]:
        raise ValueError("analysis_page_missing_or_corrupt")
    return {**page["document"], "digest":page["digest"],"manifest_digest":manifest["manifest_digest"],
        "next_cursor": canonical({**expected,"page_index":index+1}) if index+1<len(references) else None}


def create_directory_export(store, workspace, game_ids, directory):
    """An interrupted directory retains its first frozen input, even after edits."""
    marker = Path(directory) / '.tempo-analysis-export.json'
    if marker.exists():
        if marker.is_symlink() or marker.stat().st_size > 4096:
            raise ValueError('analysis_export_resume_marker_invalid')
        reservation = json.loads(marker.read_bytes())
        if set(reservation) != {'workspace_id','input_id','manifest_digest'} or reservation['workspace_id'] != workspace:
            raise ValueError('analysis_export_resume_scope_conflict')
        with store.connect() as database:
            row = database.execute('SELECT manifest FROM analysis_exports WHERE input_id=%s AND workspace_id=%s',
                                   (reservation['input_id'],workspace)).fetchone()
            if row is None or row['manifest']['manifest_digest'] != reservation['manifest_digest']:
                raise ValueError('analysis_export_resume_input_unavailable')
            manifest = row['manifest']
            if game_ids is not None:
                pages = database.execute("SELECT document FROM analysis_export_pages WHERE input_id=%s AND kind='games'", (manifest['input_id'],)).fetchall()
                frozen_games = {item['game_id'] for page in pages for item in page['document']['items']}
                if len(game_ids) != len(set(game_ids)) or set(game_ids) != frozen_games:
                    raise ValueError('analysis_export_resume_selection_conflict')
    else:
        manifest = create_export(store,workspace,game_ids)
    write_export(store,manifest,directory)
    return manifest


def write_export(store, manifest, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_exact(directory / '.tempo-analysis-export.json',
                {key:manifest[key] for key in ('workspace_id','input_id','manifest_digest')})
    for reference in manifest["pages"]:
        with store.connect() as database:
            page = database.execute("SELECT document FROM analysis_export_pages WHERE input_id=%s AND kind=%s AND page_index=%s", (manifest["input_id"],reference["kind"],reference["page_index"])).fetchone()["document"]
        if digest(page) != reference["digest"]:
            raise ValueError("analysis_export_digest_mismatch")
        write_exact(directory / reference["filename"], page)
    # The manifest is written last, so an interrupted directory is never complete.
    write_exact(directory / "manifest.json", manifest)


def write_exact(path, value):
    encoded = canonical(value) + "\n"
    if path.exists():
        if path.read_text() != encoded:
            raise ValueError("export_output_conflict")
    else:
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.tempo-export-',delete=False) as output:
            temporary=Path(output.name)
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        try:
            try:
                os.link(temporary,path)
            except FileExistsError:
                if path.read_text()!=encoded:
                    raise ValueError('export_output_conflict')
        finally:
            temporary.unlink()
