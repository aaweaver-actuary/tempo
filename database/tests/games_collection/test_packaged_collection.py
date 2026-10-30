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
import signal
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


def _candidate_web_image(revision):
    image = os.environ.get('TEMPO_COLLECTION_TEST_WEB_IMAGE')
    assert image, 'Set TEMPO_COLLECTION_TEST_WEB_IMAGE to the immutable qualified web image ID'
    inspected = json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]
    assert inspected['Id']==image and inspected['Config']['Labels']['org.opencontainers.image.revision']==revision
    # Dockerfile.web copies the root context, including test sources. Keep the
    # exact candidate closure; do not relabel a previous image after a repair.
    subprocess.run(['git','diff','--exit-code',revision,'--','.'],cwd=ROOT,check=True,capture_output=True)
    assert not subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=ROOT,text=True).strip()
    return image


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
    bounded_repertoire = 'synthetic-estimated-byte-bound'
    def publish_black():
        with connections() as db:
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Black source','fixture','2026-09-01')", (black_repertoire,))
            db.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('black-route',%s,'Black route','black',%s,%s,'2026-09-01')", (black_repertoire, 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', json.dumps(['e2e4','e7e5','g1f3','b8c6'])))
            db.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready','2026-09-01')", (black_repertoire,))
    publish_black()
    # A real owning SQL estimate refusal, matching the PM-reported dimension.
    # Each row is below 64 KiB and counts are below their sentinels. Padding is
    # synthetic authored metadata, never a seeded finding or injected exception.
    with connections() as db:
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Bound fixture','fixture','2026-09-01')", (bounded_repertoire,))
        db.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready','2026-09-01')", (bounded_repertoire,))
        with db.cursor() as cursor:
            cursor.executemany("""INSERT INTO repertoire_lines
                (id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                VALUES(%s,%s,%s,'white',%s,'["e2e4"]','2026-09-01')""",
                [(f'bound-route-{index:04d}', bounded_repertoire, 'Synthetic padding ' + 'x' * 6000,
                  captured['game']['start_fen']) for index in range(1000)])
    review = PostgresReviewStore(connections)
    configs = []
    for provider in ('lichess','chesscom'):
        config = SyncConfiguration(workspace_id='legacy',provider=provider,account='Learner',
            window=dict(start_at='2026-09-30T12:00:00Z',end_at='2026-10-01T12:00:00Z',precision='second'),
            repertoire_ids=(repertoire,black_repertoire,bounded_repertoire),speeds=('rapid',),rated=True,interval_seconds=60,revision=1)
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
    operator_receipts = []
    def run(mode):
        (directory/'provider.json').write_text(json.dumps(inputs))
        name = 'tempo-g-collection-'+uuid.uuid4().hex
        args = ['docker','run','--rm','--name',name,'--label','tempo.owner='+matches[0]['owner'],
            '--label','tempo.disposable=true','--read-only','--tmpfs','/tmp:rw,size=32m','--memory','256m','--pids-limit','96',
            '--network','container:'+pg['Id'],'--env-file',str(secret),'--mount','type=bind,src='+str(directory)+',dst=/qualification,readonly',
            image,'python','-B','-c',"exec(compile(open('/qualification/entry.py').read(), '/qualification/entry.py', 'exec'))",mode]
        try:
            started = time.monotonic()
            result = subprocess.run(args,capture_output=True,text=True,timeout=180)
            receipts.append(dict(mode=mode,exit=result.returncode,stdout=result.stdout,stderr=result.stderr,
                                 elapsed_seconds=time.monotonic()-started))
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

    def evaluate(report, identity, preview, allowances, *, expected_schema=54):
        # Actual operator policy consumes authentic packaged reader output.
        # Only frozen-release/process metadata is synthetic here; installed
        # Docker/Compose orchestration remains a separate acceptance boundary.
        program = """
import {readFileSync} from 'node:fs';
import {collectionBlockers, assessSelectedReadiness, readProbe} from MODULE;
const input=JSON.parse(readFileSync(0,'utf8'));
const service={command:['python','-m','app.product_composition.game_sync_current','--collect-only',
 '--follow','--overlap-seconds','0','--reconcile-interval-seconds','600'],
 environment:{TEMPO_PRODUCT_GAME_SYNC_ENABLED:'1',
  TEMPO_DATABASE_WRITE_URL:'postgresql://tempo_writer@postgres:5432/tempo',
  TEMPO_DATABASE_READ_URL:'postgresql://tempo_writer@postgres:5432/tempo',PGPASSFILE:'/run/secrets/worker_pgpass'},
 secrets:[{source:'writer_pgpass',target:'worker_pgpass'}],mem_limit:268435456,pids_limit:96,
 labels:{'tempo.collection.owner':'managed-game-collection','tempo.collection.mode':'collect-only'}};
const identityProbe=readProbe(()=>JSON.stringify(input.identity));
const fullReaderProbe=readProbe(()=>JSON.stringify(input.report));
const blockers=collectionBlockers({candidate:input.revision,
 release:{revision:input.revision,schema:input.expected_schema,
  images:{'game-feedback-worker':input.image.Id},configFingerprint:'fixture'},
 service,image:input.image,report:input.report,preview:input.preview,containers:[],hostCommands:[],
 target:{project:'synthetic-collection'},fingerprint:'fixture',maintenance:{status:'clear'},
 identityProbe,fullReaderProbe,allowances:input.allowances});
console.log(JSON.stringify({activation_ready:blockers.length===0,blockers,
 ...assessSelectedReadiness(input.report,input.allowances,{fullReaderSuccessful:true,collectionOnly:true})}));
""".replace('MODULE', json.dumps((ROOT/'scripts/game-review-collection.mjs').as_uri()))
        # Keep global launch blockers separate from selected-readiness blockers.
        program = program.replace('activation_ready:blockers.length===0,blockers,',
                                  'activation_ready:blockers.length===0,launch_blockers:blockers,')
        result = subprocess.run(['node','--input-type=module','-e',program], input=json.dumps(dict(
            report=report,identity=identity,preview=preview,allowances=allowances,
            revision=revision,image=inspected,expected_schema=expected_schema)),
            capture_output=True,text=True,check=True,timeout=10)
        value = json.loads(result.stdout)
        operator_receipts.append(value)
        return value

    try:
        # Both healthy repertoires compare; the real selected bound stays unresolved.
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
            assert {'chesscom' if s['normalized']['source_id']=='chess.com' else
                    s['normalized']['source_id'] for s in sources} == {'lichess','chesscom'}
            assert {f['repertoire_id'] for s in sources for f in s['findings']} >= {repertoire,black_repertoire}
            assert any(bounded_repertoire in s['diagnostic'] for s in sources if s['diagnostic'])
            original_deliveries = [row[0] for row in db.execute('SELECT to_jsonb(t) FROM product_feedback_deliveries t ORDER BY operation_id')]
            assert original_deliveries and all(row['state']=='pending' for row in original_deliveries)
        assert study_rows() == before
        private_partial = run('status')
        public_partial = run('status-public')
        identity = run('identity')
        preview = run('preview')
        tokens = set()
        for private, public in zip(private_partial['providers']['items'],public_partial['providers']['items']):
            assert private['provider'] == public['provider']
            assert private['configuration']['revision'] == public['configuration']['revision'] == 1
            assert private['selected_repertoire_count'] == public['selected_repertoire_count'] == 3
            assert private['configuration']['repertoire_ids'] == [repertoire,black_repertoire,bounded_repertoire]
            index = next(i for i,row in enumerate(private['repertoire_readiness']) if row['repertoire_id']==bounded_repertoire)
            row = public['repertoire_readiness'][index]
            assert row['availability']=='unavailable' and row['diagnostic']=='repertoire_snapshot_exceeds_bound'
            assert row['bound']['stage']=='read' and row['bound']['sample_complete'] is True
            assert [v['dimension'] for v in row['bound']['violations']] == ['estimated_snapshot_bytes']
            assert row['bound']['violations'][0]['observation']=='conservative_estimate'
            tokens.add(row['repertoire_id'])
        assert len(tokens)==1
        token = tokens.pop()
        strict = evaluate(public_partial,identity,preview,[])
        assert not strict['activation_ready'] and strict['comparison_coverage']=='unresolved'
        allowed = evaluate(public_partial,identity,preview,[token])
        assert allowed['activation_ready'] and allowed['comparison_coverage']=='partial'
        assert allowed['deferred_comparisons'][0]['token']==token
        assert set(allowed['deferred_comparisons'][0]['providers'])=={'lichess','chesscom'}
        unknown_token = 'redacted:000000000000'
        assert unknown_token != token
        assert not evaluate(public_partial,identity,preview,[unknown_token])['activation_ready']
        corrupted = json.loads(json.dumps(public_partial))
        corrupted['providers']['items'][0]['selected_repertoire_count'] = 4
        assert not evaluate(corrupted,identity,preview,[token])['activation_ready']
        unknown = json.loads(json.dumps(public_partial))
        for provider in unknown['providers']['items']:
            for row in provider['repertoire_readiness']:
                if row['repertoire_id']==token: row['diagnostic']='repertoire_read_unavailable'
        assert not evaluate(unknown,identity,preview,[token])['activation_ready']
        wrong_role = run('identity-writer')
        assert wrong_role['identity']['reader_role']=='tempo_writer'
        assert 'reader_permission_unverified' in evaluate(public_partial,wrong_role,preview,[token])['launch_blockers']
        assert 'collection_schema_mismatch' in evaluate(public_partial,identity,preview,[token],expected_schema=55)['launch_blockers']
        assert all(r['elapsed_seconds'] <= 25 for r in receipts if r['mode'] in ('status','status-public'))
        # Repair only generated fixture metadata. All production limits and the
        # real reader/projection stay unchanged, with no successful facts seeded.
        with connections() as db:
            db.execute("DELETE FROM repertoire_lines WHERE repertoire_id=%s AND id<>'bound-route-0000'", (bounded_repertoire,))
            db.execute("UPDATE repertoire_lines SET name='Repaired synthetic route' WHERE repertoire_id=%s", (bounded_repertoire,))
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
            assert any(f['repertoire_id'] == bounded_repertoire for f in findings)
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
        recovered = run('status-public')
        recovered_readiness = evaluate(recovered,run('identity'),run('preview'),[token])
        assert recovered_readiness['activation_ready'] and recovered_readiness['comparison_coverage']=='complete'
        assert recovered_readiness['deferred_comparisons']==[]
        assert recovered_readiness['unused_bound_allowances']==[token]
        assert all(row['availability']=='available' for provider in recovered['providers']['items']
                   for row in provider['repertoire_readiness'])
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
            fixture_container=pg['Id'],jobs=jobs,counts=counts,receipts=receipts,
            operator_evaluations=operator_receipts,
            bound_fixture='complete-sample conservative estimated_snapshot_bytes; matches PM-reported dimension, not a fresh private measurement',
            production_source_overlay=False,seeded_successful_findings=False),indent=2))


# Reuse the exact native setup/teardown owner with a fresh module-independent
# lifetime. Running both packaged cases cannot lend retained collection rows or
# configuration to the single feedback journey.
@pytest.fixture
def feedback_postgres(tmp_path_factory):
    yield from inspect.unwrap(owned_postgres)(tmp_path_factory,additional_ports=(80,))


@pytest.fixture
def feedback_case(feedback_postgres):
    yield from inspect.unwrap(case)(feedback_postgres)


@pytest.mark.parametrize("introduced", [False, True], ids=["unseen", "introduced"])
def test_packaged_feedback_source_reaches_real_study_and_same_key_settlement(feedback_case, tmp_path, introduced):
    """One real provider → role-separated command → Study journey, no source overlay."""
    from types import SimpleNamespace
    from app.games.feedback.ports import Delivery
    import shutil

    image, backend_revision = _candidate_image()
    candidate = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    web_image = _candidate_web_image(candidate)
    case = feedback_case
    with case.transaction() as database:
        info = database.raw.info
        admin_dsn = make_conninfo(info.dsn, password=info.password)
        port, database_name = info.port, info.dbname
        assert database.execute_native('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] == 56
        database.execute_native("UPDATE settings SET timezone='UTC',new_cards_per_day=0,study_only_mode=1 WHERE id=1")
        assert tuple(database.execute_native('SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1').fetchone())==(0,1)
    manifests = [json.loads(path.read_text()) for path in (ROOT/'test-results/game-feedback-native').glob('*/fixture.json')]
    matches = [item for item in manifests if item.get('port') == port and not item.get('cleanup_verified')]
    assert len(matches) == 1
    owner = matches[0]['owner']
    pg = json.loads(subprocess.check_output(['docker','inspect',matches[0]['container_id']],text=True))[0]
    assert pg['Config']['Labels']['tempo.owner'] == owner and pg['Config']['Labels']['tempo.disposable'] == 'true'
    binding = pg['NetworkSettings']['Ports']['80/tcp'][0]
    assert binding['HostIp']=='127.0.0.1' and matches[0]['published_ports']['80/tcp']==[binding]
    web_url = 'http://127.0.0.1:'+binding['HostPort']
    # Authored fixture facts only. Queue rows, introductions, attempt origins,
    # human reviews and effect receipts below come from installed owners.
    retained_owner_case(case, False)
    target_id, source_id = 'packaged-'+('introduced-' if introduced else 'unseen-')+uuid.uuid4().hex, 'p'+uuid.uuid4().hex[:7]
    with case.transaction() as database:
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type)
            SELECT %s,repertoire_id,kind,start_fen,moves_json,'locked',CURRENT_DATE,'opening'
            FROM cards WHERE id=%s""", (target_id,case.card_id))
        # The general retained-history proof deliberately uses a timestamp.
        # This ordinary browser seed follows canonical admission's day storage.
        database.execute_native("""UPDATE cards SET due_date=CURRENT_DATE,
            introduced_at=split_part(introduced_at,'T',1) WHERE id=%s""",(case.card_id,))
    target_case = SimpleNamespace(transaction=case.transaction,card_id=target_id,
        decisions=SimpleNamespace(mappings=(case.decisions.mappings[0].model_copy(update={'presentation_id':target_id}),)),
        lapse=case.lapse.model_copy(update={'key':case.lapse.key.model_copy(update={'game_id':uuid.uuid4().hex}),
            'finding':case.lapse.finding.model_copy(update={'presentation_id':target_id})}))
    repertoire, capture, _, _ = retained_owner_case(target_case, True)
    with case.transaction() as database:
        if introduced:
            # Preexisting authored scheduling state only; the actual provider
            # command must create its own Again receipt and fresh issuance.
            database.execute_native("""UPDATE cards target SET state='mature',due_date=CURRENT_DATE+30,
                interval_days=original.interval_days,stability=original.stability,
                fsrs_card_json=original.fsrs_card_json,first_correct_at=original.first_correct_at,
                introduced_at=split_part(target.introduced_at,'T',1)
                FROM cards original WHERE target.id=%s AND original.id=%s""", (target_id,case.card_id))
        else:
            database.execute_native("""UPDATE cards SET introduced_at=NULL,first_correct_at=NULL,fsrs_card_json=NULL,
                state='locked',interval_days=0,stability=0 WHERE id=%s""", (target_id,))
        assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(target_id,)).fetchone()[0] == 0
        assert database.execute_native('SELECT COUNT(*) FROM daily_queue').fetchone()[0] == 0
    roles_spec = importlib.util.spec_from_file_location('feedback_fixture_roles',ROOT/'database/tooling/provision_postgres_roles.py')
    roles = importlib.util.module_from_spec(roles_spec); roles_spec.loader.exec_module(roles)
    reader_password, writer_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    roles.provision(admin_dsn, reader_password, writer_password)
    reader_dsn = make_conninfo(host='postgres',port=5432,dbname=database_name,user='tempo_reader',password=reader_password)
    writer_dsn = make_conninfo(host='postgres',port=5432,dbname=database_name,user='tempo_writer',password=writer_password)
    directory = tmp_path/'feedback-qualification'; directory.mkdir()
    shutil.copyfile(ROOT/'backend/tests/fixtures/product_collection_packaged.py',directory/'entry.py')
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    pgn = '[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n[SetUp "1"]\n[FEN "'+capture['game']['start_fen']+'"]\n\n1. d4 1-0\n'
    played = now-timedelta(minutes=90)
    inputs = dict(now=now.timestamp(),lichess=[dict(id=source_id,createdAt=int(played.timestamp()*1000),
        rated=True,speed='rapid',pgn=pgn)],chesscom={},configuration=dict(workspace_id='legacy',provider='lichess',
        account='Learner',window=dict(start_at=(now-timedelta(hours=3)).replace(microsecond=0).isoformat(),
            end_at=(now-timedelta(hours=1)).replace(microsecond=0).isoformat(),precision='second'),repertoire_ids=[repertoire],
        speeds=['rapid'],rated=True,interval_seconds=60,revision=1))
    (directory/'provider.json').write_text(json.dumps(inputs))
    nginx = (ROOT/'nginx.conf').read_text()
    assert nginx.count('proxy_pass http://api:8000;')==1
    (directory/'nginx.conf').write_text(nginx)
    budgets = json.loads((ROOT/'config/resource-budgets.json').read_text())['developmentServices']
    containers, report = {}, dict(candidate=candidate,backend_revision=backend_revision,web_revision=candidate,
        image=image,web_image=web_image,schema=56,disposition='introduced_again' if introduced else 'first_introduction',
        fixture_container=pg['Id'],web_origin=web_url,stages=[])
    stage = 'role_startup'
    browser, browser_log = None, None
    network, postgres_attached = None, False
    evidence = ROOT/'test-results/collection-release'; evidence.mkdir(parents=True,exist_ok=True)
    browser_evidence = evidence/('browser-'+owner); browser_evidence.mkdir()

    def docker(*arguments, check=True, input=None):
        result = subprocess.run(['docker',*arguments],input=input,capture_output=True,text=True,timeout=45)
        if check:
            if result.returncode:
                diagnostic = result.stderr
                for private in (admin_dsn,reader_dsn,writer_dsn,reader_password,writer_password):
                    diagnostic = diagnostic.replace(private,'<redacted>')
                report.setdefault('first_command_failure',dict(stage=stage,returncode=result.returncode,
                    stderr=diagnostic.encode('utf8')[-4096:].decode('utf8',errors='replace')))
            assert result.returncode == 0, 'packaged_feedback_docker_'+stage
        return result

    def launch(role, command, *, kind='backend'):
        selected_image = (image if kind=='backend' else web_image if kind=='web'
            else json.loads(docker('image','inspect','redis:7-alpine').stdout)[0]['Id'])
        budget = budgets[role]
        secret = tmp_path/(role+'.env')
        common = ['TEMPO_TEST_INSTANCE=disposable','TZ=UTC','TEMPO_COORDINATOR_MODE=external',
            'TEMPO_PRODUCT_GAME_SYNC_ENABLED=1','TEMPO_REDIS_URL=redis://redis:6379/0',
            'PYTHONDONTWRITEBYTECODE=1','TEMPO_DATABASE_READ_URL='+(reader_dsn if role=='api' else writer_dsn)]
        if role != 'api':
            common.append('TEMPO_DATABASE_WRITE_URL='+writer_dsn)
        secret.write_text('\n'.join(common)); secret.chmod(0o600)
        name = owner+'-'+role
        arguments = ['run','--detach','--name',name,'--init','--label','tempo.owner='+owner,
            '--label','tempo.disposable=true','--label','com.docker.compose.service='+role,
            '--read-only','--tmpfs','/tmp:rw,size=32m',
            '--memory',str(budget['memoryMiB'])+'m','--pids-limit',str(budget.get('pidsLimit',96))]
        if kind=='web':
            arguments += ['--network','container:'+pg['Id']]
        else:
            arguments += ['--network',network['id'],'--network-alias',role]
        if kind=='backend':
            arguments += ['--env-file',str(secret),'--mount','type=bind,src='+str(directory)+',dst=/qualification,readonly']
        elif kind=='redis':
            arguments += ['--tmpfs','/data:rw,size=32m']
        else:
            assert kind=='web'
            arguments += ['--tmpfs','/var/cache/nginx:rw,size=16m','--tmpfs','/var/run:rw,size=1m',
                '--mount','type=bind,src='+str(directory/'nginx.conf')+',dst=/etc/nginx/conf.d/default.conf,readonly']
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

    fixture_entry = "exec(compile(open('/qualification/entry.py').read(), '/qualification/entry.py', 'exec'))"

    def entry(role, mode, *arguments):
        return json.loads(execute(role,'python','-B','-c',fixture_entry,mode,*arguments).strip().splitlines()[-1])

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

    def health_ready():
        observed = api('GET','/api/health')
        diagnostic = json.dumps(observed['body'])
        for private in (admin_dsn,reader_dsn,writer_dsn,reader_password,writer_password):
            diagnostic = diagnostic.replace(private,'<redacted>')
        if len(diagnostic.encode('utf8'))>4096:
            body = dict(truncated=True,prefix=diagnostic.encode('utf8')[:4000].decode('utf8',errors='replace'))
        else:
            body = json.loads(diagnostic)
        snapshot = dict(status=observed['status'],body=body)
        report.setdefault('first_health_response',snapshot)
        report['last_health_response']=snapshot
        return observed['status']==200

    def wait_for(observe, reason):
        deadline = time.monotonic()+45
        while True:
            result = observe()
            if result:
                return result
            assert time.monotonic()<deadline, reason
            time.sleep(.1)

    def queue():
        result = api('GET','/api/queue/window?limit=2')
        if result['status']==503:
            detail = result['body'].get('detail')
            assert (isinstance(detail,dict) and detail.get('code')=='study_feedback_preparation_pending'
                or detail=='Queue changed or attempt issuance is pending. Retry loading the queue.'), 'packaged_feedback_queue_unavailable'
            return None
        assert result['status']==200, 'packaged_feedback_queue_rejected'
        return result['body'] if result['body'].get('cards') else None

    def provider_ready(value):
        path = directory/'provider-ready.json'
        temporary = path.with_suffix('.next')
        temporary.write_text(json.dumps(value))
        temporary.replace(path)

    def browser_ready(expected_stage):
        path = directory/'browser-ready.json'
        if path.exists():
            observed = json.loads(path.read_text())
            if observed['stage']==expected_stage:
                return observed
        assert browser is not None and browser.poll() is None, 'packaged_feedback_browser_exited_before_'+expected_stage
        return None

    browser_name = 'packaged first-departure feedback uses real browser saves and issuance'

    try:
        network_name = owner+'-runtime'
        network_id = docker('network','create','--driver','bridge','--label','tempo.owner='+owner,
            '--label','tempo.disposable=true',network_name).stdout.strip()
        network = dict(id=network_id,name=network_name)
        observed_network = json.loads(docker('network','inspect',network_id).stdout)[0]
        assert observed_network['Name']==network_name and observed_network['Driver']=='bridge'
        assert observed_network['Labels']['tempo.owner']==owner and observed_network['Labels']['tempo.disposable']=='true'
        report['network']=network
        docker('network','connect','--alias','postgres',network_id,pg['Id'])
        postgres_attached=True
        launch('redis',['redis-server','--appendonly','no','--save',''],kind='redis')
        launch('foreground-worker',['celery','-A','app.celery_app:celery_app','worker','--queues=foreground',
            '--concurrency=2','--prefetch-multiplier=1','--loglevel=INFO'])
        launch('background-worker',['celery','-A','app.celery_app:celery_app','worker','--queues=background',
            '--concurrency=1','--prefetch-multiplier=1','--loglevel=INFO'])
        launch('api',['uvicorn','app.main:app','--host','0.0.0.0','--port','8000'])
        launch('web',['nginx','-g','daemon off;'],kind='web')
        entry('foreground-worker','feedback-configure')
        launch('game-feedback-worker',['python','-B','-c',fixture_entry,'feedback-worker'])
        wait_for(health_ready,'packaged_feedback_api_not_ready')
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
            operation_id='packaged-feedback:'+uuid.uuid4().hex,release_revision=backend_revision,collector_image=image,
            configuration_fingerprint=hashlib.sha256(json.dumps(observed_config,sort_keys=True).encode()).hexdigest(),
            target_fingerprint=hashlib.sha256(json.dumps(dict(owner=owner,container=pg['Id'],database=database_name),sort_keys=True).encode()).hexdigest(),
            runtimes=observed_runtimes,updated_at=stamp,verified_at=stamp)
        recorded = json.loads(execute('game-feedback-worker','python','-m','app.product_composition.game_feedback_mode',
            'record','--workspace','legacy','--expected-generation','0','--intent-json',json.dumps(mode),'--apply'))
        assert recorded['applied'] and recorded['mode']=='feedback'
        stage = 'actual_browser_start'
        scenario = directory/'browser-scenario.json'
        scenario.write_text(json.dumps(dict(version=1,protected_card_id=case.card_id,
            protected_queue_entry_id=protected['queue_entry_id'],target_card_id=target_id,
            introduced=introduced)))
        node = shutil.which('node')
        cli = ROOT/'node_modules/@playwright/test/cli.js'
        assert node and cli.is_file(), 'packaged_feedback_installed_playwright_required'
        browser_report = browser_evidence/'playwright.json'
        browser_log = (browser_evidence/'runner.log').open('w')
        browser = subprocess.Popen([node,str(cli),'test','tests/browser/real-game-feedback.spec.ts',
            '--config=playwright.config.ts','--project=chromium','--grep',browser_name,'--trace=on'],
            cwd=ROOT,env=os.environ|dict(TEMPO_DOCKER_URL=web_url,TEMPO_PACKAGED_FEEDBACK_SCENARIO=str(scenario),
                TEMPO_CI_REPORT=str(browser_report),TEMPO_TEST_OUTPUT_DIR=str(browser_evidence/'artifacts')),
            stdout=browser_log,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        report['browser_process']=dict(pid=browser.pid,process_group=browser.pid,report=str(browser_report))
        wait_for(lambda:browser_ready('protected'),'packaged_feedback_browser_protected_board_missing')
        inputs['now'] = datetime.now(timezone.utc).timestamp()
        (directory/'provider.json').write_text(json.dumps(inputs))
        (directory/'feedback-start').write_text('actual_mode_verified\n')
        stage = 'provider_first_departure'
        def bounded(value):
            if isinstance(value,str):
                for private in (admin_dsn,reader_dsn,writer_dsn,reader_password,writer_password):
                    value = value.replace(private,'<redacted>')
                return value[:200]
            if isinstance(value,dict): return {key:bounded(item) for key,item in value.items()}
            if isinstance(value,list): return [bounded(item) for item in value[:2]]
            return value
        def actual_delivery():
            with case.transaction() as database:
                source_rows = database.execute_native("""SELECT source_key,version,
                    payload->'quarantined' AS quarantined,payload->>'diagnostic' AS diagnostic,
                    jsonb_build_object('game_id',payload->'normalized'->'game_id',
                        'workspace_id',payload->'normalized'->'workspace_id',
                        'source_revision',payload->'normalized'->'source_revision',
                        'source_digest',payload->'normalized'->'source_digest',
                        'played_at',payload->'normalized'->'played_at') AS normalized_identity,
                    jsonb_build_object('status',payload->'first_departure'->'status',
                        'diagnostic',payload->'first_departure'->'diagnostic',
                        'decision_id',payload->'first_departure'->'decision'->'decision_id',
                        'card_id',payload->'first_departure'->'decision'->'presentation_id',
                        'ply',payload->'first_departure'->'ply') AS selection,
                    payload->>'determined_at' AS determined_at
                    FROM product_feedback_sources
                    WHERE payload->'original'->>'source_game_id'=%s ORDER BY source_key LIMIT 2""",(source_id,)).fetchall()
                rows = database.execute_native("""SELECT delivery.operation_id,delivery.payload
                    FROM product_feedback_deliveries delivery JOIN product_feedback_sources source USING(source_key)
                    WHERE source.payload->'original'->>'source_game_id'=%s
                    ORDER BY delivery.operation_id LIMIT 2""",(source_id,)).fetchall()
                originating = [row[1]['origin_job_id'] for row in rows if row[1].get('origin_job_id')][:2]
                jobs = database.execute_native("""SELECT job_id,payload->>'status' AS status,
                    payload->>'error' AS error,payload->>'continuation' AS continuation,
                    payload->'window_complete' AS window_complete,payload->'pages_committed' AS pages_committed,
                    payload->>'last_checkpoint_at' AS last_checkpoint_at,payload->'attempts' AS attempts,
                    payload->'next_attempt_at' AS next_attempt_at,payload->'spec'->>'stream' AS stream,
                    jsonb_path_query_array(payload,'$.rejections[0 to 1]') AS rejections
                    FROM product_feedback_jobs WHERE job_id=ANY(%s)
                        OR (%s AND payload->'spec'->>'workspace_id'='legacy'
                            AND payload->'spec'->>'provider'='lichess'
                            AND payload->'spec'->>'account'=%s
                            AND payload->'spec'->>'configuration_revision'='1')
                    ORDER BY job_id LIMIT 2""",(originating,not originating,inputs['configuration']['account'].casefold())).fetchall()
            deliveries = []
            for row in rows:
                payload = row[1]
                kind = 'control' if payload.get('control') else 'encounter' if payload.get('encounter') else 'lapse'
                receipt = payload.get('receipt') or {}
                progress = receipt.get('progress') or {}
                deliveries.append(dict(operation_id=row[0],kind=kind,
                    **{key:payload.get(key) for key in ('state','qualification_status','qualification_reason',
                        'qualification_retryable','determined_at','stream','error','attempts','next_attempt_at','origin_job_id')},
                    receipt={key:receipt.get(key) for key in ('status','reason','admission_status','deferred_reason')},
                    progress={key:progress.get(key) for key in ('determined_at','target','first_issued','first_issued_ordinal')}))
            facts = bounded(dict(source_present=bool(source_rows),source_sample_count=len(source_rows),
                source_sample_at_limit=len(source_rows)==2,delivery_sample_count=len(rows),
                delivery_sample_at_limit=len(rows)==2,job_sample_count=len(jobs),
                job_sample_at_limit=len(jobs)==2,sources=[dict(row) for row in source_rows],
                deliveries=deliveries,jobs=[dict(row) for row in jobs]))
            if facts!=report.get('last_delivery_observation'):
                previous = report.get('delivery_observation_changes',0)
                report.setdefault('first_delivery_observation',facts)
                report['last_delivery_observation']=facts
                report['delivery_observation_changes']=min(previous+1,20)
                report['delivery_observation_count_capped']=previous>=20
            assert len(rows)<=1, 'packaged_feedback_duplicate_original_effect'
            if not rows:
                return None
            observed = Delivery.model_validate_json(json.dumps(rows[0][1]))
            return rows[0] if observed.qualification_status=='ready' and observed.determined_at else None
        delivery = wait_for(actual_delivery,'packaged_feedback_original_delivery_missing')
        original_delivery = Delivery.model_validate_json(json.dumps(delivery[1]))
        assert original_delivery.command_kind=='encounter'
        operation_id, original = delivery[0], original_delivery.command.model_dump(mode='json')
        assert original_delivery.operation_id==operation_id and delivery[1]['encounter']==original
        assert original['observation']=='missed' and original['played_uci']=='d2d4' and original['expected_uci']=='e2e4'
        with case.transaction() as database:
            # One statement captures the task generation and event boundary
            # before the browser is released to save its protected attempt.
            # Completion timestamps cannot prove a new refresh was requested.
            pre_save_refresh = dict(database.execute_native("""SELECT clock_timestamp() AS boundary_at,
                task.id AS task_id,task.generation,
                (SELECT COALESCE(MAX(id),0) FROM background_task_events) AS event_id
                FROM (SELECT 1) baseline LEFT JOIN background_tasks task
                    ON task.kind='daily_queue' AND task.deduplication_key='current'""").fetchone())
            refresh_requested_after = pre_save_refresh['boundary_at']
        report['natural_refresh_pre_save_boundary'] = pre_save_refresh
        provider_ready(dict(stage='source',operation_id=operation_id,key=original['key']))
        stage = 'browser_save_prepare_and_reread'
        inspection_marker = wait_for(lambda:browser_ready('inspect'),'packaged_feedback_browser_inspection_missing')
        def study_inspection_state():
            with case.transaction() as database:
                return {table:[dict(row) for row in database.execute_native(statement,parameters).fetchall()]
                    for table,statement,parameters in (
                        ('cards','SELECT id,revision,introduced_at,fsrs_card_json,state,due_date FROM cards WHERE id=ANY(%s) ORDER BY id',([case.card_id,target_id],)),
                        ('reviews','SELECT id,card_id,source_kind,source_ref,reviewed_at FROM reviews WHERE card_id=ANY(%s) ORDER BY id',([case.card_id,target_id],)),
                        ('origins','SELECT * FROM queue_attempt_origins WHERE card_id=ANY(%s) ORDER BY issuance_sequence NULLS FIRST,queue_entry_id',([case.card_id,target_id],)))}
        before_inspection = study_inspection_state()
        provider_ready(dict(stage='inspect',game_id=original['key']['game_id'],key=original['key'],
            disposition='introduced_again' if introduced else 'first_introduction'))
        target_marker = wait_for(lambda:browser_ready('target'),'packaged_feedback_browser_target_missing')
        assert target_marker['target_entry_id']==inspection_marker['target_entry_id']
        assert study_inspection_state()==before_inspection, 'packaged_game_review_changed_study_state'
        report['game_review_inspection']=target_marker['game_review']
        review_detail = target_marker['game_review']['detail']
        assert review_detail['first_departure']['status']=='own_actionable'
        assert review_detail['first_departure']['decision']['presentation_id']==target_id
        assert review_detail['first_departure']['ply']==original['key']['occurrence_ply']==1
        assert review_detail['first_departure']['played_uci']=='d2d4'
        assert review_detail['first_departure']['expected_uci']==['e2e4']
        assert target_marker['game_review']['board_fen']==capture['game']['start_fen']
        actual_receipts = [item for item in review_detail['receipts'] if item['key']==original['key']]
        assert len(actual_receipts)==1 and actual_receipts[0]['progress']['first_issued']['queue_entry_id']==target_marker['target_entry_id']
        protected_review = target_marker['protected_review']
        assert protected_review['card_id']==case.card_id and protected_review['body']['guided'] is False
        assert protected_review['body']['queue_entry_id']==protected['queue_entry_id']
        assert protected_review['response']['persisted'] and protected_review['replay']==protected_review['response']
        before_review = entry('api','feedback-state',operation_id)
        receipt = before_review['receipt']
        assert receipt['key']==original['key'] and receipt['disposition']==('introduced_again' if introduced else 'first_introduction')
        assert before_review['qualification']['selection']['status']=='own_actionable'
        assert before_review['qualification']['selection']['ply']==original['key']['occurrence_ply']==1
        assert before_review['qualification']['selection']['decision']['presentation_id']==target_id
        assert receipt['progress']['target']['card_id']==target_id
        assert receipt['progress']['first_issued']['queue_entry_id']==target_marker['target_entry_id']
        assert (receipt['progress']['first_issued_ordinal']==1 if introduced else
            1<=receipt['progress']['first_issued_ordinal']<=5)
        report['feedback_before_refresh']=bounded(dict(operation_id=operation_id,
            delivery_state=before_review['delivery_state'],key=receipt['key'],
            **{name:receipt.get(name) for name in ('status','outcome','owner_receipt_id','applied_at',
                'disposition','admission_status','deferred_reason')},
            progress={name:receipt['progress'].get(name) for name in ('determined_at','target',
                'introduction_applied_at','schedule_applied_at','scheduling_review_id','remediation_fence','first_issued','first_issued_ordinal')}))
        assert receipt['status']=='applied'
        if introduced:
            assert receipt['progress']['schedule_applied_at'] and receipt['progress']['scheduling_review_id']
            assert receipt['progress']['introduction_applied_at'] is None
        else:
            assert receipt['progress']['introduction_applied_at']
            assert receipt['progress']['scheduling_review_id'] is None
        with case.transaction() as database:
            assert tuple(database.execute_native("""SELECT queue_entry_id,card_id,revision,issuance_sequence,issued_at
                FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s AND revision=%s""",protected_origin[:3]).fetchone())==protected_origin
            assert tuple(database.execute_native('SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1').fetchone())==(0,1)
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='study'",(target_id,)).fetchone()[0]==0
            scheduling = database.execute_native("SELECT id,internal_rating FROM reviews WHERE card_id=%s AND source_kind='game_lapse' ORDER BY id",(target_id,)).fetchall()
            assert len(scheduling)==int(introduced)
            if introduced:
                assert scheduling[0]['id']==receipt['progress']['scheduling_review_id'] and scheduling[0]['internal_rating']=='again'
            assert database.execute_native('SELECT card_id FROM reviews WHERE id=%s',
                (protected_review['response']['review_id'],)).fetchone()[0]==case.card_id
        def refresh_observation(event_generation=None):
            with case.transaction() as database:
                tasks = database.execute_native("""SELECT task.id,task.generation,task.state,task.phase,
                    task.payload_json::jsonb->>'_queue_phase' AS payload_phase,
                    task.payload_json::jsonb->>'queue_date' AS queue_date,
                    task.lease_token IS NOT NULL AS lease_present,task.lease_expires_at,
                    task.attempt_count,task.next_attempt_at,task.last_error,task.updated_at,task.completed_at,
                    task.transaction_timeout_count,task.transaction_timeout_checkpoint,
                    control.work_id IS NOT NULL AS control_present,control.paused,control.promoted,
                    control.phase AS control_phase,control.updated_at AS control_updated_at
                    FROM background_tasks task LEFT JOIN background_activity control
                        ON control.source='durable' AND control.work_id=task.id
                    WHERE task.kind='daily_queue' AND task.deduplication_key='current'
                    ORDER BY task.id LIMIT 2""").fetchall()
                events, phase_events, generation_enqueue = [], {}, None
                if tasks:
                    identity = (tasks[0]['id'],event_generation if event_generation is not None else tasks[0]['generation'])
                    enqueue_row = database.execute_native("""SELECT id,event,phase,created_at
                        FROM background_task_events WHERE task_id=%s AND generation=%s AND event='enqueued'
                        ORDER BY id LIMIT 1""",identity).fetchone()
                    generation_enqueue = dict(enqueue_row) if enqueue_row is not None else None
                    events = database.execute_native("""SELECT id,task_id,generation,event,phase,detail,created_at
                        FROM background_task_events WHERE task_id=%s AND generation=%s
                        ORDER BY id DESC LIMIT 12""",identity).fetchall()
                    phase_events = dict(database.execute_native("""SELECT
                        MIN(id) FILTER (WHERE event='slice_complete' AND phase='reconcile_unseen') AS reconcile_entered,
                        MIN(id) FILTER (WHERE event='slice_complete' AND phase='admit_due') AS reconcile_completed,
                        MIN(id) FILTER (WHERE event='published' AND phase='published') AS published
                        FROM background_task_events WHERE task_id=%s AND generation=%s""",identity).fetchone())
                projections = database.execute_native("""SELECT queue_date,state,generation,refresh_pending,
                    last_error,updated_at,blocked_count FROM queue_projections WHERE queue_date=%s LIMIT 2""",
                    (protected_queue['local_date'],)).fetchall()
                targets = database.execute_native("""SELECT card.id,card.revision,card.repertoire_id,card.content_type,
                    card.introduced_at,queue.id AS queue_entry_id,queue.queue_date,queue.status,
                    queue.admission_kind,queue.cycle,queue.position,
                    (SELECT COUNT(*) FROM reviews WHERE card_id=card.id) AS review_count,
                    EXISTS(SELECT 1 FROM queue_attempt_origins origin WHERE origin.queue_entry_id=queue.id
                        AND origin.card_id=card.id AND origin.revision=card.revision
                        AND origin.issued_as_head=1 AND origin.issuance_sequence IS NOT NULL) AS genuinely_issued
                    FROM cards card LEFT JOIN daily_queue queue ON queue.card_id=card.id AND queue.id=%s
                    WHERE card.id=%s LIMIT 2""",(target_marker['target_entry_id'],target_id)).fetchall()
            return dict(tasks=[bounded(dict(row)) for row in tasks],events=[bounded(dict(row)) for row in events],
                phase_events=phase_events,generation_enqueue=generation_enqueue,
                projections=[bounded(dict(row)) for row in projections],
                targets=[bounded(dict(row)) for row in targets])
        def eligible_target(observed):
            assert len(observed['targets'])==1, 'packaged_feedback_target_missing'
            target = observed['targets'][0]
            assert (target['id']==target_id and target['revision']==receipt['progress']['target']['card_revision']
                and target['queue_entry_id']==target_marker['target_entry_id'] and target['status']=='queued'
                and target['content_type']=='opening' and target['repertoire_id']!='__retained_cards__'
                and (introduced or target['introduced_at']==target['queue_date']) and target['review_count']==int(introduced)
                and (target['admission_kind'] or '')!='explicit' and target['genuinely_issued']), \
                'packaged_feedback_unseen_refresh_target_not_eligible'
            return target
        report['natural_refresh_requested_after']=refresh_requested_after
        report['natural_refresh_before_request']=refresh_observation()
        eligible_target(report['natural_refresh_before_request'])
        baseline_generation, baseline_identity = None, None
        stage = 'natural_refresh_before_explicit_request'
        def natural_refresh():
            nonlocal baseline_generation, baseline_identity
            observed = refresh_observation(baseline_generation)
            if observed!=report.get('last_natural_refresh_observation'):
                report.setdefault('first_natural_refresh_observation',observed)
                report['last_natural_refresh_observation']=observed
                previous = report.get('natural_refresh_observation_changes',0)
                report['natural_refresh_observation_changes']=min(previous+1,20)
                report['natural_refresh_observation_count_capped']=previous>=20
            assert len(observed['tasks'])<=1, 'packaged_feedback_duplicate_natural_refresh_task'
            if not observed['tasks']:
                return None
            task = observed['tasks'][0]
            enqueue = observed['generation_enqueue']
            if baseline_identity is None:
                prior_generation = pre_save_refresh['generation']
                newer_generation = (pre_save_refresh['task_id']==task['id'] and prior_generation is not None
                    and task['generation']>prior_generation)
                requested_after_boundary = (enqueue is not None and enqueue['id']>pre_save_refresh['event_id']
                    and datetime.fromisoformat(enqueue['created_at'].replace('Z','+00:00'))>=refresh_requested_after)
                if not (newer_generation or requested_after_boundary):
                    return None  # An older generation finishing now is insufficient.
                assert enqueue is not None and enqueue['event']=='enqueued', 'packaged_feedback_natural_refresh_enqueue_missing'
                baseline_generation = task['generation']
                baseline_identity = (task['id'],baseline_generation)
                report['natural_refresh_generation_provenance'] = dict(task_id=task['id'],
                    generation=baseline_generation,newer_generation=newer_generation,
                    requested_after_boundary=requested_after_boundary,enqueue=enqueue)
            assert (task['id'],task['generation'])==baseline_identity, 'packaged_feedback_natural_refresh_generation_changed'
            eligible_target(observed)
            assert task['state']!='failed', 'packaged_feedback_natural_refresh_task_failed'
            if task['state']!='complete' or task['phase']!='published':
                return None
            assert len(observed['projections'])==1, 'packaged_feedback_natural_refresh_projection_missing'
            projection = observed['projections'][0]
            if projection['state']!='ready' or projection['refresh_pending']!=0:
                return None
            return dict(**task,projection=projection,target=observed['targets'][0])
        report['natural_refresh_completed'] = wait_for(natural_refresh,'packaged_feedback_natural_refresh_pending')
        report['refresh_control_before_request']=bounded(api('GET','/api/system/foreground-active'))
        provider_ready(dict(stage='request-refresh',baseline_generation=baseline_generation))
        stage = 'browser_requests_fresh_refresh'
        requested_marker = wait_for(lambda:browser_ready('refresh-requested'),'packaged_feedback_browser_refresh_request_missing')
        requested = requested_marker['request']
        report['browser_refresh_request']=requested
        assert requested_marker['target_entry_id']==target_marker['target_entry_id']
        assert requested['settings']==requested['response'] and requested['settings']['new_cards_per_day']==0
        assert requested['settings']['study_only_mode'] is True and requested['identity']
        assert requested['status'] in (200,202)
        stage = 'actual_unseen_refresh_before_browser_completion'
        refresh_identity = None
        def actual_unseen_refresh():
            nonlocal refresh_identity
            observed = refresh_observation(refresh_identity[1] if refresh_identity else None)
            if observed!=report.get('last_requested_refresh_observation'):
                report.setdefault('first_requested_refresh_observation',observed)
                report['last_requested_refresh_observation']=observed
                previous = report.get('requested_refresh_observation_changes',0)
                report['requested_refresh_observation_changes']=min(previous+1,20)
                report['requested_refresh_observation_count_capped']=previous>=20
            assert len(observed['tasks'])<=1, 'packaged_feedback_duplicate_refresh_task'
            if not observed['tasks'] or observed['tasks'][0]['generation']<=baseline_generation:
                return None
            task = observed['tasks'][0]
            identity = (task['id'],task['generation'])
            if refresh_identity is None:
                refresh_identity = identity
                report['requested_refresh_identity']=dict(task_id=identity[0],generation=identity[1],
                    baseline_generation=baseline_generation)
            assert identity==refresh_identity, 'packaged_feedback_refresh_generation_changed'
            eligible_target(observed)
            assert task['state']!='failed', 'packaged_feedback_refresh_task_failed'
            phases = observed['phase_events']
            if not all(phases.get(name) for name in ('reconcile_entered','reconcile_completed','published')):
                return None
            assert phases['reconcile_entered']<phases['reconcile_completed']<phases['published'], \
                'packaged_feedback_refresh_phase_order_invalid'
            if task['state']!='complete' or task['phase']!='published':
                return None
            assert len(observed['projections'])==1, 'packaged_feedback_refresh_projection_missing'
            projection = observed['projections'][0]
            if projection['state']!='ready' or projection['refresh_pending']!=0:
                return None
            return dict(**task,phase_events=phases,projection=projection,target=observed['targets'][0])
        report['actual_sliced_unseen_refresh'] = wait_for(actual_unseen_refresh,'packaged_feedback_unseen_refresh_pending')
        provider_ready(dict(stage='refresh',generation=report['actual_sliced_unseen_refresh']['generation']))
        stage = 'browser_clean_completion_and_replay'
        completed_browser = wait_for(lambda:browser_ready('complete'),'packaged_feedback_browser_completion_missing')
        assert browser.wait(timeout=5)==0, 'packaged_feedback_playwright_failed'
        assert browser_report.is_file(), 'packaged_feedback_playwright_report_missing'
        execution = json.loads(browser_report.read_text())
        executed = []
        def selected_tests(suites):
            for suite in suites:
                selected_tests(suite.get('suites',[]))
                for spec in suite.get('specs',[]):
                    for test in spec.get('tests',[]):
                        executed.append(dict(title=spec['title'],id=test.get('testId') or spec.get('id'),
                            project=test['projectName'],results=test.get('results',[])))
        selected_tests(execution.get('suites',[]))
        assert len(executed)==1 and executed[0]['title']==browser_name and executed[0]['project']=='chromium'
        assert executed[0]['id'] and len(executed[0]['results'])==1 and executed[0]['results'][0]['status']=='passed'
        attachments = executed[0]['results'][0].get('attachments',[])
        assert any(item['name']=='trace' and Path(item['path']).is_file() for item in attachments)
        assert any(item['name']=='actual-browser-correspondence' for item in attachments)
        report['browser_execution']=executed
        target_reviews = [item for item in completed_browser['reviews'] if item['card_id']==target_id]
        clean_reviews = [item for item in target_reviews if item['body']['guided'] is False]
        assert 1<=len(target_reviews)<=2 and len(clean_reviews)==1
        clean = clean_reviews[0]
        assert clean['response']['persisted'] and clean['replay']==clean['response']
        stage = 'same_key_settlement'
        settled = entry('api','feedback-state',operation_id)
        assert settled['command']==original and settled['receipt']['key']==original['key']
        assert settled['receipt']['status']=='settled'
        assert settled['receipt']['progress']['settlement']['review_id']==clean['response']['review_id']
        with case.transaction() as database:
            from tempo_legacy_database.adapters.study.issuance import attempt_issuance
            actual_clean_origin = attempt_issuance(database,clean['body']['queue_entry_id'],target_id,clean['body']['expected_revision'])
            assert actual_clean_origin is not None
            assert settled['receipt']['progress']['settlement']['attempt']==actual_clean_origin.model_dump(mode='json')
            assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind=\'study\'',(target_id,)).fetchone()[0]==len(target_reviews)
            assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind=\'game_lapse\'',(target_id,)).fetchone()[0]==int(introduced)
            assert database.execute_native("SELECT payload->'encounter' FROM product_feedback_deliveries WHERE operation_id=%s",(operation_id,)).fetchone()[0]==original
            report['actual_issuances_after_determination'] = database.execute_native("""SELECT COUNT(*) FROM queue_attempt_origins
                WHERE issued_as_head=1 AND issued_at>%s AND issuance_sequence IS NOT NULL""",
                (receipt['progress']['determined_at'],)).fetchone()[0]
            report['unseen_refresh_task'] = database.execute_native("""SELECT payload_json FROM background_tasks
                WHERE kind='daily_queue' AND deduplication_key='current'""").fetchone()[0]
        # Revisit retained provider history through the existing collection-only
        # worker. It cannot dispatch, and repeated capture must preserve the same
        # original command, receipt, application and genuine settlement.
        stage = 'same_source_reconciliation_replay'
        with case.transaction() as database:
            captures_before_replay = database.execute_native('SELECT COUNT(*) FROM product_feedback_captures').fetchone()[0]
            reconciliation_before = database.execute_native("SELECT COUNT(*) FROM product_feedback_jobs WHERE status='complete' AND payload->'spec'->>'stream'='reconciliation'").fetchone()[0]
        inputs['now'] += 601
        (directory/'provider.json').write_text(json.dumps(inputs))
        replay_run = entry('foreground-worker','worker')
        assert replay_run['durable_checkpoints_retained'] and replay_run['bounded_units']>1
        report['source_replay_run'] = replay_run
        with case.transaction() as database:
            reconciliation_after = database.execute_native("SELECT COUNT(*) FROM product_feedback_jobs WHERE status='complete' AND payload->'spec'->>'stream'='reconciliation'").fetchone()[0]
            assert reconciliation_after>reconciliation_before, 'packaged_feedback_retained_reconciliation_did_not_complete'
            captures_after_replay = database.execute_native('SELECT COUNT(*) FROM product_feedback_captures').fetchone()[0]
            assert captures_after_replay>=captures_before_replay
            assert database.execute_native('SELECT COUNT(*) FROM product_feedback_sources').fetchone()[0]==1
        report['source_replay_capture_counts'] = dict(before=captures_before_replay,after=captures_after_replay)
        replayed = entry('api','feedback-state',operation_id)
        assert replayed['command']==original and replayed['receipt']==settled['receipt']
        with case.transaction() as database:
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",(target_id,)).fetchone()[0]==int(introduced)
        report.update(operation_id=operation_id,key=original['key'],qualification=settled['qualification'],receipt=settled['receipt'],source_replay_receipt=replayed['receipt'])
        report['stages'].append('same_key_settlement')
    except BaseException as error:
        report['first_failure'] = dict(stage=stage,error=type(error).__name__)
        if stage in ('natural_refresh_before_explicit_request','browser_requests_fresh_refresh',
                'actual_unseen_refresh_before_browser_completion'):
            try:
                report['refresh_observation_at_failure']=refresh_observation(
                    baseline_generation if stage=='natural_refresh_before_explicit_request' else
                    refresh_identity[1] if stage=='actual_unseen_refresh_before_browser_completion' and refresh_identity else None)
                report['refresh_control_at_failure']=bounded(api('GET','/api/system/foreground-active'))
            except Exception as diagnostic_error:
                report['refresh_failure_observation_error']=type(diagnostic_error).__name__
        raise
    finally:
        if browser is not None:
            if browser.poll() is None:
                provider_ready(dict(stage='abort'))
                try:
                    browser.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(browser.pid,signal.SIGTERM)
                    try:
                        browser.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(browser.pid,signal.SIGKILL)
                        browser.wait(timeout=5)
            report['browser_exit']=browser.returncode
            try:
                os.killpg(browser.pid,0)
            except ProcessLookupError:
                report['browser_process_group_removed']=True
            else:
                os.killpg(browser.pid,signal.SIGTERM)
                cleanup_deadline = time.monotonic()+5
                while True:
                    try:
                        os.killpg(browser.pid,0)
                    except ProcessLookupError:
                        report['browser_process_group_removed']=True
                        break
                    if time.monotonic()>=cleanup_deadline:
                        os.killpg(browser.pid,signal.SIGKILL)
                        report['browser_process_group_removed']=False
                        break
                    time.sleep(.05)
        if browser_log is not None:
            browser_log.close()
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
        report['cleanup_verified']=len(cleanup)==len(containers) and network is None
        report['runtimes']=cleanup
        if network is not None:
            report['network_cleanup_verified']=False
            (evidence/('feedback-'+owner+'.json')).write_text(json.dumps(report,indent=2,default=str))
            observed_network = json.loads(docker('network','inspect',network['id']).stdout)[0]
            assert observed_network['Id']==network['id'] and observed_network['Name']==network['name']
            assert observed_network['Labels']['tempo.owner']==owner and observed_network['Labels']['tempo.disposable']=='true'
            assert set(observed_network['Containers'])==({pg['Id']} if postgres_attached else set())
            if postgres_attached:
                owned_pg = json.loads(docker('inspect',pg['Id']).stdout)[0]
                assert owned_pg['Config']['Labels']['tempo.owner']==owner and owned_pg['Config']['Labels']['tempo.disposable']=='true'
                docker('network','disconnect',network['id'],pg['Id'])
            docker('network','rm',network['id'])
            absent = docker('network','inspect',network['id'],check=False)
            missing = absent.stderr.lower()
            assert (absent.returncode!=0 and network['id'] in missing
                and ('no such network' in missing or 'network '+network['id']+' not found' in missing))
            report['network_cleanup_verified']=True
            report['cleanup_verified']=len(cleanup)==len(containers)
        evidence = ROOT/'test-results/collection-release'; evidence.mkdir(parents=True,exist_ok=True)
        (evidence/('feedback-'+owner+'.json')).write_text(json.dumps(report,indent=2,default=str))
        assert report.get('browser_process_group_removed',True), 'packaged_feedback_browser_process_group_remained'
