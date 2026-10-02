"""CF-1/4: real durable PostgreSQL scope, graph, coverage, and publication races."""
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
from app.canonical_prefix_api import save_prefix
from app.services import repertoire_opportunities as opportunities
from app.services.canonical_prefix import read_prefix
from app.services.canonical_prefix_preview import request_preview, execute_prefix_preview_slice
from app.services.canonical_scope_freshness import game_scope_generation
from app.services.durable_tasks import claim_task, enqueue_task_in_transaction, warm_completion_sql
from app.services.postgres_opening_graph import execute_postgres_opening_graph_slice
from app.services.postgres_coverage_seed import execute_coverage_seed_slice, request_coverage_seed_in_transaction
from app.services.postgres_coverage_explorer import _publish_node
from app.services.postgres_game_repertoire import execute_game_repertoire_comparison_slice
from app.services.repertoire_coverage import coverage_summary, coverage_gaps

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
    saved_calculate = opportunities._calculate_node_opportunities
    try:
        with postgres_store.connection() as database:
            for identifier in (repertoire_id, other_repertoire_id, baseline_repertoire_id):
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
            requested_run = database.execute('SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=? ORDER BY created_at DESC LIMIT 1',(repertoire_id,)).fetchone()[0]
        phases = run_bounded_task_slices('opening_graph_rebuild',repertoire_id,execute_postgres_opening_graph_slice)
        assert {'stage','link','classify','cleanup','finalize'} <= phases, phases
        assert prefix_metadata(repertoire_id)['source_revision'] == after_user_write, 'Graph materialization changed the authoritative source version'
        with postgres_store.connection(read_only=True) as database:
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
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT repertoire_id FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()[0] == baseline_repertoire_id
        set_prefix(other_repertoire_id,[])
        with postgres_store.connection(read_only=True) as database:
            assert not database.execute('SELECT 1 FROM repertoire_comparisons WHERE game_id=?',(game_id,)).fetchone()
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
            generated_card = database.execute('SELECT id FROM cards WHERE repertoire_id=? AND canonical_route_source=0 LIMIT 1',(repertoire_id,)).fetchone()[0]
            database.execute("UPDATE cards SET moves_json='[]' WHERE id=?",(generated_card,))
            assert database.execute('SELECT canonical_route_source FROM cards WHERE id=?',(generated_card,)).fetchone()[0] == 1
            assert database.execute('SELECT canonical_route_source FROM repertoire_cards WHERE card_id=?',(generated_card,)).fetchone()[0] == 1
        print('PASS PostgreSQL clearing authored moves revokes the previous route source',flush=True)
    finally:
        opportunities._calculate_node_opportunities = saved_calculate
        with postgres_store.connection() as database:
            database.execute('DELETE FROM imported_games WHERE id=?',(game_id,))
            database.execute("DELETE FROM background_tasks WHERE deduplication_key IN (?,?,?) OR json_extract(payload_json,'$.repertoire_id') IN (?,?,?) OR json_extract(payload_json,'$.game_id')=?",(repertoire_id,other_repertoire_id,baseline_repertoire_id,repertoire_id,other_repertoire_id,baseline_repertoire_id,game_id))
            database.execute('DELETE FROM repertoires WHERE id IN (?,?,?)',(repertoire_id,other_repertoire_id,baseline_repertoire_id))
        postgres_store.close_pools()

if __name__ == '__main__':
    main()
