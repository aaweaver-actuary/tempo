"""Coordinate genuine native transactions without substituting their results."""
import json, os, pathlib, sys, threading, time, signal
ROOT = pathlib.Path.cwd()
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'backend')]
import httpx, psycopg
import check_postgres_study_priority as proof
DIRECTORY = pathlib.Path(os.environ['TEMPO_SATURATION_DIRECTORY'])
original_client = httpx.Client
state = {'reviews': [], 'response_loss_replays': [], 'extra_background_sections': 0}
stopping = threading.Event()
background = None
owned_resources = []
original_docker = proof.docker

def owned_docker(*arguments, **options):
    result = original_docker(*arguments, **options)
    if arguments and arguments[0] == 'run' and '--name' in arguments:
        name = arguments[arguments.index('--name')+1]
        details = json.loads(original_docker('inspect', name))[0]
        owned_resources.append({'name': name, 'id': details['Id'], 'owner': details['Config']['Labels']['tempo.owner'],
            'cpu': details['HostConfig']['NanoCpus']/1e9, 'memory_bytes': details['HostConfig']['Memory'],
            'volumes': [mount['Name'] for mount in details['Mounts'] if mount['Type']=='volume']})
    return result

proof.docker = owned_docker

def publish(name, value):
    temporary = DIRECTORY/(name+'.partial')
    temporary.write_text(json.dumps(value))
    temporary.replace(DIRECTORY/(name+'.json'))

def wait(name, seconds=180):
    deadline = time.monotonic()+seconds
    while not (DIRECTORY/(name+'.json')).exists():
        if (DIRECTORY/'abort.json').exists(): raise RuntimeError('Overlap coordinator aborted')
        if time.monotonic() >= deadline: raise TimeoutError(name)
        time.sleep(.02)

def optional_sections():
    from app.database import background_connection
    from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
    from app.services import queue_application
    from psycopg.errors import TransactionTimeout
    while not stopping.is_set():
        try:
            with activity_gate.background_job('study_priority_saturation', 'owned-overlap', yielding=True):
                with background_connection() as database:
                    row = database.execute("SELECT payload_json FROM background_tasks WHERE id='saturation-unlock-checkpoint' AND generation=1 AND lease_token='saturation-proof' AND state='leased' FOR UPDATE").fetchone()
                    assert row is not None
                    payload = json.loads(row[0])
                    cursor = queue_application._unlock_eligible_opening_cards(database, payload['queue_date'], after_card_id=payload.get('after_card_id',''), batch_size=8)
                    payload['after_card_id'] = cursor or ''
                    payload['complete'] = cursor is None
                    if cursor is None: payload['completed_passes'] = payload.get('completed_passes', 0)+1
                    assert database.execute("UPDATE background_tasks SET payload_json=? WHERE id='saturation-unlock-checkpoint' AND generation=1 AND lease_token='saturation-proof' AND state='leased'", (json.dumps(payload),)).rowcount == 1
                state['extra_background_sections'] += 1
                state.setdefault('maintenance_cursors', []).append(cursor)
            if cursor is None:
                state['maintenance_complete'] = True
                stopping.wait(1)
                continue
            stopping.wait(.1)  # Yield outside every short committed section.
        except (BackgroundAdmissionDeferred, TransactionTimeout) as error:
            state['maintenance_deferrals'] = state.get('maintenance_deferrals', 0)+1
            stopping.wait(.05)
        except Exception as error:
            state['background_error'] = str(error)
            return

def study_fingerprints(database):
    from psycopg import sql
    tables = ['cards', 'reviews', 'daily_queue', 'review_attempt_receipts', 'queue_attempt_origins',
              'tactic_introductions', 'guided_review_attempts', 'study_attempts', 'defense_attempts', 'endgame_attempts',
              'opening_evidence_attempts', 'opening_evidence_clean_days', 'opening_evidence_events',
              'opening_evidence_observations', 'opening_evidence_presentations', 'opening_evidence_queue_contexts',
              'opening_evidence_summaries', 'operation_receipts']
    result = {}
    for table in tables:
        predicate = sql.SQL(" WHERE operation_id=ANY(%s)") if table == 'operation_receipts' else sql.SQL('')
        statement = sql.SQL("SELECT count(*),md5(COALESCE(string_agg(encoded,',' ORDER BY encoded),'')) FROM (SELECT row_to_json(item)::text encoded FROM {} item{}) snapshot").format(sql.Identifier(table), predicate)
        parameters = (state['reviews'],) if table == 'operation_receipts' else ()
        count, digest = database.execute(statement, parameters).fetchone()
        result[table] = {'rows': count, 'md5': digest}
    return result

original_read_text = pathlib.Path.read_text

def stop_maintenance_before_rollback(path, *arguments, **options):
    if path.name == '049_study_priority_recovery.sql' and path.parent.name == 'rollbacks':
        stopping.set()
        if background:
            background.join(timeout=2)
            assert not background.is_alive(), 'Maintenance writer did not stop before rollback'
        state['maintenance_stopped_before_rollback'] = True
        state['maintenance_after_ui_quiet'] = state['extra_background_sections']
        with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as database:
            state['study_fingerprints_before_rollback'] = study_fingerprints(database)
            state['checkpoint_before_rollback'] = database.execute("SELECT payload_json FROM background_tasks WHERE id='saturation-unlock-checkpoint'").fetchone()[0]
    return original_read_text(path, *arguments, **options)

pathlib.Path.read_text = stop_maintenance_before_rollback

original_thread_start = threading.Thread.start

def start_after_ui_quiet(thread):
    if getattr(getattr(thread, '_target', None), '__name__', '') == 'background_load':
        wait('ui-inputs-complete', 180)
    return original_thread_start(thread)

threading.Thread.start = start_after_ui_quiet

class CoordinatedClient(original_client):
    initialized = False
    aliases = {}
    def request(self, method, url, **options):
        global background
        if method.upper() == 'GET' and str(url).startswith('/api/queue/window') and len(state['reviews']) == 59:
            wait('ui-inputs-complete', 90)
            state['maintenance_after_ui_quiet'] = state['extra_background_sections']
        review = method.upper() == 'POST' and '/review' in str(url)
        request_identity = options.get('headers', {}).get('Idempotency-Key') if review else None
        if review and not state['reviews'] and request_identity not in self.aliases:
            publish('ui-review-request', {'path': str(url), 'body': options['json'], 'identity': request_identity})
            wait('ui-review-response', 60)
            delivered = json.loads((DIRECTORY/'ui-review-response.json').read_text())
            assert delivered['status'] == 200 and delivered['response']['persisted']
            self.aliases[request_identity] = {'json': delivered['request_body'],
                'headers': {'Idempotency-Key': delivered['request_identity']}}
            options = self.aliases[request_identity]
            response = httpx.Response(delivered['status'], json=delivered['response'], headers=delivered['response_headers'])
            state['actual_ui_original_identity'] = delivered['request_identity']
        else:
            if request_identity in self.aliases:
                options = self.aliases[request_identity]
            response = super().request(method, url, **options)
        if not self.initialized and method.upper() == 'GET' and str(url).startswith('/api/queue/window') and response.status_code == 200:
            self.initialized = True
            with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as database:
                day = __import__('datetime').date.today().isoformat()
                database.execute("INSERT INTO background_tasks(id,kind,deduplication_key,state,phase,payload_json,next_attempt_at,created_at,updated_at,lease_token) VALUES('saturation-unlock-checkpoint','daily_queue','saturation-unlock','leased','unlock_opening',%s,%s,%s,%s,'saturation-proof')", (json.dumps({'queue_date':day,'_queue_phase':'unlock_opening'}),day,day,day))
            background = threading.Thread(target=optional_sections, daemon=True)
            background.start()
            publish('native-ready', {'origin': str(self.base_url).rstrip('/'), 'pid': os.getpid(), 'resources': owned_resources})
            wait('browser-ready')
            state['maintenance_before_ui_input'] = state['extra_background_sections']
        if review and response.status_code == 200:
            request_identity = options['headers']['Idempotency-Key']
            if request_identity not in state['reviews']:
                state['reviews'].append(request_identity)
                if len(state['reviews']) % 10 == 1:
                    accepted = response.json()
                    response = super().request(method, url, **options)
                    assert response.status_code == 200 and response.json() == accepted
                    state['response_loss_replays'].append(request_identity)
        return response
    def __exit__(self, *arguments):
        if any(arguments):
            stopping.set()
            if background:
                background.join(timeout=2)
                assert not background.is_alive(), 'Maintenance did not stop before exception unwound database configuration'
        if self.initialized and not any(arguments) and not (DIRECTORY/'abort.json').exists():
            with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as database:
                state['reconciliation'] = {
                    'reviews': database.execute("SELECT count(*) FROM reviews WHERE card_id LIKE 'review-%'").fetchone()[0],
                    'remaining_queue': database.execute("SELECT count(*) FROM daily_queue WHERE card_id LIKE 'review-%'").fetchone()[0],
                    'review_receipts': database.execute("SELECT count(*) FROM operation_receipts WHERE operation_id=ANY(%s) AND state='complete'", (state['reviews'],)).fetchone()[0],
                    'reviewed_cards': database.execute("SELECT count(DISTINCT card_id) FROM reviews WHERE card_id LIKE 'review-%'").fetchone()[0],
                }
                state['receipts'] = [row[0] for row in database.execute('SELECT to_jsonb(receipt) FROM operation_receipts receipt WHERE operation_id=ANY(%s) ORDER BY operation_id', (state['reviews'],))]
                state['tables'] = [row[0] for row in database.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE '%attempt%' OR table_name LIKE '%evidence%' OR table_name LIKE '%introduction%') ORDER BY table_name")]
                state['durable_counts'] = {}
                from psycopg import sql
                for table in state['tables']:
                    state['durable_counts'][table] = database.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
                state['study_fingerprints_after_rollback'] = study_fingerprints(database)
                assert state['study_fingerprints_after_rollback'] == state['study_fingerprints_before_rollback']
                state['queue_by_status'] = {row[0]: row[1] for row in database.execute("SELECT status,count(*) FROM daily_queue WHERE card_id LIKE 'review-%' GROUP BY status ORDER BY status")}
                state['final_study_rows'] = {}
                for table, predicate in [('cards', "id LIKE 'review-%'"), ('reviews', "card_id LIKE 'review-%'"), ('daily_queue', "card_id LIKE 'review-%'"), ('queue_attempt_origins', "card_id LIKE 'review-%'")]:
                    state['final_study_rows'][table] = [row[0] for row in database.execute(sql.SQL('SELECT to_jsonb(item) FROM {} item WHERE {}').format(sql.Identifier(table), sql.SQL(predicate)))]
                checkpoint = database.execute("SELECT payload_json FROM background_tasks WHERE id='saturation-unlock-checkpoint'").fetchone()[0]
                assert checkpoint == state['checkpoint_before_rollback']
                state['checkpoint_after_rollback'] = checkpoint
            assert 'background_error' not in state, state
            assert state['extra_background_sections'] > 0
            assert state['maintenance_after_ui_quiet'] > state['maintenance_before_ui_input']
            assert state['maintenance_stopped_before_rollback']
            assert state['reconciliation']['reviews'] == 60
            assert state['reconciliation']['review_receipts'] == 60
            assert state['reconciliation']['reviewed_cards'] == 60
            assert len(state['reviews']) == 60
            publish('native-reconciled', state)
            wait('browser-done', 300)
        return super().__exit__(*arguments)

def interrupted(signum, frame):
    raise SystemExit('Owned saturation interrupted')
signal.signal(signal.SIGTERM, interrupted)
httpx.Client = CoordinatedClient
try:
    proof.main()
finally:
    stopping.set()
    if background: background.join(timeout=2)
    publish('native-overlap', state)
