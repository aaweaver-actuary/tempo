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

provider_path = Path('/tmp/tempo-qualification-provider.json')
if not provider_path.exists():
    # Assembly roles receive an immutable initial snapshot in their launch env.
    # The separate bounded collect-only fixture has no live reader at startup.
    initial = os.environ.get('TEMPO_QUALIFICATION_PROVIDER_INITIAL')
    if initial is None:
        initial = Path('/qualification/provider.json').read_text()
    json.loads(initial)
    temporary = provider_path.with_suffix('.next')
    temporary.write_text(initial)
    temporary.replace(provider_path)

def install_r2_current_feedback_guards(path=Path('/qualification/r2-current-feedback.json')):
    """Install deepest guards before consumer imports/closures, never change authority.

    The optional exact-target mutation is controlled fixture SQL immediately
    before the NEW narrow target fence, inside the actual owner transaction.
    Its rejection and the mutation must roll back together; this is not an
    external concurrency simulation or a fabricated readiness/effect result.
    """
    if not path.exists():
        return False
    configuration = json.loads(path.read_text())
    assert (set(configuration) == {'schema_version','target_id','target_revision','expected_key','inject_target_change',
            'inject_configuration_change','lose_owner_ack','hold_accepted_debt'}
        and configuration['schema_version'] == 1 and isinstance(configuration['target_id'],str)
        and type(configuration['target_revision']) is int and configuration['target_revision'] > 0
        and all(type(configuration[name]) is bool for name in ('inject_target_change',
            'inject_configuration_change','lose_owner_ack','hold_accepted_debt')))
    from functools import wraps
    import hashlib
    from app.contracts.product_v02.learning import GameEffectKey
    expected_key = GameEffectKey.model_validate(configuration['expected_key'])

    def forbidden(name):
        def unavailable(*args, **kwargs):
            print(json.dumps({'r2_legacy_capture_forbidden_call':name}), flush=True)
            raise RuntimeError('r2_legacy_capture_forbidden')
        return unavailable

    # Patch the deepest implementation before importing callers that retain
    # aliases. Then patch public projections before importing composition roots.
    from app.services import legacy_game_capture
    legacy_game_capture.capture_source_scope = forbidden('capture_source_scope')
    from app import legacy_game_observation
    legacy_game_observation.capture_source_scope = forbidden('capture_source_scope_alias')
    legacy_game_observation.capture_game = forbidden('capture_game')
    from app.repertoire import snapshot
    snapshot.project_capture = forbidden('snapshot.project_capture')
    from app import repertoire
    repertoire.project_capture = forbidden('repertoire.project_capture')
    from tempo_legacy_database.adapters.study.current_feedback_authority import CurrentQualifiedOwnerDecisions
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentDecisionUnavailable
    current = CurrentQualifiedOwnerDecisions._current_prepared_qualified

    @wraps(current)
    def changed_target_fence(self, command):
        fault = getattr(self,'_r2_prepared_target_fault',None)
        if fault is None:
            return current(self, command)
        assert command.decision.presentation_revision == fault['target_revision'], 'r2_fault_target_revision_changed'
        changed = self.database.execute_native(
            'UPDATE cards SET revision=revision+1 WHERE id=%s AND revision=%s',
            (fault['target_id'],fault['target_revision']))
        assert changed.rowcount == 1, 'r2_fault_target_missing_or_changed'
        try:
            current(self, command)
        except CurrentDecisionUnavailable as error:
            assert error.reason in {'current_owner_integrity_changed','current_owner_card_changed'}, \
                'r2_fault_unexpected_target_refusal'
            print(json.dumps({'r2_current_target_fence_rejected':True,
                'target_token':hashlib.sha256(fault['target_id'].encode()).hexdigest()[:16],
                'reason':error.reason,'controlled_in_transaction_mutation':True}), flush=True)
            raise
        raise RuntimeError('r2_changed_target_was_not_rejected')

    CurrentQualifiedOwnerDecisions._current_prepared_qualified = changed_target_fence
    from app.product_composition import provider_priority
    provider_priority.project_capture = forbidden('provider_priority.project_capture')
    provider_priority._owner_capture = forbidden('provider_priority._owner_capture')
    prepare_owner = provider_priority._prepare_qualified_owner
    @wraps(prepare_owner)
    def prepared_owner(*args, **kwargs):
        prepared = prepare_owner(*args, **kwargs)
        # Read the controlled marker only after real preparation closes every
        # reader, never perform filesystem I/O inside the owner writer.
        fault = json.loads(path.read_text())
        if fault['inject_target_change'] and prepared.command.key == expected_key:
            prepared.prepared_decisions._r2_prepared_target_fault = fault
        if fault['inject_configuration_change'] and prepared.command.key == expected_key:
            # Snapshot the flag outside the writer. Mutate the real synthetic
            # configuration only inside the borrowed owner transaction; its
            # actual final fence must refuse and roll back this change too.
            from dataclasses import replace
            from app.games.feedback.review import ReviewUnavailable
            original_fence = prepared.qualification_fence
            def changed_configuration_fence(database, command, qualification):
                changed = database.execute_native("""UPDATE product_feedback_configuration
                    SET revision=revision+1,payload=jsonb_set(payload,'{revision}',to_jsonb(revision+1))
                    WHERE workspace_id=%s AND provider=%s AND revision=%s""",
                    (command.key.workspace_id,command.key.provider,qualification.configuration_revision))
                assert changed.rowcount == 1, 'r2_configuration_missing_or_changed'
                try:
                    original_fence(database,command,qualification)
                except ReviewUnavailable as error:
                    assert str(error) == 'qualified_configuration_changed', 'r2_unexpected_configuration_refusal'
                    print(json.dumps({'r2_configuration_fence_rejected':True,
                        'configuration_revision':qualification.configuration_revision,
                        'controlled_in_transaction_mutation':True}),flush=True)
                    raise
                raise RuntimeError('r2_changed_configuration_was_not_rejected')
            prepared.prepared_decisions._qualification_fence = changed_configuration_fence
            prepared = replace(prepared,qualification_fence=changed_configuration_fence)
        return prepared
    provider_priority._prepare_qualified_owner = prepared_owner

    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    original_save = PostgresGamesStore.save_delivery
    def save_with_lost_owner_ack(self,delivery,*,serviced=False):
        selected = delivery.command.key == expected_key
        marker = Path('/tmp/r2-owner-ack-lost')
        if (configuration['lose_owner_ack'] and selected and delivery.state == 'acknowledged'
                and delivery.receipt is not None and delivery.receipt.owner_receipt_id):
            try:
                with marker.open('x') as stream:
                    stream.write(delivery.operation_id)
            except FileExistsError:
                pass
            else:
                # accept_qualified has returned only after the real owner commit;
                # do not call the outbox writer for this first acknowledgment.
                print(json.dumps({'r2_owner_ack_lost_after_commit':True,
                    'operation_id':delivery.operation_id,'owner_receipt_id':delivery.receipt.owner_receipt_id}),flush=True)
                raise TimeoutError('r2_owner_ack_lost_after_commit')
        original_save(self,delivery,serviced=serviced)
        if selected and delivery.error == 'owner_acknowledgment_uncertain' and marker.exists():
            # The uncertainty row is now committed and all connections closed.
            # Give the host an observable boundary before genuine same-key retry.
            deadline = time.monotonic()+45
            while not Path('/qualification/r2-release-lost-ack').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('r2_lost_ack_observation_not_released')
                time.sleep(.1)
    PostgresGamesStore.save_delivery = save_with_lost_owner_ack

    qualified_facts = provider_priority._qualified_facts
    @wraps(qualified_facts)
    def hold_actual_accepted_debt(command,*,connections,clock,delivery=None,**kwargs):
        fault = json.loads(path.read_text())
        if fault['hold_accepted_debt'] and command.key == expected_key:
            from app.product_composition.game_review import CurrentStudyReceiptReads
            from app.games.feedback.ports import DeliveryDeferred
            # This fixture barrier runs before the real preparation reads/writer.
            # A genuine owner receipt also closes the intentional lost-ACK gap;
            # pending new commands with no accepted owner receipt still proceed.
            receipt = CurrentStudyReceiptReads(connections,clock).encounter_status(command.key)
            if receipt is not None:
                raise DeliveryDeferred('r2_accepted_debt_fixture_barrier',retry_after=1)
        return qualified_facts(command,connections=connections,clock=clock,delivery=delivery,**kwargs)
    provider_priority._qualified_facts = hold_actual_accepted_debt
    print(json.dumps({'r2_current_feedback_guards_installed':True,'mode':sys.argv[1]}), flush=True)
    return True


r2_current_feedback_guards = install_r2_current_feedback_guards()

from app.product_composition.database_infrastructure import bind_database_infrastructure
bind_database_infrastructure()


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
        if r2_current_feedback_guards:
            print(json.dumps({'r2_current_feedback_child_guard':True,'pid':os.getpid()}), flush=True)
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
            marker = Path('/tmp/tempo-essential-crashed.json')
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
        '--concurrency=' + ('2' if foreground else '1'), '--prefetch-multiplier=1', '--loglevel=INFO',
        *(['--beat', '--schedule=/tmp/qualification-celerybeat-schedule'] if foreground else [])])
    raise SystemExit(0)

inputs = json.loads(provider_path.read_text())
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
    def now(self): return max(inputs['now'], time.time())
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
    inputs = json.loads(provider_path.read_text())
    yield

try:
    if sys.argv[1] in {'worker', 'feedback-worker'}:
        feedback = sys.argv[1] == 'feedback-worker'
        if feedback and r2_current_feedback_guards:
            print(json.dumps({'r2_current_feedback_producer_guard':True}), flush=True)
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
            '--reconcile-interval-seconds', '600',
            *([] if feedback and r2_current_feedback_guards else ['--once','--max-units','50'])]
        current.main()
        if feedback and Path('/qualification/recovery-lifetime').exists():
            deadline = time.monotonic() + 600
            while not Path('/qualification/recovery-configuration-ready').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('recovery_fixture_configuration_not_released')
                time.sleep(.05)
            inputs = json.loads(provider_path.read_text())
            recovery_runtime = True
            @contextmanager
            def recovery_admitted():
                global inputs
                deadline = time.monotonic() + 180
                while not Path('/qualification/recovery-producer-start').exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError('recovery_fixture_actual_admission_not_released')
                    time.sleep(.05)
                inputs = json.loads(provider_path.read_text())
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
