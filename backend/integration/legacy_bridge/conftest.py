"""Real disposable backend processes; collected only by the explicit native gate.

No mocks, direct handler invocation or ambient database/Redis URL. The prepared
outer owner supplies one isolated PG/Redis network and the qualified artifact
environment. Each case owns a fresh database, namespaces and process sessions.
"""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
import uuid

import chess
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

def owned_manifest():
    path = Path('/evidence/native-manifest.json')
    value = json.loads(path.read_text())
    if (not re.fullmatch(r'tempo-bridge-backend-[0-9a-f]{32}', value['identity'])
            or os.environ.get('TEMPO_BRIDGE_NATIVE_OWNED') != value['identity']
            or value['postgres_host'] != 'bridge-postgres' or value['redis_host'] != 'bridge-redis'
            or value['pgpass'] != '/evidence/private/pgpass'
            or conninfo_to_dict(value['admin_dsn']) != dict(user='postgres', dbname='postgres',
                host='bridge-postgres', port='5432')):
        raise RuntimeError('Explicit disposable backend manifest required; ambient targets refused')
    if not all(Path(item).is_relative_to('/qualified') or item in ('/source/backend', '/source/scripts')
               for item in value['import_paths']):
        raise RuntimeError('Native import path escaped qualified artifacts/backend source')
    return value


class NativeBackend:
    def __init__(self, manifest, directory):
        self.manifest, self.directory = manifest, directory
        self.case_id = 'bridge_' + uuid.uuid4().hex
        self.database_name = self.case_id
        self.identities = {key: self.case_id + '-' + key for key in ('ruy', 'italian', 'physical', 'protected', 'ordinary', 'game')}
        self.processes, self.handles, self.calls = {}, [], []
        self.created = False
        self.private = directory / 'private'; self.private.mkdir(mode=0o700)
        self.paths = manifest['import_paths']
        self.admin_dsn = make_conninfo(manifest['admin_dsn'], dbname=self.database_name)

    def environment(self, role):
        # Whitelist instead of inheriting another task's URLs, credentials or
        # scheduler configuration. Passwords travel only in owned0600 passfiles.
        result = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
        result.update(PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
            TEMPO_TEST_INSTANCE='disposable', TEMPO_CATALOG_ROOT='/source/public',
            TEMPO_REDIS_URL='redis://bridge-redis:6379/0', TEMPO_REDIS_NAMESPACE=self.case_id + ':broker:',
            TEMPO_ADMISSION_NAMESPACE=self.case_id + ':admission', TEMPO_POSTGRES_POOL_SIZE='2',
            TEMPO_POSTGRES_BACKGROUND_POOL_SIZE='1', TEMPO_TRAINING_TIMINGS='0',
            PGPASSFILE=str(self.private / (role + '.pgpass')))
        if role == 'tempo_reader':
            result['TEMPO_DATABASE_READ_URL'] = make_conninfo(host='bridge-postgres', port=5432,
                dbname=self.database_name, user=role)
        elif role == 'tempo_writer':
            result['TEMPO_DATABASE_READ_URL'] = result['TEMPO_DATABASE_WRITE_URL'] = make_conninfo(
                host='bridge-postgres', port=5432, dbname=self.database_name, user=role)
        return result

    def bootstrap(self, source):
        return [sys.executable, '-I', '-c', 'import sys;sys.path[:0]=' + repr(self.paths) + ';' + source]

    def connection(self, *, role='postgres'):
        return psycopg.connect(make_conninfo(self.admin_dsn, user=role),
            passfile=str(self.private / (role + '.pgpass')), connect_timeout=2,
            options='-c statement_timeout=5000 -c lock_timeout=1000')

    def start(self, key, source, role):
        log = (self.directory / (key + '-' + str(len(self.handles)) + '.log')).open('w')
        self.handles.append(log)
        child = subprocess.Popen(self.bootstrap(source), env=self.environment(role), cwd=self.directory,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.processes[key] = child

    def stop(self, key):
        child = self.processes.pop(key, None)
        if child is None: return
        try: os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait(timeout=3)

    def worker(self, queue):
        self.start(queue, 'from app.celery_app import celery_app;celery_app.worker_main(' + repr([
            'worker', '--pool=solo', '--concurrency=1', '--queues=' + queue,
            '--hostname=' + queue + '@' + self.case_id, '--without-gossip', '--without-mingle', '--loglevel=WARNING']) + ')', 'tempo_writer')

    def request(self, method, path, document=None, headers=None):
        from tempo_a_bridge.cli import NoRedirect
        body = json.dumps(document).encode() if document is not None else None
        request = Request(self.endpoint + path, data=body, method=method,
            headers={'Accept': 'application/json', **({'Content-Type': 'application/json'} if body is not None else {}), **(headers or {})})
        try:
            with build_opener(NoRedirect()).open(request, timeout=4) as response:
                code, data = response.status, response.read(8 * 1024 * 1024 + 1)
        except HTTPError as error:
            code, data = error.code, error.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024: raise RuntimeError('Native HTTP response exceeds bound')
        value = json.loads(data)
        self.calls.append(dict(method=method, path=path, status=code))
        return code, value

    def wait(self, function, *, seconds=20):
        deadline = time.monotonic() + seconds
        last = None
        while time.monotonic() < deadline:
            for key, child in self.processes.items():
                if child.poll() is not None: raise RuntimeError('Owned native process exited: ' + key)
            try:
                last = function()
                if last: return last
            except (URLError, OSError): pass
            time.sleep(.2)
        raise AssertionError('Bounded native wait exhausted; last=' + repr(last))

    def operation(self, operation):
        from urllib.parse import quote
        code, result = self.request('GET', '/api/operations/' + quote(operation, safe=''))
        assert code == 200
        return result

    def completed(self, operation):
        def read():
            receipt = self.operation(operation)
            if receipt['state'] in ('failed', 'blocked'): raise AssertionError(receipt)
            return receipt if receipt['state'] == 'complete' else None
        return self.wait(read)['response']

    def post(self, path, value, operation):
        code, response = self.request('POST', path, value, {'Idempotency-Key': operation})
        assert code in (200, 202), (code, response)
        return self.completed(operation) if code == 202 else response

    def queue(self, limit=2):
        def read():
            code, response = self.request('GET', '/api/queue/window?limit=' + str(limit))
            if code == 503 and response.get('detail') == 'Queue changed or attempt issuance is pending. Retry loading the queue.':
                return None
            assert code == 200, (code, response)
            return response
        return self.wait(read)

    def counts(self):
        tables = ('remediation_deliveries', 'remediation_evidence', 'remediation_contributions',
            'remediation_incident_versions', 'remediation_attempts', 'remediation_review_receipts', 'remediation_settlements')
        with self.connection() as raw:
            result = {table: raw.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table))).fetchone()[0] for table in tables}
            result['target_reviews'] = raw.execute('SELECT COUNT(*) FROM reviews WHERE card_id=%s', (self.identities['physical'],)).fetchone()[0]
            row = raw.execute('SELECT sequence FROM remediation_cards WHERE card_id=%s', (self.identities['physical'],)).fetchone()
            result['frontier'] = row[0] if row else 0
        return result

    def preview(self):
        capture = self.directory / 'capture.json'
        source = ('import runpy;sys.argv=["legacy-capture","--repertoire-id",' + repr(self.identities['ruy']) +
            ',"--game-id",' + repr(self.identities['game']) + ',"--output",' + repr(str(capture)) +
            '];runpy.run_module("app.legacy_game_observation",run_name="__main__")')
        result = subprocess.run(self.bootstrap(source), env=self.environment('tempo_reader'), cwd=self.directory,
            capture_output=True, text=True, timeout=25)
        (self.directory / 'capture.log').write_text(result.stdout + result.stderr)
        assert result.returncode == 0, 'Real backend read capture failed; see capture.log'
        from tempo_a_bridge.legacy_observation import observe_capture
        from tempo_game_evidence import preview_legacy_observation
        observation = observe_capture(json.loads(capture.read_text()))
        preview = preview_legacy_observation(observation)
        (self.directory / 'observation.json').write_text(json.dumps(observation, indent=2))
        (self.directory / 'preview.json').write_text(json.dumps(preview, indent=2))
        assert preview['result'] == 'miss_obligation'
        assert preview['delivery']['obligations'][0]['card_id'] == self.identities['physical']
        return preview

    def seed(self, prior):
        now = datetime.now(timezone.utc); today = date.today().isoformat()
        played = (now - timedelta(days=1)).isoformat(); introduced = (now - timedelta(days=2)).isoformat()
        future = (date.today() + timedelta(days=10)).isoformat()
        identifier = self.identities
        expected = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5', 'a7a6']
        with self.connection() as raw:
            raw.execute('INSERT INTO settings(id,timezone,new_cards_per_day,tactics_new_per_day,study_only_mode) VALUES(1,\'UTC\',0,0,1)')
            raw.execute("INSERT INTO tactic_rotation(id) VALUES(1)")
            for key in ('ruy', 'italian'):
                raw.execute("INSERT INTO repertoires(id,name,source_name,created_at,is_main,new_cards_per_day) VALUES(%s,%s,'native-synthetic',%s,%s,0)",
                    (identifier[key], key, today, int(key == 'ruy')))
                moves = expected if key == 'ruy' else ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'f8c5']
                raw.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,'Synthetic','black',%s,%s,%s)",
                    (identifier[key] + '-route', identifier[key], chess.STARTING_FEN, json.dumps(moves), today))
            for key, color, content, moves in [('physical', 'black', 'opening', expected),
                    ('protected', 'white', 'tactic', ['d2d4']), ('ordinary', 'white', 'tactic', ['c2c4'])]:
                raw.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,introduced_at,content_type,canonical_route_source) VALUES(%s,%s,'prefix',%s,%s,%s,'learning',%s,%s,%s,0)",
                    (identifier[key], identifier['ruy'], chess.STARTING_FEN, json.dumps(moves), color, future, introduced, content))
                raw.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)', (identifier['ruy'], identifier[key]))
            raw.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)", (identifier['ruy'], now.isoformat()))
            raw.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind) VALUES(%s,1,%s,0,%s,%s,%s,%s,'black','prefix')",
                (identifier['ruy'], identifier['ruy'] + '-route', identifier['physical'], ' '.join(chess.STARTING_FEN.split()[:4]), chess.STARTING_FEN, json.dumps(expected)))
            # Synthetic completed owner facts, not activation receipts. The
            # existing graph/integrity algorithms retain their own qualification.
            for kind in ('opening_graph_rebuild', 'integrity_scan'):
                raw.execute("INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,next_attempt_at,created_at,updated_at) VALUES(%s,%s,%s,1,'complete',%s,%s,%s,%s)",
                    (self.case_id + '-' + kind, kind, identifier['ruy'], json.dumps(dict(repertoire_id=identifier['ruy'], graph_generation=1)), now.isoformat(), now.isoformat(), now.isoformat()))
            raw.execute("INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,scan_status,scan_generation,scan_completed_sources,scan_total_sources) VALUES(%s,'clean',%s,'idle',%s,3,3)",
                (identifier['ruy'], now.isoformat(), self.case_id + '-integrity_scan:1'))
            raw.execute("INSERT INTO imported_games(id,provider,provider_game_id,content_hash,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES(%s,'lichess',%s,%s,'synthetic',%s,'rapid',0,'black','draw',%s,%s)",
                (identifier['game'], identifier['game'], 'b' * 64, played, chess.STARTING_FEN, json.dumps([*expected[:-1], 'g8f6'])))
            for position, key in enumerate((('physical', 'protected', 'ordinary') if prior else ('protected', 'ordinary'))):
                raw.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,%s)', (today, identifier[key], position))
            raw.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,updated_at) VALUES(%s,'ready',1,0,%s)", (today, now.isoformat()))

    def initialize(self, prior):
        # Wait only on the owned internal endpoints; no host port or live URL.
        os.environ['PGPASSFILE'] = self.manifest['pgpass']
        def ready():
            try:
                with psycopg.connect(self.manifest['admin_dsn'], connect_timeout=1) as raw: return raw.execute('SELECT 1').fetchone()[0]
            except psycopg.OperationalError: return None
        self.wait(ready)
        with psycopg.connect(self.manifest['admin_dsn'], autocommit=True) as raw:
            raw.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.database_name)))
            self.created = True
            raw.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(self.database_name), sql.Literal('tempo-disposable-postgres-test')))
        admin_pass = Path(self.manifest['pgpass']).read_text().strip().rsplit(':', 1)[-1]
        for role in ('postgres', 'tempo_reader', 'tempo_writer'):
            password = admin_pass if role == 'postgres' else secrets.token_urlsafe(24)
            (self.private / (role + '.password')).write_text(password)
            passfile = self.private / (role + '.pgpass')
            passfile.write_text(f'bridge-postgres:5432:{self.database_name}:{role}:{password}\n'); passfile.chmod(0o600)
            (self.private / (role + '.password')).chmod(0o600)
        from apply_postgres_migrations import apply_migrations
        from provision_postgres_roles import provision
        os.environ['PGPASSFILE'] = str(self.private / 'postgres.pgpass')
        apply_migrations(self.admin_dsn)
        provision(self.admin_dsn, (self.private / 'tempo_reader.password').read_text(), (self.private / 'tempo_writer.password').read_text())
        with self.connection() as raw:
            assert raw.execute('SELECT COUNT(*),MAX(version) FROM tempo_schema_migrations').fetchone() == (50, 50)
            assert raw.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND left(table_name,12)='remediation_'").fetchone()[0] == 12
        self.seed(prior)
        with self.connection(role='tempo_reader') as raw:
            assert raw.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
            assert not raw.execute("SELECT has_table_privilege(current_user,'cards','INSERT')").fetchone()[0]
        with closing(socket.socket()) as reserved:
            reserved.bind(('127.0.0.1', 0)); port = reserved.getsockname()[1]
        self.endpoint = 'http://127.0.0.1:' + str(port)
        self.worker('foreground'); self.worker('background')
        self.start('api', 'import uvicorn;uvicorn.run("app.main:app",host="127.0.0.1",port=' + str(port) + ',workers=1,log_level="warning")', 'tempo_reader')
        def api_ready():
            code, state = self.request('GET', '/api/system/foreground-active')
            return code == 200 and state.get('available') is True and state.get('study_only_mode') is True
        self.wait(api_ready)
        expected_head = self.identities['physical' if prior else 'protected']
        assert self.queue(1)['cards'][0]['id'] == expected_head

    def close(self):
        for key in ('api', 'background', 'foreground'): self.stop(key)
        for handle in self.handles: handle.close()
        if self.created:
            with psycopg.connect(self.manifest['admin_dsn'], passfile=self.manifest['pgpass'], autocommit=True, connect_timeout=2) as raw:
                marker = raw.execute('SELECT shobj_description(oid,\'pg_database\') FROM pg_database WHERE datname=%s', (self.database_name,)).fetchone()
                if marker != ('tempo-disposable-postgres-test',): raise RuntimeError('Owned database marker mismatch; retain for diagnosis')
                raw.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(self.database_name)))
        for path in self.private.iterdir(): path.unlink()
        self.private.rmdir()
        (self.directory / 'process-cleanup.json').write_text(json.dumps(dict(database_dropped=self.created,
            process_sessions_stopped=True, private_passfiles_removed=True, calls=self.calls), indent=2))


@pytest.fixture
def native(request, tmp_path):
    manifest = owned_manifest()
    backend = NativeBackend(manifest, tmp_path)
    try:
        backend.initialize(getattr(request, 'param', None) == 'prior')
        yield backend
    finally:
        backend.close()
