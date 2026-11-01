"""Thirty-transition continuation of the existing owned packaged assembly.

This module creates synthetic authored/learning starting facts, then delegates
source capture, preparation, reviews, issuance and settlement to the installed
candidate. It owns no launcher or migration/teardown implementation.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import uuid

import chess
import chess.pgn
from fsrs import Card

from fixtures.study_recovery_content import OBSERVED, publication


def memory_observation(docker, container):
    """Bounded read-only cgroup/process observations; never change teardown."""
    command = "printf 'CURRENT\\n'; cat /sys/fs/cgroup/memory.current; printf 'PEAK\\n'; cat /sys/fs/cgroup/memory.peak; printf 'CPU\\n'; cat /sys/fs/cgroup/cpu.stat; printf 'EVENTS\\n'; cat /sys/fs/cgroup/memory.events; printf 'STAT\\n'; awk '$1==\"anon\" || $1==\"file\" || $1==\"kernel\" {print}' /sys/fs/cgroup/memory.stat; printf 'PROCESSES\\n'; awk '/^(Name|Pid|VmRSS):/ {print}' /proc/[0-9]*/status"
    try:
        result = docker('exec', container, 'sh', '-c', command, check=False)
        return dict(returncode=result.returncode, output=result.stdout[:32768], error=result.stderr[:2000])
    except Exception as error:
        return dict(observation_error=type(error).__name__)


def _rows(transaction, table, columns, values, *, conflict=''):
    """Bounded committed fixture writes keep all normal production triggers."""
    for offset in range(0, len(values), 100):
        batch = values[offset:offset + 100]
        placeholders = ','.join('(' + ','.join(['%s'] * len(columns)) + ')' for _ in batch)
        with transaction() as database:
            database.execute_native('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES '
                + placeholders + conflict, tuple(value for row in batch for value in row))



@lru_cache(maxsize=1)
def _authored_checkpoint(path):
    candidate = Path(path)
    data = candidate.read_bytes()
    value = json.loads(data)
    assert value['version'] == 2
    assert value['generator_sha256'] == hashlib.sha256(Path(publication.__code__.co_filename).read_bytes()).hexdigest(), \
        'recovery_authored_checkpoint_generator_changed'
    return value, hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=10)
def _generated_content(ordinal):
    return publication(OBSERVED[ordinal], f'rep-{ordinal}', 'baseline', ordinal=ordinal, existing_custom=ordinal == 0)


def _content(ordinal, repertoire):
    """Reuse authored bytes only; never reuse a finding, receipt or authority."""
    path = os.environ.get('TEMPO_STUDY_RECOVERY_CONTENT')
    if path:
        checkpoint, digest = _authored_checkpoint(path)
        facts = checkpoint['publications'][ordinal]
        report = checkpoint['generated_shapes'][ordinal]
        assert report['observed'] == OBSERVED[ordinal].__dict__
        control = checkpoint['control_substitution'] if ordinal == 0 else {}
        original = dict(lines=[row for row in facts['lines'] if row['id'] != control.get('line_id')],
            cards=[row for row in facts['cards'] if row['id'] != control.get('card_id')],
            graphs=[row for row in facts['graphs'] if row['line_id'] != control.get('line_id')],
            target=report['target'], observed=report['observed'], generated=report['generated'])
        provenance = dict(kind='saved authored content only', path=path, sha256=digest,
            control='actual retained disposable DB control replaces the static pure control')
    else:
        original = _generated_content(ordinal)
        provenance = dict(kind='generated authored content once per mode pair', generator='study_recovery_content.publication')
    prior = f'rep-{ordinal}'
    def line_id(value):
        assert value.startswith(prior + '-')
        return repertoire + value[len(prior):]
    return dict(original,
        lines=[row | dict(id=line_id(row['id']), repertoire_id=repertoire) for row in original['lines']],
        cards=[row | dict(repertoire_id=repertoire) for row in original['cards']],
        graphs=[row | dict(repertoire_id=repertoire, line_id=line_id(row['line_id'])) for row in original['graphs']],
        target=original['target'] | dict(repertoire_id=repertoire) if original['target'] else None,
        provenance=provenance)


def seed_scope(case, original_repertoire, directory):
    """Use observed executable counts/lengths, not inert backlog padding."""
    namespace = 'recovery-' + uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    introduced = (now - timedelta(days=90)).date().isoformat()
    state = Card(card_id=100, due=now + timedelta(days=30), last_review=now - timedelta(days=3),
                 stability=20, difficulty=5).to_json()
    with case.transaction() as database:
        retained_control = [dict(row) for row in database.execute_native('''SELECT line.id,line.start_fen,line.moves_json,
            graph.card_id FROM repertoire_lines line JOIN opening_graph_steps graph ON graph.line_id=line.id
            WHERE line.repertoire_id=%s ORDER BY graph.decision_index''', (original_repertoire,)).fetchall()]
    assert len(retained_control) == 1
    scope, shapes, standard, physical_ids = [], [], None, set()
    for ordinal, observed in enumerate(OBSERVED):
        repertoire = original_repertoire if ordinal == 0 else f'{namespace}-rep-{ordinal}'
        scope.append(repertoire)
        if ordinal:
            with case.transaction() as database:
                database.execute_native("""INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day)
                    VALUES(%s,%s,'synthetic-recovery',%s,0)""", (repertoire, f'Recovery scope {ordinal}', now.isoformat()))
                database.execute_native("""INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at)
                    VALUES(%s,1,'ready',%s)""", (repertoire, now.isoformat()))
        generated = _content(ordinal, repertoire)
        shapes.append(dict(repertoire_ordinal=ordinal, observed=generated['observed'], generated=generated['generated'],
            authored_content=generated['provenance']))
        if standard is None and generated['target']:
            standard = generated['target']
        physical_ids.update(row['id'] for row in generated['cards'])
        _rows(case.transaction, 'repertoire_lines',
            ('id', 'repertoire_id', 'name', 'trained_color', 'start_fen', 'moves_json', 'created_at'),
            [(row['id'], repertoire, 'Synthetic legal authored route', observed.color,
              row['start_fen'], row['moves_json'], now.isoformat()) for row in generated['lines']])
        _rows(case.transaction, 'cards', ('id', 'repertoire_id', 'kind', 'start_fen', 'moves_json',
            'state', 'due_date', 'introduced_at', 'revision', 'interval_days', 'fsrs_card_json',
            'first_correct_at', 'stability', 'content_type', 'trained_color'),
            [(row['id'], repertoire, row['kind'], row['start_fen'], row['moves_json'], 'mature',
              (now + timedelta(days=30)).date().isoformat(), introduced, 1, 20, state,
              (now - timedelta(days=3)).isoformat(), 20, 'opening', observed.color) for row in generated['cards']],
            conflict=' ON CONFLICT(id) DO NOTHING')
        _rows(case.transaction, 'repertoire_cards', ('repertoire_id', 'card_id', 'canonical_route_source'),
            [(repertoire, row['id'], 0) for row in generated['cards']], conflict=' ON CONFLICT DO NOTHING')
        _rows(case.transaction, 'opening_graph_steps', ('repertoire_id', 'generation', 'line_id', 'decision_index',
            'card_id', 'decision_fen_key', 'starting_fen', 'moves_json', 'trained_color', 'segment_kind',
            'first_decision_index', 'last_decision_index', 'decision_fen_keys_json', 'parent_card_id'),
            [(repertoire, 1, row['line_id'], row['decision_index'], row['card_id'], row['decision_fen_key'],
              row['starting_fen'], row['moves_json'], observed.color, row['segment_kind'], row['first_decision_index'],
              row['last_decision_index'], row['decision_fen_keys_json'], row['parent_card_id']) for row in generated['graphs']])
        # Authored starting facts are known legal by the deterministic generator;
        # retain the same completed graph/integrity fixture authority as the
        # existing packaged proof. Runtime qualification still rechecks it.
        scan = repertoire + '-recovery-integrity'
        with case.transaction() as database:
            for kind, identity in (('opening_graph_rebuild', repertoire + '-recovery-graph'), ('integrity_scan', scan)):
                prior = database.execute_native('SELECT state FROM background_tasks WHERE kind=%s AND deduplication_key=%s',
                    (kind, repertoire)).fetchall()
                assert all(row['state'] == 'complete' for row in prior), 'recovery_fixture_cannot_replace_accepted_pending_work'
                database.execute_native("""DELETE FROM background_tasks WHERE kind=%s AND deduplication_key=%s""", (kind, repertoire))
                database.execute_native("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,
                    next_attempt_at,created_at,updated_at) VALUES(%s,%s,%s,1,'complete',%s,%s,%s,%s)""",
                    (identity, kind, repertoire, json.dumps(dict(repertoire_id=repertoire, graph_generation=1)),
                     now.isoformat(), now.isoformat(), now.isoformat()))
            database.execute_native("""INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,scan_status,
                scan_generation,scan_completed_sources,scan_total_sources) VALUES(%s,'clean',%s,'idle',%s,%s,%s)
                ON CONFLICT(repertoire_id) DO UPDATE SET status='clean',checked_at=EXCLUDED.checked_at,scan_status='idle',
                scan_generation=EXCLUDED.scan_generation,scan_completed_sources=EXCLUDED.scan_completed_sources,
                scan_total_sources=EXCLUDED.scan_total_sources,scan_error=NULL""",
                (repertoire, now.isoformat(), scan + ':1', len(generated['lines']) + len(generated['cards']) + (2 if ordinal == 0 else 0),
                 len(generated['lines']) + len(generated['cards']) + (2 if ordinal == 0 else 0)))
    assert standard is not None
    for ordinal, repertoire in enumerate(scope):
        with case.transaction() as database:
            lines = database.execute_native('''SELECT COUNT(*) AS routes,
                SUM(jsonb_array_length(moves_json::jsonb)) AS authored_plies,
                MAX(jsonb_array_length(moves_json::jsonb)) AS max_route_plies
                FROM repertoire_lines WHERE repertoire_id=%s''', (repertoire,)).fetchone()
            graph = database.execute_native('''SELECT COUNT(*) AS graphs,
                SUM(jsonb_array_length(moves_json::jsonb)) AS graph_plies,
                MAX(jsonb_array_length(moves_json::jsonb)) AS max_card_plies
                FROM opening_graph_steps WHERE repertoire_id=%s AND generation=1''', (repertoire,)).fetchone()
            memberships = database.execute_native('SELECT COUNT(*) FROM repertoire_cards WHERE repertoire_id=%s',
                (repertoire,)).fetchone()[0]
        actual = dict(lines) | dict(graph) | dict(memberships=memberships)
        observed = OBSERVED[ordinal]
        assert actual['routes'] == observed.routes and actual['authored_plies'] == observed.authored_plies
        assert actual['graphs'] == observed.graphs and actual['max_route_plies'] == observed.max_route_plies
        assert actual['memberships'] == shapes[ordinal]['generated']['memberships']
        assert actual['graph_plies'] == shapes[ordinal]['generated']['graph_plies']
        shapes[ordinal]['persisted_shape'] = actual
    # The real queue workload is executable admitted opening reviews, not inert
    # jobs or a tactics-only shortcut around opening freshness predicates.
    with case.transaction() as database:
        ordinary = [row[0] for row in database.execute_native("""SELECT id FROM cards
            WHERE id=ANY(%s) AND trained_color='white' AND content_type='opening'
              AND kind IN ('prefix','response')
              AND id<>%s ORDER BY id LIMIT 576""", (sorted(physical_ids), standard['card_id'])).fetchall()]
    assert len(ordinary) == 576, ('recovery_fixture_needs_actual_executable_queue_backlog', len(ordinary))
    for offset in range(0, len(ordinary), 100):
        with case.transaction() as database:
            database.execute_native('UPDATE cards SET due_date=%s WHERE id=ANY(%s)',
                (now.date().isoformat(), ordinary[offset:offset + 100]))
    baseline_cards = [*ordinary, standard['card_id']]
    _rows(case.transaction, 'reviews', ('card_id', 'rating', 'reviewed_at', 'previous_interval',
        'next_interval', 'internal_rating', 'guided', 'source_kind', 'source_ref'),
        [(card, 'correct', (now - timedelta(days=3)).isoformat(), 20, 20, 'good', 0, 'study',
          'synthetic-recovery-baseline:' + card) for card in baseline_cards])
    result = dict(namespace=namespace, scope=scope, shapes=shapes, standard=standard, ordinary=ordinary,
        retained_control=retained_control,
        source='deterministic legal authored fixture; G0 counts and payload aggregates only',
        differences='physical memberships follow executable alias reuse and are reported, never padded with inert cards; graph segments cover complete authored routes')
    result['baseline_history_rows'] = len(baseline_cards)
    (directory / 'recovery-workload.json').write_text(json.dumps(result, indent=2))
    return result


def _marker(path, value):
    temporary = path.with_suffix('.next')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def held_prerequisite_journey(case, *, directory, report, inputs, entry, api, docker, collector, background, foreground, root, web_url,
                              publish_provider,
                              repertoire, target):
    """Actual missing owner certificate, stopped producer, save and same-key recovery.

    Reuse the small packaged scope before representative full-scope seeding. All
    starting card facts are synthetic; queue admission/reviews/effects are real
    installed owner commands. No certificate or successful task is fabricated.
    """
    def confirmed(result):
        if result['status'] == 200:
            return result['body']
        assert result['status'] == 202, result
        deadline = time.monotonic() + 45
        while True:
            observed = api('GET', '/api/operations/' + result['body']['operation_id'])
            assert observed['status'] == 200
            if observed['body'].get('state') == 'complete':
                return observed['body']['response']
            assert time.monotonic() < deadline, 'held_prerequisite_operation_pending'
            time.sleep(.05)
    controls = ['held-control-' + uuid.uuid4().hex for _ in range(2)]
    hold_scenario = directory/'hold-scenario.json'
    hold_scenario.write_text(json.dumps(dict(version=1, controls=controls, target=target['card_id'])))
    held_browser = None
    evidence = root/'test-results/collection-release'/('held-'+uuid.uuid4().hex)
    evidence.mkdir(parents=True)
    report['held_prerequisite_evidence'] = str(evidence)
    def release(stage):
        _marker(directory/'hold-provider-ready.json', dict(stage=stage))
    def wait_browser(stage):
        deadline = time.monotonic()+45
        while True:
            marker = directory/'hold-browser-ready.json'
            if marker.exists():
                value = json.loads(marker.read_text())
                if value['stage'] == stage:
                    return value
            assert held_browser.poll() is None, 'held_browser_failed_before_'+stage
            assert time.monotonic()<deadline, 'held_browser_stage_timeout_'+stage
            time.sleep(.05)
    paused_background = paused_collector = False
    try:
      docker('pause', background)
      paused_background = True
      docker('pause', collector)
      paused_collector = True
      with (evidence/'runner.log').open('w') as output:
        held_browser = subprocess.Popen([shutil.which('node'), str(root/'node_modules/@playwright/test/cli.js'),
            'test','tests/browser/real-game-feedback.spec.ts','--config=playwright.config.ts','--project=chromium',
            '--grep','packaged held prerequisite preserves Study through essential worker recovery','--trace=on'],
            cwd=root, env=os.environ|dict(TEMPO_DOCKER_URL=web_url,TEMPO_PACKAGED_HOLD_SCENARIO=str(hold_scenario),
                TEMPO_TEST_OUTPUT_DIR=str(evidence/'artifacts'),TEMPO_CI_REPORT=str(evidence/'playwright.json')),
            stdout=output,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        waiting = wait_browser('waiting')
        assert waiting['empty'] is True and waiting['playable'] is False
        empty = api('GET','/api/queue/window?limit=2')
        assert empty['status']==200 and empty['body']['cards']==[], empty
        for control in controls:
            with case.transaction() as database:
                database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type,trained_color)
                SELECT %s,repertoire_id,kind,start_fen,moves_json,'mature',CURRENT_DATE,introduced_at,1,interval_days,
                    fsrs_card_json,first_correct_at,stability,'tactic','white' FROM cards WHERE id=%s""", (control, case.card_id))
        deadline = time.monotonic()+45
        while True:
            policy = api('GET','/api/system/foreground-active')
            if policy['status']==200 and policy['body'].get('available') is True and policy['body'].get('active') is True:
                break
            assert time.monotonic()<deadline, 'held_browser_waiting_activity_not_observed'
            time.sleep(.1)
        assert json.loads(docker('inspect',background).stdout)[0]['State']['Paused'] is True
        settings = api('GET', '/api/settings')
        assert settings['status'] == 200
        (directory/'essential-crash-after-slice').write_text('crash once after genuine committed queue slice\n')
        confirmed(api('PUT', '/api/settings', settings['body'], 'held-queue:' + uuid.uuid4().hex))
        release('controls')
        deadline = time.monotonic() + 45
        while True:
            queue = api('GET', '/api/queue/window?limit=20')
            if queue['status'] == 200 and queue['body'].get('cards') and queue['body']['cards'][0]['id'] in controls:
                card = queue['body']['cards'][0]
                break
            assert time.monotonic() < deadline, 'held_control_not_admitted_by_queue_owner'
            time.sleep(.1)
        source_id = 'held-' + uuid.uuid4().hex[:8]
        game = chess.pgn.Game(); game.setup(chess.Board(target['starting_fen']))
        game.headers.update(White='Learner', Black='Opponent', Result='1-0')
        node = game
        for uci in [*target['prefix'], target['wrong']]:
            node = node.add_variation(chess.Move.from_uci(uci))
        inputs['now'] = datetime.now(timezone.utc).timestamp()
        now = datetime.fromtimestamp(inputs['now'], timezone.utc).replace(microsecond=0)
        inputs['configuration']['revision'] += 1
        inputs['configuration']['window'] = dict(start_at=(now-timedelta(seconds=120)).isoformat(),
            end_at=(now-timedelta(seconds=60)).isoformat(), precision='second')
        inputs['lichess'].append(dict(id=source_id, createdAt=int((inputs['now']-90)*1000), rated=True, speed='rapid', pgn=str(game)))
        publish_provider(inputs, allow_paused=True)
        entry('foreground-worker', 'recovery-configure')
        entry('foreground-worker', 'worker')
        with case.transaction() as database:
            rows = database.execute_native("""SELECT delivery.operation_id,delivery.payload FROM product_feedback_deliveries delivery
                JOIN product_feedback_sources source USING(source_key)
                WHERE source.payload->'original'->>'source_game_id'=%s""", (source_id,)).fetchall()
            assert len(rows) == 1 and rows[0]['payload']['state'] == 'pending'
            operation, original = rows[0]['operation_id'], rows[0]['payload']['encounter']
            graph = database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s', (repertoire,)).fetchone()[0]
        qualified = entry('foreground-worker','qualify-local-feedback',operation)
        assert qualified['qualified'] and qualified['operation_id']==operation
        assert entry('foreground-worker', 'hold-integrity', repertoire)['invalidated']
        prepared = confirmed(api('POST', '/api/queue/prepare-next', {}, 'held-prepare:' + uuid.uuid4().hex))
        assert prepared['pending'] and prepared['blocks_issuance'] is False, prepared
        with case.transaction() as database:
            held = database.execute_native('SELECT payload FROM product_feedback_deliveries WHERE operation_id=%s', (operation,)).fetchone()[0]
            assert held['qualification_status'] == 'held' and held['encounter'] == original
            assert database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE identity_json::jsonb->>'game_id'=%s", (original['key']['game_id'],)).fetchone()[0] == 0
        release('held')
        saved_marker = wait_browser('saved')
        saved = saved_marker['saved']
        assert saved['persisted'] and saved_marker['replay'] == saved
        assert saved_marker['control'] == card['id']
        assert saved_marker['successor'] in controls and saved_marker['successor'] != card['id']
        ordinary = api('GET', '/api/queue/window?limit=2')
        assert ordinary['status'] == 200 and ordinary['body']['cards'][0]['id'] == saved_marker['successor'], ordinary
        day = ordinary['body']['local_date']
        release('go-away')
        wait_browser('away')
        deadline = time.monotonic()+45
        while api('GET','/api/system/foreground-active')['body'].get('active') is not False:
            assert time.monotonic()<deadline, 'held_browser_visibility_lease_not_released'
            time.sleep(.1)
        scan = entry('foreground-worker', 'request-integrity', repertoire, str(graph), day)
        phases = []
        for _ in range(128):
            recovered = entry('foreground-worker', 'step-integrity', scan['task_id'], repertoire,
                str(scan['generation']), day, str(graph))
            phases.extend(recovered['phases'])
            if recovered['complete']:
                break
        else:
            raise AssertionError('held_prerequisite_owner_recovery_exceeded_finite_work')
        # Preserve the original delivery's real retry deadline. Advancing the
        # collection simulation would freeze a future determination that the
        # genuine browser issuance cannot discharge.
        retry_deadline = time.monotonic() + 45
        while datetime.now(timezone.utc).timestamp() < held['next_attempt_at']:
            assert time.monotonic() < retry_deadline, 'held_original_retry_deadline_not_reached'
            time.sleep(.1)
        inputs['now'] = datetime.now(timezone.utc).timestamp()
        publish_provider(inputs, allow_paused=True)
        retry = entry('foreground-worker', 'retry-local-feedback')
        current = entry('api', 'feedback-state', operation)
        assert current['command'] == original and current['receipt'] is not None
        assert current['receipt']['key'] == original['key']
        release('restored')
        final_browser = wait_browser('complete')
        assert held_browser.wait(timeout=5)==0
        settled = entry('api','feedback-state',operation)
        assert settled['command']==original and settled['receipt']['status']=='settled', settled
        assert settled['receipt']['progress']['scheduling_review_id'] is not None
        assert settled['receipt']['progress']['settlement']['review_id']==final_browser['target_review']['review_id']
        entry('foreground-worker','retry-local-feedback')
        assert entry('api','feedback-state',operation)['receipt']==settled['receipt']
        crash = json.loads(docker('exec', foreground, 'cat', '/tmp/tempo-essential-crashed.json').stdout)
        with case.transaction() as database:
            events = [dict(row) for row in database.execute_native("SELECT event,phase,generation FROM background_task_events WHERE task_id=%s AND generation=%s ORDER BY id", (crash['task_id'],crash['generation'])).fetchall()]
            assert any(item['event']=='slice_complete' for item in events) and any(item['event']=='published' for item in events)
            from app.study.game_feedback import effect_id
            from app.contracts.product_v02.learning import GameEffectKey
            effect = effect_id(GameEffectKey.model_validate(original['key']))
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE source_kind='game_lapse' AND source_ref=%s",(effect,)).fetchone()[0]==1
        report['essential_queue_crash_recovery'] = dict(crash=crash, events=events, browser_waiting=policy['body']['active'], optional_worker_paused=True)
        report['held_prerequisite_recovery'] = dict(source_id=source_id, operation_id=operation,
            original=original, held=held, saved=saved, control_card_id=card['id'], browser=final_browser,
            core_queue_after_save=ordinary['status'], owner_scan=scan, owner_phases=phases,
            local_retry=retry, recovered_receipt=current['receipt'], settled_receipt=settled['receipt'], collector_stopped=True,
            classification='synthetic packaged owner restoration and actual browser save; separate 30-transition browser measurements follow')

    finally:
        if held_browser and held_browser.poll() is None:
            release('abort')
            os.killpg(held_browser.pid, signal.SIGTERM)
            try: held_browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(held_browser.pid, signal.SIGKILL); held_browser.wait(timeout=5)
        if paused_collector:
            docker('unpause', collector)
        if paused_background:
            docker('unpause', background)


def run_recovery(case, *, root, directory, report, inputs, entry, api, execute, docker, containers,
                 publish_provider,
                 original_repertoire, custom_target, web_url, study_only, mode_intent):
    """Continue the already verified roles/network/images; no second launcher."""
    # The setup collector switches to the original production admission for
    # its explicit recovery unit. Its image/container/boot identity is unchanged.
    collector = json.loads(docker('inspect', containers['game-feedback-worker']['id']).stdout)[0]
    postgres = json.loads(docker('inspect', report['fixture_container']).stdout)[0]
    assert collector['Config']['Labels']['tempo.owner'] == postgres['Config']['Labels']['tempo.owner']
    assert collector['Config']['Labels']['tempo.disposable'] == 'true' and collector['Image'] == report['image']
    assert collector['State']['Running']
    identity = json.loads(execute('game-feedback-worker', 'python', '-m',
        'app.product_composition.managed_runtime', 'inspect'))['runtime']
    expected = next(item for item in mode_intent['runtimes'] if item['service'] == 'game-feedback-worker')
    assert expected['container_id'] == collector['Id'] and expected['image'] == collector['Image']
    assert identity['hostname'] == expected['hostname'] and identity['boot_nonce'] == expected['boot_nonce']
    report['recovery_producer_identity'] = expected
    scheduler_logs = docker('logs', containers['foreground-worker']['id'])
    scheduler_evidence = [line for line in (scheduler_logs.stdout+'\n'+scheduler_logs.stderr).splitlines()
                          if 'beat: Starting' in line]
    assert scheduler_evidence, 'recovery_actual_essential_scheduler_missing'
    report['essential_scheduler'] = dict(runtime='supported embedded Celery beat in existing foreground worker',
        schedule='/tmp/qualification-celerybeat-schedule', startup=scheduler_evidence[-1],
        memory_limit_mib=384, additional_service=False)
    report['recovery_memory_before_scope'] = {role: memory_observation(docker, resource['id'])
        for role, resource in containers.items()}
    held_prerequisite_journey(case, directory=directory, report=report, inputs=inputs, entry=entry,
        publish_provider=publish_provider,
        api=api, docker=docker, collector=collector['Id'], background=containers['background-worker']['id'],
        foreground=containers['foreground-worker']['id'],
        root=root, web_url=web_url, repertoire=original_repertoire, target=custom_target)
    workload = seed_scope(case, original_repertoire, directory)
    inputs['configuration']['repertoire_ids'] = workload['scope']
    configuration_windows = []
    def configure_window():
        # Fixture source admission is explicit. Each fresh bounded window is
        # committed through the configuration owner, never by forging a job
        # continuation or reopening a completed provider command.
        inputs['configuration']['revision'] += 1
        now = datetime.fromtimestamp(inputs['now'], timezone.utc).replace(microsecond=0)
        inputs['configuration']['window'] = dict(start_at=(now - timedelta(seconds=120)).isoformat(),
            end_at=(now - timedelta(seconds=60)).isoformat(), precision='second')
        publish_provider(inputs)
        configured = entry('foreground-worker', 'recovery-configure')
        assert configured['selected_repertoire_count'] == 10
        configuration_windows.append(dict(revision=inputs['configuration']['revision'],
            window=inputs['configuration']['window'], controlled_source_admission=True))
    inputs['now'] = datetime.now(timezone.utc).timestamp()
    configure_window()
    settings = api('GET', '/api/settings')
    assert settings['status'] == 200
    value = settings['body'] | dict(study_only_mode=study_only)
    changed = api('PUT', '/api/settings', value, 'recovery-settings:' + uuid.uuid4().hex)
    assert changed['status'] in (200, 202)
    if changed['status'] == 202:
        operation = changed['body']['operation_id']
        deadline = time.monotonic() + 45
        while True:
            state = api('GET', '/api/operations/' + operation)
            if state['body'].get('state') == 'complete':
                break
            assert time.monotonic() < deadline, 'recovery_mode_command_not_complete'
            time.sleep(.05)
    (directory / 'recovery-configuration-ready').write_text('current scope and mode committed\n')
    deadline = time.monotonic() + 45
    while True:
        initial = api('GET', '/api/queue/window?limit=20')
        if initial['status'] == 200 and initial['body'].get('cards'):
            break
        assert time.monotonic() < deadline, 'recovery_due_intake_not_ready'
        time.sleep(.1)
    scenario = directory / 'recovery-scenario.json'
    scenario.write_text(json.dumps(dict(version=1, study_only=study_only)))
    evidence = root / 'test-results/collection-release' / ('recovery-' + workload['namespace'])
    evidence.mkdir(parents=True, exist_ok=True)
    node, cli = shutil.which('node'), root / 'node_modules/@playwright/test/cli.js'
    assert node and cli.is_file()
    browser = None
    staged, transitions, background = [], [], []
    producer_progress = {}
    with case.transaction() as database:
        event_boundary = database.execute_native('SELECT COALESCE(MAX(id),0) FROM background_task_events').fetchone()[0]
    marker = directory / 'recovery-browser-ready.json'
    release = directory / 'recovery-provider-ready.json'
    def wait_for(stage, index):
        deadline = time.monotonic() + 45
        while True:
            if marker.exists():
                found = json.loads(marker.read_text())
                if found.get('stage') == stage and found.get('index') == index:
                    return found
            assert browser.poll() is None, 'recovery_browser_failed_before_' + stage
            assert time.monotonic() < deadline, 'recovery_browser_marker_timeout_' + stage
            time.sleep(.02)
    try:
        with (evidence / 'runner.log').open('w') as output:
            browser = subprocess.Popen([node, str(cli), 'test', 'tests/browser/study-recovery.spec.ts',
                '--config=playwright.config.ts', '--project=chromium', '--grep',
                'packaged recovery records thirty authoritative playable transitions', '--trace=on'], cwd=root,
                env=os.environ | dict(TEMPO_DOCKER_URL=web_url, TEMPO_STUDY_RECOVERY_SCENARIO=str(scenario),
                    TEMPO_CI_REPORT=str(evidence / 'playwright.json'), TEMPO_TEST_OUTPUT_DIR=str(evidence / 'artifacts')),
                stdout=output, stderr=subprocess.STDOUT, text=True, start_new_session=True)
            for index in range(30):
                current = wait_for('before-review', index)
                if index == 7:
                    _marker(release, dict(stage='continue', index=index, background=True))
                    wait_for('foreground-away', index)
                    quiet_deadline = time.monotonic() + 45
                    while True:
                        policy = api('GET', '/api/system/foreground-active')
                        if (policy['status'] == 200 and policy['body'].get('available') is True
                                and policy['body'].get('active') is False
                                and policy['body'].get('study_only_mode') is study_only):
                            break
                        assert time.monotonic() < quiet_deadline, 'recovery_genuine_visibility_did_not_release_foreground'
                        time.sleep(.05)
                    gap_id = 'g' + uuid.uuid4().hex[:7]
                    inputs['now'] = datetime.now(timezone.utc).timestamp()
                    inputs['lichess'].append(dict(id=gap_id, createdAt=int((inputs['now'] - 90) * 1000),
                        rated=True, speed='rapid', pgn='[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 c5 1-0\n'))
                    configure_window()
                    (directory / 'recovery-producer-start').write_text('original admission only\n')
                    producer_deadline = time.monotonic() + 45
                    while True:
                        logged = docker('logs', collector['Id'])
                        producer_log = logged.stdout + '\n' + logged.stderr
                        if '"recovery_actual_worker_finished": true' in producer_log:
                            break
                        assert time.monotonic() < producer_deadline, 'recovery_actual_producer_unit_did_not_finish'
                        time.sleep(.05)
                    grants = producer_log.count('"recovery_actual_admission_granted": true')
                    with case.transaction() as database:
                        produced = database.execute_native("""SELECT source_key,payload->'first_departure' AS selection
                            FROM product_feedback_sources WHERE payload->'original'->>'source_game_id'=%s""", (gap_id,)).fetchall()
                        jobs = [dict(row) for row in database.execute_native("""SELECT job_id,version,status,
                            payload->>'pages_committed' AS pages_committed,payload->>'continuation' AS continuation,
                            payload->'spec'->>'stream' AS stream FROM product_feedback_jobs
                            WHERE payload->'spec'->>'configuration_revision'=%s ORDER BY job_id LIMIT 100""",
                            (str(inputs['configuration']['revision']),)).fetchall()]
                    if not study_only:
                        assert grants > 0 and len(produced) == 1, 'recovery_optional_producer_did_not_compute_real_work'
                        assert produced[0]['selection']['status'] == 'opponent_gap'
                    else:
                        assert (len(produced) == 1 and grants > 0) or (not produced and grants == 0)
                    producer_progress = dict(control=policy['body'], original_admission_grants=grants,
                        source_id=gap_id, source_rows=[dict(row) for row in produced],
                        optional_paused=not produced, runtime=expected,
                        jobs=jobs,
                        classification='actual production admission with controlled HTTP/clock; visibility pause is outside review metrics')
                    _marker(release, dict(stage='continue', index=index + 200))
                if index in (4, 14, 24):
                    target = workload['standard'] if index in (14, 24) else custom_target
                    source_id = 'r' + uuid.uuid4().hex[:7]
                    game = chess.pgn.Game()
                    game.setup(chess.Board(target['starting_fen']))
                    game.headers.update(White='Learner', Black='Opponent', Result='1-0')
                    game_node = game
                    for uci in [*target['prefix'], target['wrong']]:
                        game_node = game_node.add_variation(chess.Move.from_uci(uci))
                    inputs['now'] = datetime.now(timezone.utc).timestamp()
                    inputs['lichess'].append(dict(id=source_id, createdAt=int((inputs['now'] - 90) * 1000),
                        rated=True, speed='rapid', pgn=str(game)))
                    configure_window()
                    # Real provider parsing/capture/comparison. This is explicit
                    # controlled fixture admission, not a claim that recurring
                    # optional collection is permitted in Study-only mode.
                    producer_role = 'background-worker'
                    producer = json.loads(docker('inspect', containers[producer_role]['id']).stdout)[0]
                    assert producer['Id']==containers[producer_role]['id']
                    assert producer['State']['Running'] and not producer['State']['Paused']
                    assert producer['HostConfig']['Memory']==448*1024*1024
                    captured = entry(producer_role, 'worker')
                    with case.transaction() as database:
                        source = database.execute_native("""SELECT source_key,payload->'first_departure' AS selection
                            FROM product_feedback_sources WHERE payload->'original'->>'source_game_id'=%s""", (source_id,)).fetchone()
                        assert source and source['selection']['status'] == 'own_actionable'
                        assert source['selection']['decision']['presentation_id'] == target['card_id']
                        deliveries = database.execute_native("""SELECT operation_id,payload FROM product_feedback_deliveries
                            WHERE source_key=%s AND state='pending'""", (source['source_key'],)).fetchall()
                        assert len(deliveries) == 1
                        staged.append(dict(index=index, source_id=source_id, target=target,
                            operation_id=deliveries[0]['operation_id'], command=deliveries[0]['payload'].get('encounter'),
                            provider_run=captured, execution_role=producer_role, memory_bytes=producer['HostConfig']['Memory'],
                            admission='explicit synthetic fixture admission'))
                if index != 7:
                    _marker(release, dict(stage='continue', index=index))
                measured = wait_for('transition', index)['sample']
                with case.transaction() as database:
                    receipt = database.execute_native("""SELECT result_json,review_id,card_id,queue_entry_id FROM review_attempt_receipts
                        WHERE attempt_id=%s""", (measured['attemptId'],)).fetchone()
                    # The exact persisted review and distinct issued origin are
                    # read AFTER the browser clock endpoint, so this inspection
                    # cannot make an unissued board pass the measured boundary.
                    assert receipt is not None
                    operation = database.execute_native("""SELECT command_name,state,response_json,payload_json
                        FROM operation_receipts WHERE operation_id=%s""", (measured['requestId'],)).fetchone()
                    assert operation and operation['command_name']=='cards.review' and operation['state']=='complete'
                    assert json.loads(operation['response_json']) == measured['receipt']
                    request = json.loads(operation['payload_json'])
                    assert request['card_id']==measured['cardId']
                    assert request['review']['attempt_id']==measured['attemptId']
                    assert request['review']['queue_entry_id']==measured['entryId']
                    # The compatibility adapter appends only this owner result
                    # after Learning stores its attempt receipt. The complete
                    # command receipt above must retain the exact public JSON.
                    core_receipt = dict(measured['receipt'])
                    core_receipt.pop('recombination', None)
                    assert json.loads(receipt['result_json']) == core_receipt
                    assert receipt['card_id'] == measured['cardId'] and receipt['queue_entry_id'] == measured['entryId']
                    assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE id=%s AND card_id=%s AND source_kind='study'",
                        (receipt['review_id'], measured['cardId'])).fetchone()[0] == 1
                    origins = database.execute_native("""SELECT queue_entry_id,card_id,revision,issuance_sequence,issued_at
                        FROM queue_attempt_origins WHERE queue_entry_id=ANY(%s) AND issued_as_head=1""",
                        ([measured['entryId'], measured['nextEntryId']],)).fetchall()
                    assert len(origins) == 2 and all(row['issuance_sequence'] is not None for row in origins)
                    by_id = {row['queue_entry_id']: dict(row) for row in origins}
                    assert by_id[measured['entryId']]['card_id'] == measured['cardId']
                    assert by_id[measured['nextEntryId']]['issuance_sequence'] > by_id[measured['entryId']]['issuance_sequence']
                    assert by_id[measured['nextEntryId']]['card_id'] == measured['nextCardId']
                    next_card = database.execute_native('SELECT start_fen,moves_json,trained_color FROM cards WHERE id=%s',
                        (measured['nextCardId'],)).fetchone()
                    board = chess.Board(next_card['start_fen'])
                    for uci in json.loads(next_card['moves_json']):
                        if board.turn == (next_card['trained_color'] == 'white'):
                            break
                        board.push_uci(uci)
                    assert ' '.join(measured['nextFen'].split()[:4]) == ' '.join(board.fen().split()[:4])
                    transitions.append(dict(index=index, sample=measured, origins=by_id))
                    tasks = database.execute_native("""SELECT id,generation,state,phase,payload_json
                        FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'""").fetchall()
                    background.append(dict(index=index, tasks=[dict(row) for row in tasks]))
                _marker(release, dict(stage='continue', index=index + 100))
            assert browser.wait(timeout=30) == 0, 'recovery_browser_acceptance_failed'
        measurements = json.loads((directory / 'recovery-measurements.json').read_text())
        assert measurements['study_only'] is study_only and len(measurements['samples']) == 30
        assert measurements['preparationTransitions'] >= 3
        assert any(item['target']['starting_fen'] == chess.STARTING_FEN for item in staged)
        assert any(not item['receipt'].get('prepared_continuation') for item in measurements['samples'])
        # The normal command worker is unmodified. Count actual expensive local
        # handoff calls from its bounded owner diagnostics, not just effects.
        logged = docker('logs', containers['foreground-worker']['id'])
        diagnostics = []
        for line in (logged.stdout + '\n' + logged.stderr).splitlines():
            if 'study_feedback_diagnostic ' in line:
                try:
                    diagnostics.append(json.loads(line.split('study_feedback_diagnostic ', 1)[1]))
                except json.JSONDecodeError:
                    pass
        for item in staged:
            token = hashlib.sha256(item['operation_id'].encode()).hexdigest()[:16]
            calls = [record for record in diagnostics if record.get('operation') == 'prepare_delivery_feedback'
                     and record.get('work_token') == token]
            assert len(calls) == 1, 'recovery_local_preparer_start_count_not_one'
            item['actual_local_preparer_calls'] = calls
        with case.transaction() as database:
            progress = [dict(row) for row in database.execute_native("""SELECT event.id,event.task_id,event.generation,event.event,event.phase
                FROM background_task_events event JOIN background_tasks task ON task.id=event.task_id
                WHERE event.id>%s AND task.kind='daily_queue' ORDER BY event.id LIMIT 2000""", (event_boundary,)).fetchall()]
            assert any(row['event'] == 'claimed' for row in progress), 'recovery_background_never_claimed_permitted_work'
            assert any(row['event'] in ('slice_complete', 'published') for row in progress), 'recovery_background_made_no_useful_progress'
        # Each injected occurrence must survive into a real retained effect;
        # the following measured human-equivalent synthetic UI review settles it.
        for item in staged:
            status = entry('api', 'feedback-state', item['operation_id'])
            assert status['receipt']['status'] == 'settled'
            assert status['command'] == item['command']
            item['settled_receipt'] = status['receipt']
        report['study_recovery'] = dict(first_sample_context='warm: the preceding packaged browser journeys already used this candidate; initial browser acquisition is recorded separately',
            cpu_observation_interval='cgroup CPU counters before scope/held case and after the complete acceptance; not exclusive per-review CPU', mode=study_only, workload=workload, measurements=measurements,
            transitions=transitions, background=background, staged=staged,
            background_events=progress,
            actual_managed_producer=producer_progress, controlled_configuration_windows=configuration_windows,
            baseline='same-candidate same-fixture no-pending-work save samples before first staged occurrence; all samples retained',
            pre_run_setup='existing packaged provider/protected-review/target-settlement journey precedes the 30 samples; full observed scope is seeded afterwards; initial page/queue acquisition is setup, and no measured transition is dropped',
            save_latency_comparison=dict(control_ms=[row['saveMs'] for row in measurements['samples'][:4]],
                pending_work_ms=[measurements['samples'][index]['saveMs'] for index in (4, 14, 24)],
                qualification='review this comparison before accepting; no pre-repair same-fixture save baseline is implied'))
    except BaseException as error:
        report['study_recovery_failure'] = dict(error=type(error).__name__,
            completed_authoritative_transitions=len(transitions), staged_occurrences=len(staged))
        raise
    finally:
        if browser and browser.poll() is None:
            _marker(release, dict(stage='abort'))
            os.killpg(browser.pid, signal.SIGTERM)
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(browser.pid, signal.SIGKILL)
                browser.wait(timeout=5)
        for name in ('recovery-browser-trace.json', 'recovery-measurements.json', 'recovery-workload.json'):
            path = directory / name
            if path.exists():
                shutil.copyfile(path, evidence / name)
        (evidence / 'owner-readbacks.json').write_text(json.dumps(dict(transitions=transitions, staged=staged,
            background=background, browser_exit=browser.returncode if browser else None), indent=2, default=str))
