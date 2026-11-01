"""Controlled HTTP/clock/admission inputs for the real packaged CLI.

Mount only this fixture and synthetic provider inputs. Production modules are
loaded from the image; no source overlay, successful finding or receipt exists.
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
import time

import httpx

# Fault injection runs the real Celery application/queues after installing one
# bounded observation/barrier around the actual local qualification stage. No
# owner result, receipt, source fact or runtime identity is manufactured.
if len(sys.argv) > 1 and sys.argv[1] in {'recovery-foreground', 'recovery-background'}:
    from celery.signals import task_prerun
    patched = False

    @task_prerun.connect(weak=False)
    def install_preparation_observer(sender=None, args=(), **unused):
        global patched
        selected = (getattr(sender, 'name', '') in {'app.tasks.maintain_game_feedback', 'app.tasks.progress_study_queue'}
            or getattr(sender, 'name', '') == 'app.tasks.execute_foreground_command'
            and len(args) > 1 and args[1] == 'queue.prepare_next')
        if patched or not selected:
            return
        # Install in the executing child, inside the first relevant task's
        # elapsed time. Preloading this owner before prefork would change the
        # actual concurrency memory/cold-import conditions under acceptance.
        from functools import wraps
        import hashlib
        from app.product_composition import provider_priority
        original_qualified_facts = provider_priority._qualified_facts

        @wraps(original_qualified_facts)
        def observed_qualified_facts(*values, **kwargs):
            delivery = kwargs.get('delivery')
            if delivery is not None:
                token = hashlib.sha256(delivery.operation_id.encode()).hexdigest()[:16]
                print(json.dumps(dict(recovery_actual_preparer_start=token)), flush=True)
                path = Path('/qualification/recovery-preparer-fault.json')
                fault = json.loads(path.read_text()) if path.exists() else None
                if fault and fault.get('delivery_operation_id') == delivery.operation_id:
                    assert fault['kind'] == 'wait' and fault['release'] == 'recovery-preparer-release'
                    deadline = time.monotonic() + 180
                    while not Path('/qualification/recovery-preparer-release').exists():
                        if time.monotonic() >= deadline:
                            raise RuntimeError('recovery_actual_preparer_barrier_not_released')
                        time.sleep(.02)
            return original_qualified_facts(*values, **kwargs)

        provider_priority._qualified_facts = observed_qualified_facts
        from app import tasks
        original_queue_slice = tasks.execute_postgres_queue_refresh_slice
        @wraps(original_queue_slice)
        def observed_queue_slice(task):
            more = original_queue_slice(task)
            fault = Path('/qualification/essential-crash-after-slice')
            marker = Path('/qualification/essential-crashed.json')
            if fault.exists() and more:
                try:
                    with marker.open('x') as output:
                        json.dump(dict(task_id=task['id'], generation=task['generation'],
                            phase=task['phase'], lease_token=task['lease_token'], pid=os.getpid(),
                            after_committed_slice=True), output)
                        output.flush()
                        os.fsync(output.fileno())
                except FileExistsError:
                    return more
                os._exit(19)  # Actual child loss after owner commit, before Celery acknowledgment.
            return more
        tasks.execute_postgres_queue_refresh_slice = observed_queue_slice
        patched = True

    from app.celery_app import celery_app
    foreground = sys.argv[1] == 'recovery-foreground'
    celery_app.worker_main(['worker', '--queues=' + ('foreground' if foreground else 'background'),
        '--concurrency=' + ('2' if foreground else '1'), '--prefetch-multiplier=1', '--loglevel=INFO'])
    raise SystemExit(0)

inputs = json.loads(Path('/qualification/provider.json').read_text())
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        from urllib.parse import urlsplit, parse_qs
        url = urlsplit(self.path)
        if inputs.get('fail_lichess') and url.path.startswith('/api/games/user/'):
            self.send_response(503); self.end_headers(); self.wfile.write(b'controlled provider unavailable')
            return
        if url.path.startswith('/api/games/user/'):
            query = parse_qs(url.query)
            rows = [row for row in inputs['lichess'] if int(query['since'][0]) <= row['createdAt'] < int(query['until'][0])]
            body = '\n'.join(json.dumps(row) for row in rows[:int(query['max'][0])]) + '\n'
            kind = 'application/x-ndjson'
        elif url.path.endswith('/archives'):
            body = json.dumps({'archives': ['https://api.chess.com/pub/player/learner/games/2026/09',
                                           'https://api.chess.com/pub/player/learner/games/2026/10']})
            kind = 'application/json'
        else:
            body = json.dumps({'games': inputs['chesscom'].get(url.path[-2:], [])})
            kind = 'application/json'
        self.send_response(200); self.send_header('Content-Type', kind); self.end_headers(); self.wfile.write(body.encode())

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
Thread(target=server.serve_forever, daemon=True).start()
class LocalHTTP(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        request.url = request.url.copy_with(scheme='http', host='127.0.0.1', port=server.server_port)
        return await super().handle_async_request(request)
class Clock:
    def now(self): return inputs['now']
    def utc_now(self): return datetime.fromtimestamp(self.now(), timezone.utc).isoformat()
@contextmanager
def admitted():
    yield  # Only synthetic quiet admission; deployed resource isolation unproved.

@contextmanager
def feedback_admitted():
    global inputs
    # The actual worker is constructed and running its normal loop before the
    # host verifies it. Hold only the controlled provider admission, never lend
    # a waiting generic Python process the worker's service identity.
    deadline = time.monotonic() + 45
    while not Path('/qualification/feedback-start').exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('feedback_fixture_start_not_released')
        time.sleep(.05)
    inputs = json.loads(Path('/qualification/provider.json').read_text())
    yield

try:
    if sys.argv[1] in {'worker', 'feedback-worker'}:
        feedback = sys.argv[1] == 'feedback-worker'
        from app.product_composition import game_sync_current as current
        original = current.current_provider_worker
        actual_admission = current.current_sync_admission
        recovery_runtime = False
        def build(**kwargs):
            runtime = original(**kwargs, clock=Clock(), transport_factory=LocalHTTP)
            if feedback:
                print(json.dumps({'feedback_runtime_prepared': True,
                                  'recovery_runtime_prepared': recovery_runtime}), flush=True)
            return runtime
        current.current_provider_worker = build
        current.current_sync_admission = feedback_admitted if feedback else admitted
        sys.argv = ['worker', *(['--feedback'] if feedback else ['--collect-only']), '--follow', '--overlap-seconds', '0',
            '--reconcile-interval-seconds', '600', '--once', '--max-units', '50']
        current.main()
        if feedback and Path('/qualification/recovery-lifetime').exists():
            deadline = time.monotonic() + 600
            while not Path('/qualification/recovery-configuration-ready').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('recovery_fixture_configuration_not_released')
                time.sleep(.05)
            inputs = json.loads(Path('/qualification/provider.json').read_text())
            recovery_runtime = True
            @contextmanager
            def recovery_admitted():
                global inputs
                deadline = time.monotonic() + 180
                while not Path('/qualification/recovery-producer-start').exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError('recovery_fixture_actual_admission_not_released')
                    time.sleep(.05)
                inputs = json.loads(Path('/qualification/provider.json').read_text())
                # Preserve the original production controls and resource lease.
                # Only HTTP inputs/clock and the start barrier are synthetic.
                with actual_admission():
                    print(json.dumps({'recovery_actual_admission_granted': True}), flush=True)
                    yield
            current.current_sync_admission = recovery_admitted
            sys.argv = ['worker', '--feedback', '--follow', '--overlap-seconds', '0',
                '--reconcile-interval-seconds', '600', '--once', '--max-units', '8']
            current.main()
            print(json.dumps({'recovery_actual_worker_finished': True}), flush=True)
    elif sys.argv[1] in {'feedback-configure', 'recovery-configure'}:
        from app.games.feedback.review import SyncConfiguration
        from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore
        connections = DedicatedGamesConnections(os.environ['TEMPO_DATABASE_WRITE_URL'])
        configuration = SyncConfiguration.model_validate_json(json.dumps(inputs['configuration']))
        store = PostgresReviewStore(connections)
        previous = store.configuration(configuration.workspace_id, configuration.provider) if sys.argv[1] == 'recovery-configure' else None
        store.configure(configuration, previous.revision if previous else 0)
        print(json.dumps({'configured': True, 'revision': configuration.revision,
                          'selected_repertoire_count': len(configuration.repertoire_ids)}))
    elif sys.argv[1] == 'qualify-local-feedback':
        from app.product_composition.provider_priority import CurrentProviderPriorityIntake
        from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
        from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections
        connections = DedicatedGamesConnections(os.environ['TEMPO_DATABASE_WRITE_URL'])
        store = PostgresGamesStore(connections)
        original = store.delivery(sys.argv[2])
        if original is None:
            raise RuntimeError('Original fixture delivery unavailable')
        intake = CurrentProviderPriorityIntake(connections=connections,clock=Clock(),operation_stopped=lambda:False)
        qualified = intake.prepare(original)
        assert qualified.qualification_status == 'ready' and qualified.qualification is not None
        assert qualified.command == original.command and qualified.operation_id == original.operation_id
        store.save_delivery(qualified)
        print(json.dumps({'qualified':True,'operation_id':qualified.operation_id,'determined_at':qualified.determined_at}))
    elif sys.argv[1] == 'hold-integrity':
        from tempo_legacy_database.connections import connection
        from app.services.postgres_integrity import invalidate_integrity_in_transaction
        with connection() as database:
            invalidate_integrity_in_transaction(database, sys.argv[2])
        print(json.dumps({'invalidated': True}))
    elif sys.argv[1] == 'request-integrity':
        from tempo_legacy_database.connections import connection
        from app.services.postgres_integrity import request_integrity_scan_in_transaction
        with connection() as database:
            queued = request_integrity_scan_in_transaction(database, sys.argv[2], int(sys.argv[3]), sys.argv[4])
        print(json.dumps({'task_id':queued['id'], 'generation':queued['generation']}))
    elif sys.argv[1] == 'step-integrity':
        from tempo_legacy_database.adapters import postgres_store
        from app.services.opening_graph_recovery import claim_exact_task
        from app.services.postgres_integrity import execute_postgres_integrity_slice
        from app.services.redis_admission_gate import background_lease
        postgres_store.prepare_authoritative_background_pool()
        complete, phases = False, []
        # A pinned fixture maintenance unit uses the actual integrity handler;
        # it never writes completion/certificate flags or changes global mode.
        for _ in range(16):
            with background_lease(), postgres_store.connection(background=True) as database:
                task = claim_exact_task(database, sys.argv[2], 'integrity_scan', sys.argv[3], int(sys.argv[4]),
                    day=sys.argv[5], graph_generation=int(sys.argv[6]))
            if task is None:
                complete = True
                break
            phases.append(task['phase'])
            execute_postgres_integrity_slice(task)
        print(json.dumps({'complete':complete, 'phases':phases}))
    elif sys.argv[1] == 'retry-local-feedback':
        from app.product_composition.game_sync_current import current_provider_worker
        from app.product_composition.game_worker import run_optional_worker
        from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections
        runtime, schedule = current_provider_worker(workspace='legacy',
            connections=DedicatedGamesConnections(os.environ['TEMPO_DATABASE_WRITE_URL']),
            clock=Clock(), transport_factory=LocalHTTP, delivery_enabled=True)
        units = run_optional_worker(runtime, enqueue_due=lambda:False, stop_when_idle=True, max_units=4)
        print(json.dumps({'bounded_units':units, 'original_local_retry':True}))
    elif sys.argv[1] == 'feedback-capabilities':
        from app.product_composition.game_collection_status import feedback_release_capabilities
        print(json.dumps(feedback_release_capabilities()))
    elif sys.argv[1] == 'feedback-state':
        from app.product_composition.game_review import CurrentStudyReceiptReads, SystemClock
        from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore
        connections = DedicatedGamesConnections(os.environ['TEMPO_DATABASE_READ_URL'], read_only=True)
        delivery = PostgresReviewStore(connections).delivery(sys.argv[2])
        if delivery is None:
            raise RuntimeError('feedback_fixture_delivery_unavailable')
        receipt = CurrentStudyReceiptReads(connections, SystemClock()).encounter_status(delivery.command.key)
        print(json.dumps({'operation_id': delivery.operation_id, 'command': delivery.command.model_dump(mode='json'),
            'qualification': delivery.qualification.model_dump(mode='json') if delivery.qualification else None,
            'delivery_state': delivery.state, 'receipt': receipt.model_dump(mode='json') if receipt else None}))
    elif sys.argv[1] == 'preview':
        from app.product_composition import game_sync_current as current
        sys.argv = ['preview', '--preview', '--follow', '--overlap-seconds', '0']
        current.main()
    else:
        from app.product_composition.game_collection_status import main
        arguments = ['--workspace', 'legacy', '--limit', '20']
        if sys.argv[1] == 'identity-writer':
            os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
        if sys.argv[1] in ('identity', 'identity-writer'):
            arguments.append('--identity-only')
        elif sys.argv[1] == 'status':
            arguments.append('--private')
        raise SystemExit(main(arguments))
finally:
    server.shutdown(); server.server_close()
