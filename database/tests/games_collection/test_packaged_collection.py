"""Single pinned packaged collection acceptance using the existing PG fixture."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import importlib.util
import hashlib
import inspect
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import shlex
import time
import uuid

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

# Reuse the existing disposable setup owner; no second migration/cleanup authority.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend/tests'))
from test_product_game_lapse_postgres import ROOT, owned_postgres, case, retained_owner_case


def _candidate_image():
    image = os.environ.get('TEMPO_COLLECTION_TEST_IMAGE')
    assert image, 'Set TEMPO_COLLECTION_TEST_IMAGE to the immutable qualified candidate image ID'
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    revision = os.environ.get('TEMPO_COLLECTION_TEST_REVISION', head)
    assert len(revision) == 40 and all(c in '0123456789abcdef' for c in revision)
    # Reuse this immutable image only when every production COPY input is exact.
    # Operator, external harness and documentation changes do not relabel it.
    dockerfile = (ROOT / 'backend/Dockerfile').read_text()
    inputs = ['backend/Dockerfile']
    for line in dockerfile.splitlines():
        if line.startswith('COPY '):
            parts = shlex.split(line)
            assert not parts[1].startswith(('--', '[')), 'Unsupported COPY form needs explicit qualification input binding'
            inputs.extend(parts[1:-1])
    subprocess.run(['git', 'diff', '--exit-code', revision, '--', *inputs], cwd=ROOT, check=True, capture_output=True)
    assert not subprocess.check_output(['git','ls-files','--others','--exclude-standard','--',*inputs],cwd=ROOT,text=True).strip()

    inspected = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    assert image == inspected['Id'] and inspected['Config']['Labels']['org.opencontainers.image.revision'] == revision
    return image, revision


def test_packaged_collection_retains_deviations_reconciles_and_never_delivers(case, tmp_path):
    image, revision = _candidate_image()
    repertoire, captured, _, _ = retained_owner_case(case, False)
    from app.games.feedback.review import SyncConfiguration
    from app.product_composition.game_review import configured_job
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore
    from app.queue_commands import issue_queue_head
    from app.contracts.transport.study import ReviewRequest
    from app.services.learning_review_adapter import apply_legacy_review
    from tempo_legacy_database.adapters.learning_bridge import review as review_storage
    from types import SimpleNamespace
    from unittest.mock import patch
    @contextmanager
    def connections():
        with case.transaction() as db: yield db.raw
    with connections() as db:
        info = db.info
        admin_dsn = make_conninfo(info.dsn, password=info.password)
        port = info.port
        database_name = info.dbname
    manifests = [json.loads(p.read_text()) for p in (ROOT/'test-results/game-feedback-native').glob('*/fixture.json')]
    matches = [m for m in manifests if m.get('port') == port and not m.get('cleanup_verified')]
    assert len(matches) == 1
    pg = json.loads(subprocess.check_output(['docker', 'inspect', matches[0]['container_id']], text=True))[0]
    assert pg['Config']['Labels']['tempo.owner'] == matches[0]['owner'] and pg['Config']['Labels']['tempo.disposable'] == 'true'
    module = importlib.util.spec_from_file_location('roles', ROOT/'database/tooling/provision_postgres_roles.py')
    roles = importlib.util.module_from_spec(module); module.loader.exec_module(roles)
    reader_password, writer_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    roles.provision(admin_dsn, reader_password, writer_password)
    secret = tmp_path/'container.env'
    secret.write_text('\n'.join([
        'TEMPO_PRODUCT_GAME_SYNC_ENABLED=1',
        'TEMPO_DATABASE_WRITE_URL='+make_conninfo(host='127.0.0.1',port=5432,dbname=database_name,user='tempo_writer',password=writer_password),
        'TEMPO_DATABASE_READ_URL='+make_conninfo(host='127.0.0.1',port=5432,dbname=database_name,user='tempo_reader',password=reader_password)]))
    secret.chmod(0o600)
    directory = tmp_path/'qualification'; directory.mkdir()
    import shutil
    shutil.copyfile(ROOT/'backend/tests/fixtures/product_collection_packaged.py', directory/'entry.py')
    stamp = lambda value: int(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp())
    now = stamp('2026-10-29T12:00:00Z')
    white_pgn = '[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n[SetUp "1"]\n[FEN "'+captured['game']['start_fen']+'"]\n\n1. d4 1-0\n'
    black_miss = '[White "Opponent"]\n[Black "Learner"]\n[Result "1-0"]\n\n1. e4 c5 1-0\n'
    opponent_gap = black_miss.replace('e4 c5', 'd4 d5')
    black_repertoire = 'black-source'
    def publish_black():
        with connections() as db:
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Black source','fixture','2026-09-01')", (black_repertoire,))
            db.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('black-route',%s,'Black route','black',%s,%s,'2026-09-01')", (black_repertoire, 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', json.dumps(['e2e4','e7e5','g1f3','b8c6'])))
            db.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready','2026-09-01')", (black_repertoire,))
    review = PostgresReviewStore(connections)
    configs = []
    for provider in ('lichess','chesscom'):
        config = SyncConfiguration(workspace_id='legacy',provider=provider,account='Learner',
            window=dict(start_at='2026-09-30T12:00:00Z',end_at='2026-10-01T12:00:00Z',precision='second'),
            repertoire_ids=(repertoire,black_repertoire),speeds=('rapid',),rated=True,interval_seconds=60,revision=1)
        review.configure(config, 0); configs.append(config)
    inputs = dict(now=now, lichess=[], chesscom={'09':[], '10':[]})
    for index in range(410):
        text = white_pgn if index % 3 == 0 else black_miss if index % 3 == 1 else opponent_gap
        when = stamp('2026-09-30T18:00:00Z') if index < 205 else stamp('2026-10-01T06:00:00Z')
        month = '09' if index < 205 else '10'
        inputs['lichess'].append(dict(id=f'g{index:05d}',createdAt=when*1000+index,rated=True,speed='rapid',pgn=text))
        inputs['chesscom'][month].append(dict(uuid=f'c{index:05d}',url='https://www.chess.com/game/live/123',
            pgn=text,end_time=when,rules='chess',time_class='rapid',time_control='600',rated=True))
    # Real ordinary Study owns its issued attempt and review, independent of collection.
    queue_day = date.today().isoformat()
    with case.transaction() as db:
        entry = db.execute_native("INSERT INTO daily_queue(queue_date,card_id,position,cycle,admission_kind) VALUES(%s,%s,0,0,'review') RETURNING id", (queue_day,case.card_id)).fetchone()[0]
        assert issue_queue_head(db, dict(queue_date=queue_day,entry_id=entry,card_id=case.card_id,expected_revision=1))['issued']
    protected_tables = ('cards','reviews','daily_queue','queue_attempt_origins','study_attempts','study_game_feedback','review_attempt_receipts')
    def study_rows():
        with connections() as db:
            return {table: [row[0] for row in db.execute('SELECT to_jsonb(t) FROM '+table+' t ORDER BY to_jsonb(t)::text')] for table in protected_tables}
    before = study_rows()
    receipts = []
    def run(mode):
        (directory/'provider.json').write_text(json.dumps(inputs))
        name = 'tempo-g-collection-'+uuid.uuid4().hex
        args = ['docker','run','--rm','--name',name,'--label','tempo.owner='+matches[0]['owner'],
            '--label','tempo.disposable=true','--read-only','--tmpfs','/tmp:rw,size=32m','--memory','256m','--pids-limit','96',
            '--network','container:'+pg['Id'],'--env-file',str(secret),'--mount','type=bind,src='+str(directory)+',dst=/qualification,readonly',
            image,'python','-B','-c',"exec(compile(open('/qualification/entry.py').read(), '/qualification/entry.py', 'exec'))",mode]
        try:
            result = subprocess.run(args,capture_output=True,text=True,timeout=180)
            receipts.append(dict(mode=mode,exit=result.returncode,stdout=result.stdout,stderr=result.stderr))
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout.strip().splitlines()[-1])
        finally:
            found = subprocess.run(['docker','inspect',name],capture_output=True,text=True)
            if found.returncode == 0:
                container = json.loads(found.stdout)[0]
                assert container['Config']['Labels']['tempo.owner'] == matches[0]['owner']
                assert container['Config']['Labels']['tempo.disposable'] == 'true' and container['Image'] == image
                subprocess.run(['docker','rm','-f',container['Id']],check=True,capture_output=True,text=True)
            absent = subprocess.run(['docker','inspect',name],capture_output=True,text=True)
            assert absent.returncode != 0 and 'no such' in absent.stderr.lower()

    try:
        # Black repertoire is initially absent: actual retained sources must say unresolved.
        run('worker')
        store = PostgresCanonicalGamesStore(connections)
        # A pre-existing uncertain attempt must be preserved byte-for-byte.
        pending = store.pending_delivery(now)
        assert pending is not None
        store.save_delivery(pending.model_copy(update=dict(attempts=3, next_attempt_at=now+99999,
            error='synthetic_uncertain_acknowledgment')))
        with connections() as db:
            sources = [r[0] for r in db.execute('SELECT payload FROM product_feedback_sources')]
            assert len(sources) == 820 and any(s['diagnostic'] for s in sources)
            original_deliveries = [row[0] for row in db.execute('SELECT to_jsonb(t) FROM product_feedback_deliveries t ORDER BY operation_id')]
        assert study_rows() == before
        publish_black()
        # Older games become visible after forward coverage passed their time.
        late = dict(inputs['lichess'][0], id='latevisible')
        inputs['lichess'].append(late)
        inputs['lichess'].sort(key=lambda row: row['createdAt'])
        inputs['chesscom']['09'].append(dict(inputs['chesscom']['09'][0], uuid='latevisible'))
        review.enqueue(configs[0], configured_job(configs[0], 'manual-interleaved'), now+1)
        inputs['now'] += 601
        run('worker')  # Fresh packaged process: the actual reconciler revisits the same games.
        with connections() as db:
            sources = [r[0] for r in db.execute('SELECT payload FROM product_feedback_sources')]
            assert len(sources) == 822 and all(s['diagnostic'] is None for s in sources)
            assert sum(s['original']['source_game_id'] == 'latevisible' for s in sources) == 2
            streams = review.continuous_state('legacy', inputs['now'])
            assert len(streams) == 2
            for state in streams:
                assert state.forward_job.spec.window.start_at == '2026-10-01T12:00:00+00:00'
                assert state.reconciliation_job.spec.window.start_at == '2026-09-30T12:00:00+00:00'
                assert state.reconciliation_job.window_complete
            assert store.load_job('manual-interleaved').status == 'complete'
            findings = [f for s in sources for f in s['findings']]
            assert any(f['repertoire_id'] == black_repertoire and f['expected_uci'] == ['e7e5'] and f['played_uci'] == 'c7c5' for f in findings)
            assert any(f['kind'] == 'opponent_gap' for f in findings)
            assert any(f['repertoire_id'] == repertoire for f in findings)
            for row in original_deliveries:
                saved = db.execute('SELECT to_jsonb(t) FROM product_feedback_deliveries t WHERE operation_id=%s',(row['operation_id'],)).fetchone()[0]
                assert saved == row
            stable_captures = [row[0] for row in db.execute('SELECT to_jsonb(t) FROM product_feedback_captures t ORDER BY source_key,source_revision,source_digest,comparison_revision')]
            stable_commands = [row[0] for row in db.execute('SELECT to_jsonb(t) FROM product_feedback_deliveries t ORDER BY operation_id')]
        inputs['now'] += 601
        run('worker')
        with connections() as db:
            assert [row[0] for row in db.execute('SELECT to_jsonb(t) FROM product_feedback_captures t ORDER BY source_key,source_revision,source_digest,comparison_revision')] == stable_captures
            assert [row[0] for row in db.execute('SELECT to_jsonb(t) FROM product_feedback_deliveries t ORDER BY operation_id')] == stable_commands
        report = run('status')
        assert report['read_only'] and report['collection']['availability'] == 'available'
        assert report['collection']['unique_games'] == 822
        assert study_rows() == before
        with patch.object(review_storage,'configured',lambda:True):
            with case.transaction() as db:
                result = apply_legacy_review(case.card_id, ReviewRequest(outcome='correct',queue_entry_id=entry,
                    expected_revision=1,attempt_id='packaged-'+case.card_id),database=db,
                    policy_provider=lambda:SimpleNamespace(timezone='UTC',light_first_interval_days=7))
                assert result['persisted']
    finally:
        evidence = ROOT/'test-results/collection-release'; evidence.mkdir(parents=True,exist_ok=True)
        with connections() as db:
            jobs = [row[0] for row in db.execute('SELECT payload FROM product_feedback_jobs ORDER BY job_id')]
            counts = {table: db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in
                ('product_feedback_sources','product_feedback_captures','product_feedback_deliveries')}
            schema = db.execute('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0]
        (evidence/'packaged.json').write_text(json.dumps(dict(candidate=revision,image=image,schema=schema,
            fixture_container=pg['Id'],jobs=jobs,counts=counts,receipts=receipts),indent=2))


# Reuse the exact native setup/teardown owner with a fresh module-independent
# lifetime. Running both packaged cases cannot lend retained collection rows or
# configuration to the single feedback journey.
feedback_postgres = pytest.fixture(scope='function', name='feedback_postgres')(inspect.unwrap(owned_postgres))


@pytest.fixture
def feedback_case(feedback_postgres):
    yield from inspect.unwrap(case)(feedback_postgres)


def test_packaged_feedback_unseen_source_reaches_real_study_and_same_key_settlement(feedback_case, tmp_path):
    """One real provider → role-separated command → Study journey, no source overlay."""
    from types import SimpleNamespace
    import shutil

    image, revision = _candidate_image()
    case = feedback_case
    with case.transaction() as database:
        info = database.raw.info
        admin_dsn = make_conninfo(info.dsn, password=info.password)
        port, database_name = info.port, info.dbname
        assert database.execute_native('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] == 56
        database.execute_native("UPDATE settings SET timezone='UTC',new_cards_per_day=0,study_only_mode=0 WHERE id=1")
    manifests = [json.loads(path.read_text()) for path in (ROOT/'test-results/game-feedback-native').glob('*/fixture.json')]
    matches = [item for item in manifests if item.get('port') == port and not item.get('cleanup_verified')]
    assert len(matches) == 1
    owner = matches[0]['owner']
    pg = json.loads(subprocess.check_output(['docker','inspect',matches[0]['container_id']],text=True))[0]
    assert pg['Config']['Labels']['tempo.owner'] == owner and pg['Config']['Labels']['tempo.disposable'] == 'true'
    # Authored fixture facts only. Queue rows, introductions, attempt origins,
    # human reviews and effect receipts below come from installed owners.
    retained_owner_case(case, False)
    target_id, source_id = 'packaged-unseen-'+uuid.uuid4().hex, 'p'+uuid.uuid4().hex[:7]
    with case.transaction() as database:
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type)
            SELECT %s,repertoire_id,kind,start_fen,moves_json,'locked',CURRENT_DATE,'opening'
            FROM cards WHERE id=%s""", (target_id,case.card_id))
        database.execute_native('UPDATE cards SET due_date=CURRENT_DATE WHERE id=%s',(case.card_id,))
    target_case = SimpleNamespace(transaction=case.transaction,card_id=target_id,
        decisions=SimpleNamespace(mappings=(case.decisions.mappings[0].model_copy(update={'presentation_id':target_id}),)),
        lapse=case.lapse.model_copy(update={'key':case.lapse.key.model_copy(update={'game_id':uuid.uuid4().hex}),
            'finding':case.lapse.finding.model_copy(update={'presentation_id':target_id})}))
    repertoire, capture, _, _ = retained_owner_case(target_case, True)
    with case.transaction() as database:
        database.execute_native("""UPDATE cards SET introduced_at=NULL,first_correct_at=NULL,fsrs_card_json=NULL,
            state='locked',interval_days=0,stability=0 WHERE id=%s""", (target_id,))
        assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(target_id,)).fetchone()[0] == 0
        assert database.execute_native('SELECT COUNT(*) FROM daily_queue').fetchone()[0] == 0
    roles_spec = importlib.util.spec_from_file_location('feedback_fixture_roles',ROOT/'database/tooling/provision_postgres_roles.py')
    roles = importlib.util.module_from_spec(roles_spec); roles_spec.loader.exec_module(roles)
    reader_password, writer_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    roles.provision(admin_dsn, reader_password, writer_password)
    reader_dsn = make_conninfo(host='127.0.0.1',port=5432,dbname=database_name,user='tempo_reader',password=reader_password)
    writer_dsn = make_conninfo(host='127.0.0.1',port=5432,dbname=database_name,user='tempo_writer',password=writer_password)
    directory = tmp_path/'feedback-qualification'; directory.mkdir()
    shutil.copyfile(ROOT/'backend/tests/fixtures/product_collection_packaged.py',directory/'entry.py')
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    pgn = '[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n[SetUp "1"]\n[FEN "'+capture['game']['start_fen']+'"]\n\n1. d4 1-0\n'
    played = now-timedelta(days=1)
    inputs = dict(now=now.timestamp(),lichess=[dict(id=source_id,createdAt=int(played.timestamp()*1000),
        rated=True,speed='rapid',pgn=pgn)],chesscom={},configuration=dict(workspace_id='legacy',provider='lichess',
        account='Learner',window=dict(start_at=(now-timedelta(days=2)).isoformat(),
            end_at=(now-timedelta(hours=1)).isoformat(),precision='second'),repertoire_ids=[repertoire],
        speeds=['rapid'],rated=True,interval_seconds=60,revision=1))
    (directory/'provider.json').write_text(json.dumps(inputs))
    budgets = json.loads((ROOT/'config/resource-budgets.json').read_text())['developmentServices']
    containers, report = {}, dict(candidate=revision,image=image,schema=56,fixture_container=pg['Id'],stages=[])
    stage = 'role_startup'

    def docker(*arguments, check=True, input=None):
        result = subprocess.run(['docker',*arguments],input=input,capture_output=True,text=True,timeout=45)
        if check:
            assert result.returncode == 0, 'packaged_feedback_docker_'+stage
        return result

    def launch(role, command, *, backend=True):
        selected_image = image if backend else json.loads(docker('image','inspect','redis:7-alpine').stdout)[0]['Id']
        budget = budgets[role]
        secret = tmp_path/(role+'.env')
        common = ['TEMPO_TEST_INSTANCE=disposable','TZ=UTC','TEMPO_COORDINATOR_MODE=external',
            'TEMPO_PRODUCT_GAME_SYNC_ENABLED=1','TEMPO_REDIS_URL=redis://127.0.0.1:6379/0',
            'PYTHONDONTWRITEBYTECODE=1','TEMPO_DATABASE_READ_URL='+(reader_dsn if role=='api' else writer_dsn)]
        if role != 'api':
            common.append('TEMPO_DATABASE_WRITE_URL='+writer_dsn)
        secret.write_text('\n'.join(common)); secret.chmod(0o600)
        name = owner+'-'+role
        arguments = ['run','--detach','--name',name,'--init','--label','tempo.owner='+owner,
            '--label','tempo.disposable=true','--label','com.docker.compose.service='+role,
            '--network','container:'+pg['Id'],'--read-only','--tmpfs','/tmp:rw,size=32m',
            '--memory',str(budget['memoryMiB'])+'m','--pids-limit',str(budget.get('pidsLimit',96))]
        if backend:
            arguments += ['--env-file',str(secret),'--mount','type=bind,src='+str(directory)+',dst=/qualification,readonly']
        else:
            arguments += ['--tmpfs','/data:rw,size=32m']
        identifier = docker(*arguments,selected_image,*command).stdout.strip()
        containers[role] = dict(id=identifier,image=selected_image)
        inspected = json.loads(docker('inspect',identifier).stdout)[0]
        assert inspected['Image'] == selected_image and inspected['State']['Running']
        assert inspected['Config']['Labels']['tempo.owner'] == owner
        assert inspected['HostConfig']['NanoCpus'] == 0 and inspected['HostConfig']['CpuQuota'] in (0,-1)
        assert inspected['HostConfig']['Memory'] == budget['memoryMiB']*1024*1024
        return inspected

    def execute(role, *arguments, input=None):
        return docker('exec','-i',containers[role]['id'],*arguments,input=input).stdout

    def entry(role, mode, *arguments):
        return json.loads(execute(role,'python','-B','/qualification/entry.py',mode,*arguments).strip().splitlines()[-1])

    def api(method,path,body=None,identity=None):
        code = """import json,sys,httpx
request=json.load(sys.stdin)
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=10) as client:
 try:
  response=client.request(request['method'],request['path'],json=request['body'],headers=request['headers'])
  try: value=response.json()
  except ValueError: value={'invalid_json':True}
  print(json.dumps({'status':response.status_code,'body':value}))
 except httpx.TransportError:
  print(json.dumps({'status':0,'body':{'transport_unavailable':True}}))
"""
        return json.loads(execute('api','python','-B','-c',code,input=json.dumps(dict(method=method,path=path,body=body,
            headers={'Idempotency-Key':identity} if identity else {}))))

    def wait_for(observe, reason):
        deadline = time.monotonic()+45
        while True:
            result = observe()
            if result:
                return result
            assert time.monotonic()<deadline, reason
            time.sleep(.1)

    def command(path, body, identity):
        result = api('POST',path,body,identity)
        if result['status']==202:
            operation = result['body']['operation_id']
            def complete():
                observed = api('GET','/api/operations/'+operation)
                assert observed['status']==200
                assert observed['body']['state']!='failed', 'packaged_feedback_operation_failed'
                return observed['body'].get('response') if observed['body']['state']=='complete' else None
            return wait_for(complete,'packaged_feedback_operation_pending')
        assert result['status']==200, 'packaged_feedback_command_rejected'
        return result['body']

    def queue():
        result = api('GET','/api/queue/window?limit=2')
        if result['status']==503:
            detail = result['body'].get('detail')
            assert (isinstance(detail,dict) and detail.get('code')=='study_feedback_preparation_pending'
                or detail=='Queue changed or attempt issuance is pending. Retry loading the queue.'), 'packaged_feedback_queue_unavailable'
            return None
        assert result['status']==200, 'packaged_feedback_queue_rejected'
        return result['body'] if result['body'].get('cards') else None

    try:
        launch('redis',['redis-server','--appendonly','no','--save',''],backend=False)
        launch('foreground-worker',['celery','-A','app.celery_app:celery_app','worker','--queues=foreground',
            '--concurrency=2','--prefetch-multiplier=1','--loglevel=INFO'])
        launch('background-worker',['celery','-A','app.celery_app:celery_app','worker','--queues=background',
            '--concurrency=1','--prefetch-multiplier=1','--loglevel=INFO'])
        launch('api',['uvicorn','app.main:app','--host','0.0.0.0','--port','8000'])
        entry('foreground-worker','feedback-configure')
        launch('game-feedback-worker',['python','-B','/qualification/entry.py','feedback-worker'])
        wait_for(lambda: api('GET','/api/health')['status']==200,'packaged_feedback_api_not_ready')
        def actual_collector_prepared():
            observed = json.loads(docker('inspect',containers['game-feedback-worker']['id']).stdout)[0]
            assert observed['State']['Running'], 'packaged_feedback_collector_exited_before_verification'
            return '"feedback_runtime_prepared": true' in docker('logs',observed['Id']).stdout
        wait_for(actual_collector_prepared,'packaged_feedback_collector_not_prepared')
        stage = 'genuine_protected_head'
        protected_queue = wait_for(queue,'packaged_feedback_protected_head_pending')
        protected = protected_queue['cards'][0]
        assert protected['id']==case.card_id and all(card['id']!=target_id for card in protected_queue['cards'])
        with case.transaction() as database:
            protected_origin = tuple(database.execute_native("""SELECT queue_entry_id,card_id,revision,issuance_sequence,issued_at
                FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s AND issued_as_head=1""",
                (protected['queue_entry_id'],case.card_id)).fetchone())
            assert protected_origin[3] is not None
        stage = 'verified_managed_mode'
        capabilities = entry('api','feedback-capabilities')
        assert capabilities['availability']=='available', 'packaged_feedback_composition_unavailable'
        assert capabilities['policy_version']=='first_departure_v1' and capabilities['receipt_interface_version']=='0.2.2'
        assert all(capabilities[name] is True for name in ('selector','current_authority','again','canonical_introduction',
            'retained_qualification','foreground_handoff','issuance_fence')), 'packaged_feedback_composition_incomplete'
        report['feedback_capabilities']=capabilities
        observed_runtimes, observed_config = [], {}
        for role in ('api','foreground-worker','game-feedback-worker'):
            inspected = json.loads(docker('inspect',containers[role]['id']).stdout)[0]
            identity = json.loads(execute(role,'python','-m','app.product_composition.managed_runtime','inspect'))
            assert inspected['State']['Running'] and inspected['Image']==image
            assert identity['read_only'] and identity['runtime']['hostname']==inspected['Config']['Hostname']
            profile = json.loads(execute(role,'python','-B','-c',
                "import json,os,psycopg\nwith psycopg.connect(os.environ['TEMPO_DATABASE_READ_URL']) as database:\n"
                " print(json.dumps(dict(user=database.execute('SELECT current_user').fetchone()[0],"
                "read_only=database.execute('SHOW transaction_read_only').fetchone()[0],"
                "writer_profile='TEMPO_DATABASE_WRITE_URL' in os.environ)))"))
            assert profile['user']==('tempo_reader' if role=='api' else 'tempo_writer')
            assert profile['writer_profile'] is (role!='api')
            if role=='api':
                assert profile['read_only']=='on'
            report.setdefault('database_profiles',{})[role]=profile
            observed_runtimes.append(dict(service=role,container_id=inspected['Id'],hostname=identity['runtime']['hostname'],
                boot_nonce=identity['runtime']['boot_nonce'],image=image))
            observed_config[role] = inspected['Config']
        assert len({item['container_id'] for item in observed_runtimes})==3
        stamp = datetime.now(timezone.utc).isoformat()
        mode = dict(schema_version=1,workspace_id='legacy',generation=1,mode='feedback',phase='active',
            operation_id='packaged-feedback:'+uuid.uuid4().hex,release_revision=revision,collector_image=image,
            configuration_fingerprint=hashlib.sha256(json.dumps(observed_config,sort_keys=True).encode()).hexdigest(),
            target_fingerprint=hashlib.sha256(json.dumps(dict(owner=owner,container=pg['Id'],database=database_name),sort_keys=True).encode()).hexdigest(),
            runtimes=observed_runtimes,updated_at=stamp,verified_at=stamp)
        recorded = json.loads(execute('game-feedback-worker','python','-m','app.product_composition.game_feedback_mode',
            'record','--workspace','legacy','--expected-generation','0','--intent-json',json.dumps(mode),'--apply'))
        assert recorded['applied'] and recorded['mode']=='feedback'
        inputs['now'] = datetime.now(timezone.utc).timestamp()
        (directory/'provider.json').write_text(json.dumps(inputs))
        (directory/'feedback-start').write_text('actual_mode_verified\n')
        stage = 'provider_first_departure'
        def actual_delivery():
            with case.transaction() as database:
                rows = database.execute_native("""SELECT delivery.operation_id,delivery.payload
                    FROM product_feedback_deliveries delivery JOIN product_feedback_sources source USING(source_key)
                    WHERE source.payload->'original'->>'source_game_id'=%s""",(source_id,)).fetchall()
            assert len(rows)<=1, 'packaged_feedback_duplicate_original_effect'
            return rows[0] if rows else None
        delivery = wait_for(actual_delivery,'packaged_feedback_original_delivery_missing')
        operation_id, original = delivery[0], delivery[1]['command']
        assert original['observation']=='missed' and original['played_uci']=='d2d4' and original['expected_uci']=='e2e4'
        stage = 'canonical_introduction'
        for _unit in range(8):
            command('/api/queue/prepare-next',{},'packaged-prepare:'+uuid.uuid4().hex)
            with case.transaction() as database:
                introduced = database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(target_id,)).fetchone()[0]
                queued = database.execute_native("SELECT id FROM daily_queue WHERE card_id=%s AND status='queued'",(target_id,)).fetchone()
            if introduced and queued:
                break
            time.sleep(.1)
        assert introduced and queued, 'packaged_feedback_canonical_introduction_missing'
        before_review = entry('api','feedback-state',operation_id)
        receipt = before_review['receipt']
        assert receipt['key']==original['key'] and receipt['disposition']=='first_introduction'
        assert before_review['qualification']['selection']['status']=='own_actionable'
        assert before_review['qualification']['selection']['ply']==original['key']['occurrence_ply']==1
        assert before_review['qualification']['selection']['decision']['presentation_id']==target_id
        assert receipt['progress']['target']['card_id']==target_id and receipt['progress']['first_issued'] is None
        with case.transaction() as database:
            assert tuple(database.execute_native("""SELECT queue_entry_id,card_id,revision,issuance_sequence,issued_at
                FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s AND revision=%s""",protected_origin[:3]).fetchone())==protected_origin
            assert database.execute_native('SELECT new_cards_per_day FROM settings WHERE id=1').fetchone()[0]==0
            assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(target_id,)).fetchone()[0]==0
        assert wait_for(queue,'packaged_feedback_current_board_missing')['cards'][0]['queue_entry_id']==protected['queue_entry_id']
        stage = 'ordinary_review_and_issuance'
        def review(card, attempt):
            body = dict(outcome='correct',guided=False,queue_entry_id=card['queue_entry_id'],expected_revision=card['revision'],attempt_id=attempt)
            path, identity = '/api/cards/'+card['id']+'/review','review-attempt:'+attempt
            saved = command(path,body,identity)
            assert saved['persisted'] and command(path,body,identity)==saved
            return saved
        with case.transaction() as database:
            refresh_requested_after = database.execute_native('SELECT clock_timestamp()').fetchone()[0]
        review(protected,'packaged-protected:'+uuid.uuid4().hex)
        def actual_unseen_refresh():
            with case.transaction() as database:
                row = database.execute_native("""SELECT generation,state,payload_json,updated_at FROM background_tasks
                    WHERE kind='daily_queue' AND deduplication_key='current' AND state='complete'
                    AND updated_at::timestamptz>%s""",(refresh_requested_after,)).fetchone()
                if row is None:
                    return None
                assert database.execute_native("SELECT 1 FROM daily_queue WHERE id=%s AND card_id=%s AND status='queued'",
                    (queued[0],target_id)).fetchone(), 'packaged_feedback_unseen_refresh_removed_owed_target'
                assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(target_id,)).fetchone()[0]
                return dict(row)
        report['actual_sliced_unseen_refresh'] = wait_for(actual_unseen_refresh,'packaged_feedback_unseen_refresh_pending')
        target_queue = wait_for(queue,'packaged_feedback_target_head_pending')
        target = target_queue['cards'][0]
        assert target['id']==target_id
        issued = entry('api','feedback-state',operation_id)['receipt']
        assert 1<=issued['progress']['first_issued_ordinal']<=5
        assert issued['progress']['first_issued']['queue_entry_id']==target['queue_entry_id']
        assert issued['progress']['determined_at']==before_review['qualification']['determined_at']
        saved = review(target,'packaged-target:'+uuid.uuid4().hex)
        stage = 'same_key_settlement'
        settled = entry('api','feedback-state',operation_id)
        assert settled['command']==original and settled['receipt']['key']==original['key']
        assert settled['receipt']['status']=='settled'
        assert settled['receipt']['progress']['settlement']['review_id']==saved['review_id']
        assert settled['receipt']['progress']['settlement']['attempt']==issued['progress']['first_issued']
        with case.transaction() as database:
            assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind=\'study\'',(target_id,)).fetchone()[0]==1
            assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind=\'game_lapse\'',(target_id,)).fetchone()[0]==0
            assert database.execute_native("SELECT payload->'command' FROM product_feedback_deliveries WHERE operation_id=%s",(operation_id,)).fetchone()[0]==original
            report['actual_issuances_after_determination'] = database.execute_native("""SELECT COUNT(*) FROM queue_attempt_origins
                WHERE issued_as_head=1 AND issued_at>%s AND issuance_sequence IS NOT NULL""",
                (issued['progress']['determined_at'],)).fetchone()[0]
            report['unseen_refresh_task'] = database.execute_native("""SELECT payload_json FROM background_tasks
                WHERE kind='daily_queue' AND deduplication_key='current'""").fetchone()[0]
        report.update(operation_id=operation_id,key=original['key'],qualification=settled['qualification'],receipt=settled['receipt'])
        report['stages'].append('same_key_settlement')
    except BaseException as error:
        report['first_failure'] = dict(stage=stage,error=type(error).__name__)
        raise
    finally:
        cleanup = []
        for role, resource in reversed(list(containers.items())):
            found = docker('inspect',resource['id'],check=False)
            if found.returncode==0:
                inspected = json.loads(found.stdout)[0]
                assert inspected['Config']['Labels']['tempo.owner']==owner
                assert inspected['Config']['Labels']['tempo.disposable']=='true' and inspected['Image']==resource['image']
                logs = docker('logs',resource['id'],check=False)
                text = (logs.stdout+'\n'+logs.stderr).replace(reader_password,'<reader-secret>').replace(writer_password,'<writer-secret>')
                report.setdefault('runtime_logs',{})[role]=text[-12000:]
                docker('rm','-f',resource['id'])
            absent = docker('inspect',resource['id'],check=False)
            assert absent.returncode!=0 and 'no such' in absent.stderr.lower()
            cleanup.append(dict(service=role,container=resource['id'],image=resource['image'],removed=True))
        report['cleanup_verified']=len(cleanup)==len(containers)
        report['runtimes']=cleanup
        evidence = ROOT/'test-results/collection-release'; evidence.mkdir(parents=True,exist_ok=True)
        (evidence/('feedback-'+owner+'.json')).write_text(json.dumps(report,indent=2,default=str))
