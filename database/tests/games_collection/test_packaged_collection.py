"""Single pinned packaged collection acceptance using the existing PG fixture."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import shlex
import uuid

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

# Reuse the existing disposable setup owner; no second migration/cleanup authority.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend/tests'))
from test_product_game_lapse_postgres import ROOT, owned_postgres, case, retained_owner_case


def test_packaged_collection_retains_deviations_reconciles_and_never_delivers(case, tmp_path):
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
        (evidence/'packaged.json').write_text(json.dumps(dict(candidate=revision,image=image,schema=54,
            fixture_container=pg['Id'],jobs=jobs,counts=counts,receipts=receipts),indent=2))
