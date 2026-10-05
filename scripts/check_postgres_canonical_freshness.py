"""CF-1/9: real durable PostgreSQL scope, graph, coverage, and publication races."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import chess
from fastapi import HTTPException
from app import postgres_store, coverage_maia_commands
from app.branch_commands import add_repertoire_branch
from app.card_commands import revise_card, archive_card
from app.pgn_import_commands import admit_pgn_import, prepare_import_payload
from app.canonical_prefix_api import save_prefix
from app.services import repertoire_opportunities as opportunities
from app.services.canonical_prefix import read_prefix, line_origin, prefix_projection, scope_line
from app.services.canonical_prefix_preview import request_preview, execute_prefix_preview_slice, _next_item
from app.services.canonical_scope_freshness import game_scope_generation, coverage_run_is_current
from app.services.durable_tasks import complete_task, claim_task, enqueue_task_in_transaction, warm_completion_sql
from app.services.postgres_opening_graph import execute_postgres_opening_graph_slice
from app.services.postgres_coverage_seed import execute_coverage_seed_slice, request_coverage_seed_in_transaction
from app.services.postgres_coverage_explorer import _publish_node
from app.services.postgres_game_repertoire import execute_game_repertoire_comparison_slice
from app.services.repertoire_coverage import coverage_summary, coverage_gaps
from app.services.introduction_priorities import rebuild_introduction_priorities
from app.services.postgres_game_derivation import execute_game_position_index_slice
from app.services.repertoire_game_refresh import execute_repertoire_game_refresh_slice
from app.services.postgres_opening_graph import request_graph_rebuild_in_transaction
from app.services.analysis_paste import build_paste_preview, parse_pasted_lines, commit_pasted_lines
from app.services.pgn import ParsedLine
from app.integrity_repair_commands import _replace_repertoire_line

ITALIAN = ['e2e4','e7e5','g1f3','b8c6','f1c4']
NOW = datetime.now(timezone.utc).isoformat()


def claim_owned(kind, deduplication_key):
    with postgres_store.connection() as database:
        database.execute("UPDATE background_tasks SET priority=-1000,next_attempt_at=? WHERE kind=? AND deduplication_key=? AND state IN ('queued','retrying')", (NOW, kind, deduplication_key))
        stored = database.execute('SELECT state FROM background_tasks WHERE kind=? AND deduplication_key=?', (kind, deduplication_key)).fetchone()
    if stored is None or stored[0] in {'complete','failed','superseded'}:
        return None
    task = claim_task(kind)
    assert task and task['deduplication_key'] == deduplication_key, (kind, deduplication_key, task)
    return task


def run_bounded_task_slices(kind, repertoire_id, handler):
    phases = set()
    for _ in range(120):
        task = claim_owned(kind, repertoire_id)
        if task is None:
            return phases
        phases.add(task['phase'])
        handler(task)
        # Closing every pool simulates a fresh process between durable slices.
        postgres_store.close_pools()
    raise AssertionError(f'{kind} failed to finish its bounded fixture')


def set_prefix(repertoire_id, moves):
    with postgres_store.connection() as database:
        preview = request_preview(database, repertoire_id, moves)
    run_bounded_task_slices('canonical_prefix_preview', preview['preview_id'], execute_prefix_preview_slice)
    with postgres_store.connection() as database:
        return save_prefix(database, {'repertoire_id':repertoire_id,'request':{
            'preview_id':preview['preview_id'],'expected_revision':preview['revision']}})


def prefix_metadata(repertoire_id):
    with postgres_store.connection(read_only=True) as database:
        return read_prefix(database, repertoire_id)


def complete_coverage(repertoire_id):
    with postgres_store.connection() as database:
        admitted = request_coverage_seed_in_transaction(database, repertoire_id, supersede_active=True)
    run_bounded_task_slices('coverage_seed', repertoire_id, execute_coverage_seed_slice)
    with postgres_store.connection() as database:
        database.execute("UPDATE repertoire_coverage_runs SET status='complete' WHERE id=?", (admitted['run_id'],))
        database.execute("UPDATE repertoire_coverage_nodes SET explorer_status='complete',explorer_games=1000 WHERE run_id=?", (admitted['run_id'],))
        database.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,explorer_probability,required,covered,blended_probability,source_state) SELECT id,'a7a6',0.5,1,0,0.5,'explorer-only' FROM repertoire_coverage_nodes WHERE run_id=? AND ply=5", (admitted['run_id'],))
    return admitted['run_id']


def publish_game_comparison(game_id, version):
    with postgres_store.connection() as database:
        database.execute("INSERT INTO game_derivation_jobs(game_id,status,completed_phases,derivation_version,updated_at) VALUES(?,'running',1,?,?) ON CONFLICT(game_id) DO UPDATE SET status='running',completed_phases=1,derivation_version=excluded.derivation_version", (game_id,version,NOW))
        enqueue_task_in_transaction(database,'game_derivation_compare',game_id,{'game_id':game_id,'derivation_version':version,'phase':'matches','cursor':0},priority=127)
    run_bounded_task_slices('game_derivation_compare',game_id,execute_game_repertoire_comparison_slice)


def refresh_and_derive_game(game_id):
    def run_refresh(task):
        if not execute_repertoire_game_refresh_slice(task):
            complete_task(task['id'], task['generation'], task['lease_token'], kind=task['kind'])
    run_bounded_task_slices('repertoire_game_refresh', 'all', run_refresh)
    run_bounded_task_slices('game_derivation_positions', game_id, execute_game_position_index_slice)
    run_bounded_task_slices('game_derivation_compare', game_id, execute_game_repertoire_comparison_slice)


def prove_mutation_boundaries(repertoire_id, shared_repertoire_id, game_id):
    # CF-5: actual foreground command, no manual recertification before original seed.
    route = [*ITALIAN, 'f8c5', 'c2c3']
    unrelated = [*ITALIAN, 'g8f6', 'd2d3']
    with postgres_store.connection() as database:
        add_repertoire_branch(database, {'repertoire_id': repertoire_id, 'name': 'Anchor', 'trained_color': 'white', 'starting_fen': chess.STARTING_FEN, 'moves': route})
        add_repertoire_branch(database, {'repertoire_id': repertoire_id, 'name': 'Unrelated anchor', 'trained_color': 'white', 'starting_fen': chess.STARTING_FEN, 'moves': unrelated})
    set_prefix(repertoire_id, ITALIAN)
    downstream = prefix_projection(route)['ending_fen']
    with postgres_store.connection() as database:
        before = read_prefix(database, repertoire_id)['source_revision']
        branch = add_repertoire_branch(database, {'repertoire_id': repertoire_id, 'name': 'Downstream', 'trained_color': 'white', 'starting_fen': downstream, 'moves': ['g8f6', 'd2d4']})
        requested_run = database.execute('SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=? ORDER BY created_at DESC LIMIT 1', (repertoire_id,)).fetchone()[0]
    with postgres_store.connection(read_only=True) as database:
        current = read_prefix(database, repertoire_id)
        assert current['source_revision'] > before
        assert line_origin(database, current['preview_id'], downstream) == route
        assert line_origin(database, current['preview_id'], prefix_projection(unrelated)['ending_fen']) is None
        admitted_line = dict(database.execute('SELECT * FROM repertoire_lines WHERE id=?', (branch['id'],)).fetchone())
        assert not scope_line(database, repertoire_id, admitted_line).get('scope_pending')
    run_bounded_task_slices('coverage_seed', repertoire_id, execute_coverage_seed_slice)
    assert coverage_summary(repertoire_id)['run_id'] == requested_run
    assert coverage_summary(repertoire_id)['status'] == 'queued', coverage_summary(repertoire_id)
    # Existing prefixed PGN import, shared paste, and actual PG integrity replacement.
    for admission in ('pgn', 'paste', 'repair'):
        set_prefix(repertoire_id, ITALIAN)
        with postgres_store.connection() as database:
            if admission == 'pgn':
                result = admit_pgn_import(database, prepare_import_payload(repertoire_id+'.pgn', 'white', 3, 1, [ParsedLine(downstream, ['d7d6', 'd2d3'], [])]))
                assert result['repertoire_id'] == repertoire_id
            elif admission == 'paste':
                text = 'a6 d4'
                preview = build_paste_preview(database, text, downstream, None)
                result = commit_pasted_lines(database, text, downstream, None, preview['preview_token'], [{'index': 0, 'repertoire_id': repertoire_id, 'acknowledge_conflict': True}], preview, parse_pasted_lines(text, downstream))
                assert not result['saved'][0]['duplicate']
            else:
                source = database.execute('SELECT * FROM repertoire_lines WHERE id=?', (branch['id'],)).fetchone()
                assert _replace_repertoire_line(database, source, ['g8f6', 'd2d3'])
            current = read_prefix(database, repertoire_id)
            assert line_origin(database, current['preview_id'], downstream) == route
    print('PASS CF-5 PostgreSQL downstream branch -> final source certificate -> original coverage seed; PGN, paste, repair current immediately; unrelated anchors remain stale', flush=True)

    # CF-7: real graph materialization of A/B, explicit collision, later cleanup.
    set_prefix(repertoire_id, [])
    run_bounded_task_slices('opening_graph_rebuild', repertoire_id, execute_postgres_opening_graph_slice)
    with postgres_store.connection() as database:
        pair = database.execute("SELECT a.id first_id,b.id replacement_id,b.start_fen,b.moves_json FROM cards a JOIN cards b ON a.start_fen=b.start_fen AND a.id<>b.id WHERE a.repertoire_id=? AND b.repertoire_id=? AND a.canonical_route_source=0 AND b.canonical_route_source=0 AND a.archived=0 AND b.archived=0 ORDER BY a.id,b.id LIMIT 1", (repertoire_id, repertoire_id)).fetchone()
        assert pair is not None
        first_id, replacement_id = pair['first_id'], pair['replacement_id']
        for identifier in (first_id, replacement_id):
            assert database.execute('SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (repertoire_id, identifier)).fetchone()[0] == 0
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0) ON CONFLICT(repertoire_id,card_id) DO NOTHING', (shared_repertoire_id, replacement_id))
        database.execute('UPDATE cards SET repertoire_id=? WHERE id=?', (shared_repertoire_id, replacement_id))
    # Give Y current independent route/coverage certificates before X adopts B.
    with postgres_store.connection() as database:
        database.execute('DELETE FROM repertoire_lines WHERE repertoire_id=?', (shared_repertoire_id,))
        add_repertoire_branch(database, {'repertoire_id': shared_repertoire_id, 'name': 'Shared certificate', 'trained_color': 'white', 'starting_fen': chess.STARTING_FEN, 'moves': route})
    set_prefix(shared_repertoire_id, ITALIAN)
    shared_run_id = complete_coverage(shared_repertoire_id)
    def source_ids(database, scope_id):
        identifiers = set()
        cursor = ''
        for _ in range(100):
            item = _next_item(database, {'id': 'source-inspection', 'repertoire_id': scope_id}, {'phase': 'cards', 'cursor': cursor})
            if item is None:
                return identifiers
            identifiers.add(item['id'])
            cursor = item['id']
        raise AssertionError('Authored source fixture exceeded its bound')
    with postgres_store.connection(read_only=True) as database:
        shared_sources_before = source_ids(database, shared_repertoire_id)
        assert replacement_id not in shared_sources_before
    with postgres_store.connection() as database:
        shared_prefix = read_prefix(database, shared_repertoire_id)
        shared_revision = shared_prefix['source_revision']
        shared_ending_fen = prefix_projection(route)['ending_fen']
        assert line_origin(database, shared_prefix['preview_id'], shared_ending_fen) == route
        before = read_prefix(database, repertoire_id)['source_revision']
        result = revise_card(database, {'card_id': first_id, 'request': {'starting_fen': pair['start_fen'], 'moves': json.loads(pair['moves_json']), 'history_mode': 'preserve', 'expected_revision': 1}})
        assert result['card_id'] == replacement_id
        assert database.execute('SELECT canonical_route_source FROM cards WHERE id=?', (replacement_id,)).fetchone()[0] == 1
        assert database.execute('SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (repertoire_id, replacement_id)).fetchone()[0] == 1
        assert database.execute('SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (shared_repertoire_id, replacement_id)).fetchone()[0] == 0
        assert read_prefix(database, repertoire_id)['source_revision'] > before
        assert read_prefix(database, shared_repertoire_id)['source_revision'] == shared_revision
        assert source_ids(database, shared_repertoire_id) == shared_sources_before
        assert line_origin(database, shared_prefix['preview_id'], shared_ending_fen) == route
        assert coverage_run_is_current(database, database.execute('SELECT * FROM repertoire_coverage_runs WHERE id=?', (shared_run_id,)).fetchone(), shared_repertoire_id)
        database.execute('DELETE FROM repertoire_lines WHERE repertoire_id=?', (repertoire_id,))
        request_graph_rebuild_in_transaction(database, repertoire_id, NOW[:10])
    run_bounded_task_slices('opening_graph_rebuild', repertoire_id, execute_postgres_opening_graph_slice)
    with postgres_store.connection() as database:
        database.execute('DELETE FROM repertoire_lines WHERE repertoire_id=?', (shared_repertoire_id,))
        request_graph_rebuild_in_transaction(database, shared_repertoire_id, NOW[:10])
        authored_revision = read_prefix(database, repertoire_id)['source_revision']
    run_bounded_task_slices('opening_graph_rebuild', shared_repertoire_id, execute_postgres_opening_graph_slice)
    with postgres_store.connection() as database:
        assert read_prefix(database, repertoire_id)['source_revision'] == authored_revision
        assert database.execute('SELECT archived FROM cards WHERE id=?', (replacement_id,)).fetchone()[0] == 0
        assert database.execute('SELECT 1 FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (repertoire_id, replacement_id)).fetchone()
        assert not database.execute('SELECT 1 FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (shared_repertoire_id, replacement_id)).fetchone()
        assert database.execute('SELECT repertoire_id FROM cards WHERE id=?', (replacement_id,)).fetchone()[0] == repertoire_id
        assert source_ids(database, shared_repertoire_id) == shared_sources_before
        shared_after_cleanup = read_prefix(database, shared_repertoire_id)['source_revision']
        archive_card(database, {'card_id': replacement_id})
        assert read_prefix(database, repertoire_id)['source_revision'] > authored_revision
        assert read_prefix(database, shared_repertoire_id)['source_revision'] == shared_after_cleanup
        database.execute('UPDATE cards SET archived=0 WHERE id=?', (replacement_id,))
        # Genuine mutations still invalidate every authored shared membership.
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1)', (shared_repertoire_id, replacement_id))
        structural_before = {scope_id: read_prefix(database, scope_id)['source_revision'] for scope_id in (repertoire_id, shared_repertoire_id)}
        database.execute('UPDATE cards SET archived=1 WHERE id=?', (replacement_id,))
        for scope_id, revision in structural_before.items():
            assert read_prefix(database, scope_id)['source_revision'] > revision
        database.execute('UPDATE cards SET archived=0 WHERE id=?', (replacement_id,))
        database.execute('DELETE FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (shared_repertoire_id, replacement_id))
    print('PASS CF-7 graph-generated A -> existing B promotes edited membership only; Y certificates survive X adoption; real Y graph cleanup removes generated Y link and preserves authored X/B', flush=True)

    # CF-6: current graph means card writes choose integrity-only, yet games rebuild.
    for mutation in ('revise', 'archive'):
        publish_game_comparison(game_id, 100 if mutation == 'revise' else 200)
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT 1 FROM current_repertoire_comparisons WHERE game_id=?', (game_id,)).fetchone()
            before = game_scope_generation(database)
        with postgres_store.connection() as database:
            if mutation == 'revise':
                result = revise_card(database, {'card_id': replacement_id, 'request': {'starting_fen': pair['start_fen'], 'moves': [next(move.uci() for move in chess.Board(pair['start_fen']).legal_moves if move.uci() != json.loads(pair['moves_json'])[0])], 'history_mode': 'preserve', 'expected_revision': int(database.execute('SELECT revision FROM cards WHERE id=?', (replacement_id,)).fetchone()[0])}})
                replacement_id = result['card_id']
            else:
                archive_card(database, {'card_id': replacement_id})
            assert game_scope_generation(database) > before
            assert not database.execute('SELECT 1 FROM current_repertoire_comparisons WHERE game_id=?', (game_id,)).fetchone()
            refresh = database.execute("SELECT state,payload_json FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'").fetchone()
            assert refresh['state'] == 'queued' and json.loads(refresh['payload_json']) == {'after_game_id': ''}
        refresh_and_derive_game(game_id)
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT 1 FROM current_repertoire_comparisons WHERE game_id=?', (game_id,)).fetchone()
            assert database.execute('SELECT repertoire_scope_generation FROM imported_games WHERE id=?', (game_id,)).fetchone()[0] == game_scope_generation(database)
    print('PASS CF-6 explicit authored card revise/archive hides old game publication, durably resets full refresh, real position/comparison slices restore current publication after restart', flush=True)


def prove_generated_split_boundary(card_source=0):
    from app.prefix_split_commands import accept_prefix_split  # registers the real command
    from app.command_gateway import execute_command
    from app.services.postgres_opening_graph import prepare_obsolete_graph_cards, cleanup_graph_cards_in_transaction
    identifier = 'canonical-generated-split-' + uuid.uuid4().hex
    route = ['h2h3', 'a7a6', 'g2g3', 'b7b6', 'f1g2', 'c8b7', 'g1f3']
    operation_id = identifier + '-split'
    authored_repertoire_id = identifier + '-authored'
    try:
        with postgres_store.connection() as database:
            database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)', (identifier, identifier, identifier + '.pgn', NOW))
            add_repertoire_branch(database, {'repertoire_id': identifier, 'name': 'Generated split', 'trained_color': 'white', 'starting_fen': chess.STARTING_FEN, 'moves': route})
        run_bounded_task_slices('opening_graph_rebuild', identifier, execute_postgres_opening_graph_slice)
        set_prefix(identifier, route[:3])
        run_id = complete_coverage(identifier)
        with postgres_store.connection() as database:
            source = database.execute("SELECT * FROM cards WHERE repertoire_id=? AND kind='prefix' AND canonical_route_source=0 AND archived=0", (identifier,)).fetchone()
            assert source is not None
            if card_source:
                # The globally authored card still has a generated owner link.
                database.execute('UPDATE cards SET canonical_route_source=1 WHERE id=?', (source['id'],))
                database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)', (authored_repertoire_id, authored_repertoire_id, 'authored.pgn', NOW))
                database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1)', (authored_repertoire_id, source['id']))
            prefix = read_prefix(database, identifier)
            before_scope = game_scope_generation(database)
            refresh = dict(database.execute("SELECT * FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'").fetchone())
            ending_fen = prefix_projection(route)['ending_fen']
            assert line_origin(database, prefix['preview_id'], ending_fen) == route
        result = execute_command(operation_id, 'cards.prefix_split.accept', {'card_id': source['id'], 'request': {'expected_revision': source['revision']}})
        assert result and not result['idempotent'], result
        with postgres_store.connection() as database:
            assert read_prefix(database, identifier)['source_revision'] == prefix['source_revision']
            refreshed_task = dict(database.execute("SELECT * FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'").fetchone())
            if card_source:
                assert game_scope_generation(database) > before_scope
                assert refreshed_task['generation'] > refresh['generation']
            else:
                assert game_scope_generation(database) == before_scope
                assert refreshed_task == refresh
            for child_id in [result['parent']['card_id'], result['continuation']['card_id']]:
                assert database.execute('SELECT canonical_route_source FROM cards WHERE id=?', (child_id,)).fetchone()[0] == card_source
                assert database.execute('SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (identifier, child_id)).fetchone()[0] == 0
                if card_source:
                    assert database.execute('SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (authored_repertoire_id, child_id)).fetchone()[0] == 1
            assert line_origin(database, prefix['preview_id'], ending_fen) == route
            assert coverage_run_is_current(database, database.execute('SELECT * FROM repertoire_coverage_runs WHERE id=?', (run_id,)).fetchone(), identifier)
        run_bounded_task_slices('opening_graph_rebuild', identifier, execute_postgres_opening_graph_slice)
        # Prove the prepare/commit boundary using real worker functions: adopt a
        # selected generated link before its write slice, then replay that slice.
        with postgres_store.connection() as database:
            probe_id = identifier + '-adopted-between-slices'
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES(?,?,'response',?,?,'2026-10-03',0)", (probe_id, identifier, chess.STARTING_FEN, json.dumps(route[:1])))
            database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0)', (identifier, probe_id))
            task = request_graph_rebuild_in_transaction(database, identifier, NOW[:10])
        for _ in range(120):
            claimed = claim_owned('opening_graph_rebuild', identifier)
            assert claimed is not None
            if claimed['phase'] == 'cleanup':
                break
            execute_postgres_opening_graph_slice(claimed)
            postgres_store.close_pools()
        else:
            raise AssertionError('Graph fixture never reached its cleanup slice')
        candidates = prepare_obsolete_graph_cards(identifier, claimed['generation'], probe_id[:-1])
        assert probe_id in candidates, candidates
        with postgres_store.connection() as database:
            database.execute('UPDATE repertoire_cards SET canonical_route_source=1 WHERE repertoire_id=? AND card_id=?', (identifier, probe_id))
            assert cleanup_graph_cards_in_transaction(database, claimed, candidates)
            assert database.execute('SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=? AND card_id=?', (identifier, probe_id)).fetchone()[0] == 1
            assert not cleanup_graph_cards_in_transaction(database, claimed, candidates)
        print(f'PASS CF-8 card source={card_source}: split command preserves generated owner scope/certificate/coverage and each link provenance; only authored memberships advance global scope/refresh; graph cleanup protects intervening adoption', flush=True)
    finally:
        with postgres_store.connection() as database:
            database.execute('DELETE FROM operation_receipts WHERE operation_id=?', (operation_id,))
            database.execute("DELETE FROM background_tasks WHERE deduplication_key=? OR json_extract(payload_json,'$.repertoire_id')=?", (identifier, identifier))
            database.execute('DELETE FROM repertoires WHERE id IN (?,?)', (identifier, authored_repertoire_id))
        postgres_store.close_pools()


def prove_guided_review_boundary(repertoire_id):
    from app.guided_review_commands import start_review, submit_review_attempt  # register commands
    from app.command_gateway import execute_command
    from app.services.guided_review import read_session_from_database
    game_id = 'canonical-guided-' + uuid.uuid4().hex
    operation_ids = []
    def command(name, payload):
        operation_id = game_id + '-' + str(len(operation_ids))
        operation_ids.append(operation_id)
        return execute_command(operation_id, name, payload)
    try:
        with postgres_store.connection() as database:
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,analysis_version) VALUES(?,'lichess','CanonicalReview',?,'rapid',1,'white','1-0',?,'[\"e2e4\"]',1)", (game_id, NOW, chess.STARTING_FEN))
            for finding_id, kind, ply, loss in [('A', 'repertoire lapse', 0, 300), ('B', 'tactical miss', 1, 200), ('C', 'major mistake', 2, 100)]:
                database.execute("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES(?,?,1,?,?,1,?,?,?)", (game_id + finding_id, game_id, ply, kind, json.dumps({'fen': chess.STARTING_FEN, 'best_move_uci': 'e2e4', 'loss_cp': loss}), NOW, NOW))
        started = command('games.guided_review.start', {'game_id': game_id})
        assert started and started['current']['finding_id'] == game_id + 'A', started
        with postgres_store.connection() as database:
            database.execute('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=?', (repertoire_id,))
        stale = command('games.guided_review.attempt', {'session_id': started['id'], 'finding_id': game_id + 'A', 'move_uci': 'e2e4'})
        assert stale['guided_review_error']['status_code'] == 409
        resumed = command('games.guided_review.start', {'game_id': game_id})
        assert resumed['id'] == started['id'] and resumed['current']['finding_id'] == game_id + 'B'
        with postgres_store.connection() as database:
            displayed = read_session_from_database(database, started['id'])
        assert displayed['current']['finding_id'] == game_id + 'B'
        result = command('games.guided_review.attempt', {'session_id': started['id'], 'finding_id': game_id + 'B', 'move_uci': 'e2e4'})
        assert result['revealed']['finding_id'] == game_id + 'B' and result['session']['current']['finding_id'] == game_id + 'C'
        with postgres_store.connection() as database:
            assert database.execute('SELECT finding_id FROM guided_review_attempts WHERE session_id=?', (started['id'],)).fetchone()[0] == game_id + 'B'
            # Removing a completed ID remaps the index, retaining attempt history.
            database.execute("UPDATE guided_review_sessions SET finding_ids_json=?,current_index=2 WHERE id=?", (json.dumps([game_id + 'A', game_id + 'B', game_id + 'C']), started['id']))
            remapped = read_session_from_database(database, started['id'])
            assert remapped['current_index'] == 1 and remapped['current']['finding_id'] == game_id + 'C'
            assert remapped['attempts'][0]['finding_id'] == game_id + 'B'
            database.execute("UPDATE game_findings SET kind='repertoire gap',game_scope_generation=-1 WHERE id=?", (game_id + 'C',))
        complete = command('games.guided_review.attempt', {'session_id': started['id'], 'finding_id': game_id + 'C', 'move_uci': 'e2e4'})
        assert complete['guided_review_error']['status_code'] == 404
        with postgres_store.connection() as database:
            session = read_session_from_database(database, started['id'])
            assert session['status'] == 'complete' and session['current'] is None
            assert len(session['attempts']) == 1
        restarted = command('games.guided_review.start', {'game_id': game_id})
        assert restarted['id'] != started['id'] and restarted['current']['finding_id'] == game_id + 'B'
        print('PASS CF-9 real PostgreSQL guided command reconciliation/receipts: stale target 409, GET/submit parity, completed-index remap, exhausted session 404 with durable completion and no extra attempt', flush=True)
    finally:
        with postgres_store.connection() as database:
            for operation_id in operation_ids:
                database.execute('DELETE FROM operation_receipts WHERE operation_id=?', (operation_id,))
            database.execute('DELETE FROM imported_games WHERE id=?', (game_id,))
        postgres_store.close_pools()



def prove_card_mutation_coverage_status():
    for mutation in ('revise', 'archive'):
        repertoire_id = 'canonical-status-' + uuid.uuid4().hex
        from app.services.cards import card_id
        identifier = card_id(chess.STARTING_FEN, [*ITALIAN, 'f8c5', 'c2c3', 'g8f6'])
        try:
            with postgres_store.connection() as database:
                database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)', (repertoire_id, repertoire_id, repertoire_id+'.pgn', NOW))
                add_repertoire_branch(database, {'repertoire_id': repertoire_id, 'name': 'Source', 'trained_color': 'white', 'starting_fen': chess.STARTING_FEN, 'moves': [*ITALIAN, 'f8c5', 'c2c3']})
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,?,'response',?,?,?) ON CONFLICT(id) DO UPDATE SET archived=0,superseded_by=NULL", (identifier, repertoire_id, chess.STARTING_FEN, json.dumps([*ITALIAN, 'f8c5', 'c2c3', 'g8f6']), NOW[:10]))
                database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1)', (repertoire_id, identifier))
            set_prefix(repertoire_id, ITALIAN)
            old_run = complete_coverage(repertoire_id)
            with postgres_store.connection() as database:
                coverage_task_before = dict(database.execute("SELECT generation,state,payload_json FROM background_tasks WHERE kind='coverage_seed' AND deduplication_key=?", (repertoire_id,)).fetchone())
                if mutation == 'revise':
                    revision = database.execute('SELECT revision FROM cards WHERE id=?', (identifier,)).fetchone()[0]
                    revise_card(database, {'card_id': identifier, 'request': {'starting_fen': chess.STARTING_FEN, 'moves': [*ITALIAN, 'f8c5', 'c2c3', 'g8f6', 'd2d3'], 'history_mode': 'preserve', 'expected_revision': revision}})
                else:
                    archive_card(database, {'card_id': identifier})
                assert database.execute('SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=? ORDER BY created_at DESC LIMIT 1', (repertoire_id,)).fetchone()[0] == old_run
                assert dict(database.execute("SELECT generation,state,payload_json FROM background_tasks WHERE kind='coverage_seed' AND deduplication_key=?", (repertoire_id,)).fetchone()) == coverage_task_before
            summary = coverage_summary(repertoire_id)
            assert summary['status'] == 'failed' and summary['run_id'] is None, summary
            assert summary['probability_coverage'] is None and not summary['is_complete']
            assert 'Canonical prefix' in summary['last_error'] and 'refresh coverage' in summary['last_error']
            assert coverage_gaps(repertoire_id) == []
            set_prefix(repertoire_id, ITALIAN)
            assert complete_coverage(repertoire_id) != old_run
            assert coverage_summary(repertoire_id)['status'] == 'complete'
        finally:
            with postgres_store.connection() as database:
                database.execute("DELETE FROM background_tasks WHERE deduplication_key=? OR json_extract(payload_json,'$.repertoire_id')=?", (repertoire_id, repertoire_id))
                database.execute('DELETE FROM repertoires WHERE id=?', (repertoire_id,))
    print('PASS CF-10 authored revise/archive without replacement coverage reports recheck guidance; explicit recheck and refresh recover current publication', flush=True)


def prove_last_generated_membership_cleanup():
    from app.services.cards import card_id
    from app.repertoire_commands import delete_repertoire
    route = ['d2d4', 'd7d5', 'c2c4', 'e7e6', 'b1c3', 'g8f6', 'c1g5']
    for owner_scope in ('generated-owner', 'unlinked-authored-owner'):
        authored_scope = 'canonical-orphan-X-' + uuid.uuid4().hex
        generated_scope = 'canonical-orphan-Y-' + uuid.uuid4().hex
        first_id, replacement_id = card_id(chess.STARTING_FEN, route[:5]), card_id(chess.STARTING_FEN, route)
        try:
            with postgres_store.connection() as database:
                for scope_id in (authored_scope, generated_scope):
                    database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)', (scope_id, scope_id, scope_id+'.pgn', NOW))
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES(?,?,'response',?,?,?,0)", (first_id, authored_scope, chess.STARTING_FEN, json.dumps(route[:5]), NOW[:10]))
                database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0)', (authored_scope, first_id))
                owner = generated_scope if owner_scope == 'generated-owner' else authored_scope
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,canonical_route_source) VALUES(?,?,'response',?,?,?,?)", (replacement_id, owner, chess.STARTING_FEN, json.dumps(route), NOW[:10], int(owner_scope == 'unlinked-authored-owner')))
                database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0)', (generated_scope, replacement_id))
                database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct',?,1,2)", (replacement_id, NOW))
                database.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,(SELECT COALESCE(MAX(position),0)+1 FROM daily_queue))", (NOW[:10], replacement_id))
                if owner_scope == 'generated-owner':
                    revise_card(database, {'card_id': first_id, 'request': {'starting_fen': chess.STARTING_FEN, 'moves': route, 'history_mode': 'preserve', 'expected_revision': 1}})
                    delete_repertoire(database, {'repertoire_id': authored_scope})
                source_before = read_prefix(database, generated_scope)['source_revision']
                request_graph_rebuild_in_transaction(database, generated_scope, NOW[:10])
            run_bounded_task_slices('opening_graph_rebuild', generated_scope, execute_postgres_opening_graph_slice)
            with postgres_store.connection() as database:
                assert not database.execute('SELECT 1 FROM repertoire_cards WHERE card_id=?', (replacement_id,)).fetchone()
                card = database.execute('SELECT archived,canonical_route_source FROM cards WHERE id=?', (replacement_id,)).fetchone()
                assert card['canonical_route_source'] == 1
                assert bool(card['archived']) == (owner_scope == 'generated-owner')
                assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', (replacement_id,)).fetchone()[0] == 1
                assert database.execute('SELECT status FROM daily_queue WHERE card_id=?', (replacement_id,)).fetchone()[0] == ('superseded' if owner_scope == 'generated-owner' else 'queued')
                assert _next_item(database, {'id': 'inspection', 'repertoire_id': generated_scope}, {'phase': 'cards', 'cursor': ''}) is None
                assert read_prefix(database, generated_scope)['source_revision'] == source_before
                if owner_scope == 'unlinked-authored-owner':
                    assert _next_item(database, {'id': 'inspection', 'repertoire_id': authored_scope}, {'phase': 'cards', 'cursor': ''})['id'] == replacement_id
                archive_card(database, {'card_id': replacement_id})
                assert read_prefix(database, generated_scope)['source_revision'] == source_before
        finally:
            with postgres_store.connection() as database:
                database.execute("DELETE FROM background_tasks WHERE deduplication_key IN (?,?) OR json_extract(payload_json,'$.repertoire_id') IN (?,?)", (authored_scope, generated_scope, authored_scope, generated_scope))
                database.execute('DELETE FROM repertoires WHERE id IN (?,?)', (authored_scope, generated_scope))
    print('PASS CF-11 deleting authored X then cleaning last generated Y retires orphan presentation with history intact; legitimate unlinked authored owner remains playable and Y stays unchanged', flush=True)

def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Canonical freshness proof requires the runner-owned disposable PostgreSQL instance')
    os.environ['TEMPO_DATABASE_WRITE_URL'] = 'postgresql://postgres@postgres:5432/tempo'
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    # Small real transactions retain the production background limits.
    warm_completion_sql()
    repertoire_id = 'canonical-freshness-' + uuid.uuid4().hex
    other_repertoire_id = repertoire_id + '-other'
    baseline_repertoire_id = repertoire_id + '-baseline'
    game_id = repertoire_id + '-game'
    mutation_repertoire_id = repertoire_id + '-mutations'
    saved_calculate = opportunities._calculate_node_opportunities
    try:
        with postgres_store.connection() as database:
            for identifier in (repertoire_id, other_repertoire_id, baseline_repertoire_id, mutation_repertoire_id):
                database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)',(identifier,identifier,identifier+'.pgn',NOW))
            add_repertoire_branch(database, {'repertoire_id':repertoire_id,'name':'Root','trained_color':'white','starting_fen':chess.STARTING_FEN,'moves':[*ITALIAN,'f8c5']})
        run_bounded_task_slices('opening_graph_rebuild',repertoire_id,execute_postgres_opening_graph_slice)
        set_prefix(repertoire_id,ITALIAN)
        # An obsolete derived link forces real cleanup and archival as well as staging/classification.
        with postgres_store.connection() as database:
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,canonical_route_source) VALUES(?,?,'response',?,?,'learning',?,0)",(repertoire_id+'-obsolete',repertoire_id,chess.STARTING_FEN,json.dumps(ITALIAN),NOW[:10]))
            database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0)',(repertoire_id,repertoire_id+'-obsolete'))
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,?,'response',?,?,?)",(repertoire_id+'-authored',repertoire_id,chess.STARTING_FEN,json.dumps(ITALIAN[:3]),NOW[:10]))
            database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)',(repertoire_id,repertoire_id+'-authored'))
            add_repertoire_branch(database, {'repertoire_id':repertoire_id,'name':'New branch','trained_color':'white','starting_fen':chess.STARTING_FEN,'moves':[*ITALIAN,'g8f6','d2d3']})
            after_user_write = read_prefix(database,repertoire_id)['source_revision']
            after_user_game_scope = game_scope_generation(database)
            after_user_refresh_generation = database.execute("SELECT generation FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'").fetchone()[0]
            requested_run = database.execute('SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=? ORDER BY created_at DESC LIMIT 1',(repertoire_id,)).fetchone()[0]
        phases = run_bounded_task_slices('opening_graph_rebuild',repertoire_id,execute_postgres_opening_graph_slice)
        assert {'stage','link','classify','cleanup','finalize'} <= phases, phases
        assert prefix_metadata(repertoire_id)['source_revision'] == after_user_write, 'Graph materialization changed the authoritative source version'
        with postgres_store.connection(read_only=True) as database:
            assert game_scope_generation(database) == after_user_game_scope
            assert database.execute("SELECT generation FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'").fetchone()[0] == after_user_refresh_generation
            assert database.execute('SELECT archived FROM cards WHERE id=?',(repertoire_id+'-authored',)).fetchone()[0] == 0
            assert database.execute('SELECT 1 FROM repertoire_cards WHERE repertoire_id=? AND card_id=?',(repertoire_id,repertoire_id+'-authored')).fetchone()
        run_bounded_task_slices('coverage_seed',repertoire_id,execute_coverage_seed_slice)
        assert coverage_summary(repertoire_id)['run_id'] == requested_run
        assert coverage_summary(repertoire_id)['status'] == 'queued', coverage_summary(repertoire_id)
        print('PASS CF-2 real branch -> graph stage/link/classify/cleanup -> original coverage seed, with restart between slices',flush=True)
        # Current route certificates are required; certifying the authored source is explicit.
        set_prefix(repertoire_id,ITALIAN)
        old_run = complete_coverage(repertoire_id)
        with postgres_store.connection() as database:
            database.execute('UPDATE repertoire_coverage_candidates SET covered=1 WHERE node_id IN (SELECT id FROM repertoire_coverage_nodes WHERE run_id=?)',(old_run,))
        assert coverage_summary(repertoire_id)['is_complete']
        with postgres_store.connection() as database:
            lease = coverage_maia_commands.claim_maia_node(database,{})['job']
            assert lease and lease['run_id'] == old_run
            node = dict(database.execute('SELECT n.*,r.settings_json FROM repertoire_coverage_nodes n JOIN repertoire_coverage_runs r ON r.id=n.run_id WHERE n.id=?',(lease['node_id'],)).fetchone())
            database.execute("UPDATE repertoire_coverage_runs SET status='running' WHERE id=?",(old_run,))
            database.execute("UPDATE repertoire_coverage_nodes SET explorer_status='queued' WHERE id=?",(node['id'],))
            enqueue_task_in_transaction(database,'coverage_explorer',repertoire_id,{'run_id':old_run,'repertoire_id':repertoire_id,'after_node_id':''},priority=80)
        explorer_task = claim_owned('coverage_explorer',repertoire_id)
        with postgres_store.connection() as database:
            database.execute('DELETE FROM repertoire_lines WHERE repertoire_id=? AND name=?',(repertoire_id,'New branch'))
        assert coverage_summary(repertoire_id)['run_id'] is None and coverage_gaps(repertoire_id) == []
        with postgres_store.connection() as database:
            assert coverage_maia_commands.claim_maia_node(database,{})['job'] is None
            _publish_node(database, explorer_task,node,{'moves':[{'uci':'h7h6','white':1000}]},'cache','rapid:1.000000','1600')
            assert not database.execute("SELECT 1 FROM repertoire_coverage_candidates WHERE node_id=? AND move_uci='h7h6'",(node['id'],)).fetchone()
        for handler in (coverage_maia_commands.heartbeat_maia_node, coverage_maia_commands.submit_maia_node, coverage_maia_commands.fail_maia_node):
            try:
                with postgres_store.connection() as database:
                    handler(database,{'node_id':lease['node_id'],'lease_id':lease['lease_id'],'moves':[],'error':'injected old result'})
            except HTTPException as error:
                assert error.status_code == 409
            else:
                raise AssertionError('Stale Maia publication was accepted')
        set_prefix(repertoire_id,ITALIAN)
        assert complete_coverage(repertoire_id) != old_run and coverage_summary(repertoire_id)['run_id'] is not None
        print('PASS CF-1 PostgreSQL source invalidation hides old coverage, fences Explorer and Maia, and recertification restores a current run',flush=True)
        # Race a real calculation against a foreground source write with no open read connection.
        with postgres_store.connection() as database:
            enqueue_task_in_transaction(database,'repertoire_opportunity',repertoire_id,{'repertoire_id':repertoire_id,'phase':'nodes','cursor':''},priority=130)
        task = claim_owned('repertoire_opportunity',repertoire_id)
        def race(inputs):
            result = saved_calculate(inputs)
            if any(decision['active'] for decision in result):
                with postgres_store.connection() as database:
                    database.execute("UPDATE repertoire_lines SET moves_json=? WHERE repertoire_id=? AND name='Root'",(json.dumps([*ITALIAN,'f8c5','c2c3']),repertoire_id))
            return result
        opportunities._calculate_node_opportunities = race
        # A node without gaps can be visited first; walk until the calculation changes the version.
        before_race = prefix_metadata(repertoire_id)['source_revision']
        for _ in range(8):
            opportunities.execute_opportunity_slice(task)
            if prefix_metadata(repertoire_id)['source_revision'] != before_race:
                break
            task = claim_owned('repertoire_opportunity',repertoire_id)
            assert task is not None
        assert prefix_metadata(repertoire_id)['source_revision'] != before_race
        with postgres_store.connection(read_only=True) as database:
            assert not database.execute('SELECT 1 FROM repertoire_opportunities WHERE repertoire_id=?',(repertoire_id,)).fetchone()
        opportunities._calculate_node_opportunities = saved_calculate
        set_prefix(repertoire_id,ITALIAN)
        complete_coverage(repertoire_id)
        for _ in range(30):
            task = claim_owned('repertoire_opportunity',repertoire_id)
            assert task is not None
            opportunities.execute_opportunity_slice(task)
            with postgres_store.connection(read_only=True) as database:
                if opportunities.list_opportunities(database,repertoire_id): break
        else: raise AssertionError('Current opportunity never published after recertification')
        print('PASS CF-4 PostgreSQL opportunity source race discards publication, then rebuilds current discoveries',flush=True)
        # A newly eligible repertoire invalidates the primary selected for a different repertoire.
        with postgres_store.connection() as database:
            database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,\'white\',?,?,?)',(other_repertoire_id+'-line',other_repertoire_id,'Stub',chess.STARTING_FEN,json.dumps(ITALIAN[:3]),NOW))
        set_prefix(other_repertoire_id,ITALIAN)
        with postgres_store.connection() as database:
            database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,\'white\',?,?,?)',(baseline_repertoire_id+'-line',baseline_repertoire_id,'Baseline stub',chess.STARTING_FEN,json.dumps(ITALIAN[:1]),NOW))
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES(?,'lichess','fixture',?,'rapid',1,'white','1-0',?,?)",(game_id,NOW,chess.STARTING_FEN,json.dumps(['e2e4','e7e5','g1f3','d7d6','f1c4'])))
        publish_game_comparison(game_id,1)
        with postgres_store.connection() as database:
            rebuild_introduction_priorities(database,repertoire_id)
            assert database.execute('SELECT 1 FROM current_repertoire_card_introduction_priorities WHERE repertoire_id=?',(repertoire_id,)).fetchone()
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT repertoire_id FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()[0] == baseline_repertoire_id
        set_prefix(other_repertoire_id,[])
        with postgres_store.connection(read_only=True) as database:
            assert not database.execute('SELECT 1 FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()
            assert not database.execute('SELECT 1 FROM current_repertoire_card_introduction_priorities WHERE repertoire_id=?',(repertoire_id,)).fetchone()
        publish_game_comparison(game_id,2)
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT repertoire_id FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()[0] == other_repertoire_id
            before_noop = game_scope_generation(database)
        set_prefix(other_repertoire_id,[])
        with postgres_store.connection(read_only=True) as database:
            assert game_scope_generation(database) == before_noop
            assert database.execute('SELECT 1 FROM game_repertoire_matches WHERE game_id=?',(game_id,)).fetchone()
        set_prefix(other_repertoire_id,ITALIAN)
        with postgres_store.connection(read_only=True) as database:
            for table in ('game_repertoire_matches','repertoire_comparisons','repertoire_decision_events'):
                assert not database.execute(f'SELECT 1 FROM {table} WHERE game_id=?',(game_id,)).fetchone()
        publish_game_comparison(game_id,3)
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT repertoire_id FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()[0] == baseline_repertoire_id
        set_prefix(baseline_repertoire_id,ITALIAN)
        publish_game_comparison(game_id,4)
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT repertoire_id FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()[0] is None
        set_prefix(other_repertoire_id,[])
        with postgres_store.connection(read_only=True) as database:
            assert not database.execute('SELECT 1 FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()
        publish_game_comparison(game_id,5)
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT repertoire_id FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()[0] == other_repertoire_id
        print('PASS CF-3 PostgreSQL global scope fences NULL and newly eligible/ineligible classifications; no-op saves retain publication',flush=True)
        with postgres_store.connection() as database:
            before_clearing = read_prefix(database,repertoire_id)['source_revision']
            database.execute("UPDATE cards SET moves_json='[]' WHERE id=?",(repertoire_id+'-authored',))
            assert read_prefix(database,repertoire_id)['source_revision'] > before_clearing
            # The retained obsolete presentation is deliberately archived and
            # unlinked by CF-2. A card's owner alone cannot select a live
            # generated membership; this must exercise actual link promotion.
            generated_card = database.execute(
                'SELECT card.id FROM cards card JOIN repertoire_cards link ON link.card_id=card.id '
                'WHERE link.repertoire_id=? AND link.canonical_route_source=0 '
                'AND card.canonical_route_source=0 AND card.archived=0 ORDER BY card.id LIMIT 1',
                (repertoire_id,),
            ).fetchone()
            assert generated_card is not None, 'Canonical fixture requires an active generated membership'
            generated_card = generated_card[0]
            database.execute("UPDATE cards SET moves_json='[]' WHERE id=?",(generated_card,))
            assert database.execute('SELECT canonical_route_source FROM cards WHERE id=?',(generated_card,)).fetchone()[0] == 1
            assert database.execute('SELECT canonical_route_source FROM repertoire_cards WHERE card_id=?',(generated_card,)).fetchone()[0] == 1
        print('PASS PostgreSQL clearing authored moves revokes the previous route source',flush=True)
        prove_mutation_boundaries(mutation_repertoire_id, other_repertoire_id, game_id)
        prove_generated_split_boundary()
        prove_generated_split_boundary(card_source=1)
        prove_guided_review_boundary(mutation_repertoire_id)
        prove_card_mutation_coverage_status()
        prove_last_generated_membership_cleanup()
    finally:
        opportunities._calculate_node_opportunities = saved_calculate
        with postgres_store.connection() as database:
            database.execute('DELETE FROM imported_games WHERE id=?',(game_id,))
            database.execute("DELETE FROM background_tasks WHERE deduplication_key IN (?,?,?) OR json_extract(payload_json,'$.repertoire_id') IN (?,?,?) OR json_extract(payload_json,'$.game_id')=?",(repertoire_id,other_repertoire_id,baseline_repertoire_id,repertoire_id,other_repertoire_id,baseline_repertoire_id,game_id))
            database.execute("DELETE FROM background_tasks WHERE deduplication_key=? OR json_extract(payload_json,'$.repertoire_id')=?", (mutation_repertoire_id, mutation_repertoire_id))
            database.execute('DELETE FROM repertoires WHERE id IN (?,?,?,?)',(repertoire_id,other_repertoire_id,baseline_repertoire_id,mutation_repertoire_id))
        postgres_store.close_pools()

if __name__ == '__main__':
    main()
