"""Periodic and continuation signals cannot accumulate behind one worker."""
from contextlib import nullcontext
from types import SimpleNamespace

from celery import Celery
import pytest
import redis

from app.celery_app import celery_app
from app.services import background_wakes


@pytest.fixture
def wake_broker(monkeypatch):
    background_wakes.client.cache_clear()
    published = []
    values = {}
    class Server:
        def __init__(self):
            self.metadata = {}
            self.queues = {}
            self.unacked = {}

        def lpush(self, queue, message):
            self.queues.setdefault(queue, []).insert(0, message)

        def eval(self, script, key_count, *parameters):
            keys, arguments = parameters[:key_count], parameters[key_count:]
            key, metadata_key = keys[:2]
            token = arguments[0]
            metadata = self.metadata.get(metadata_key, {})
            if script.startswith('-- reserve'):
                requirement = arguments[1] if len(arguments) > 1 else '0'
                if metadata.get('phase') in ('reserved', 'published') and metadata.get('queue_refresh_wake') == '1':
                    requirement = '1'
                if key in values:
                    if metadata.get('token') == values[key] and requirement == '1':
                        metadata['queue_refresh_wake'] = '1'
                    tracked = (metadata.get('token') == values[key]
                               and metadata.get('phase') == 'published')
                    if not tracked or (metadata['message'] in self.queues.get(metadata['queue'], [])
                                       or metadata['tag'] in self.unacked):
                        return [0, values[key]]
                values[key] = token
                self.metadata[metadata_key] = {'token': token, 'phase': 'reserved', 'queue_refresh_wake': requirement}
                return [1, token]
            if script.startswith('-- publish'):
                if metadata.get('token') != token or metadata.get('phase') not in ('reserved', 'published'):
                    return 0
                if values.get(key, token) != token:
                    return 0
                if metadata['phase'] == 'published':
                    if values.get(key) != token:
                        return 0
                    if metadata['message'] in self.queues.get(metadata['queue'], []):
                        return 1
                queue, unacked_key = keys[2:]
                message, tag, direction = arguments[1:]
                if direction == 'LPUSH':
                    self.lpush(queue, message)
                else:
                    self.queues.setdefault(queue, []).append(message)
                values[key] = token
                self.metadata[metadata_key] = dict(metadata, token=token, phase='published',
                                                  queue=queue, message=message, tag=tag, unacked=unacked_key)
                return 1
            if script.startswith('-- abort'):
                if metadata.get('token') != token or metadata.get('phase') != 'reserved':
                    return 0
                if values.get(key) == token:
                    del values[key]
                metadata['phase'] = 'aborted'
                return 1
            if values.get(key) != token:
                return [0, 0]
            requirement = int(metadata.get('queue_refresh_wake', '0'))
            del values[key]
            if metadata.get('token') == token:
                self.metadata[metadata_key] = {'token': token, 'phase': 'consumed'}
            return [1, requirement]
    server = Server()
    monkeypatch.setattr(redis.Redis, 'from_url', lambda *args, **kwargs: server)
    channel = object.__new__(background_wakes.WakeRedisChannel)
    monkeypatch.setattr(channel, 'conn_or_acquire', lambda: nullcontext(server))
    def publish(_app, name, args=None, kwargs=None, **options):
        identifier = options.get('task_id', str(len(published)))
        message = {'headers': {'task': name, **options.get('headers', {})},
                   'properties': {'delivery_tag': identifier, 'delivery_info': {}}}
        before = sum(map(len, server.queues.values()))
        channel._put(options.get('queue', 'background'), message)
        if sum(map(len, server.queues.values())) > before:
            published.append((name, args, kwargs, options))
        return SimpleNamespace(id=identifier)
    monkeypatch.setattr(Celery, 'send_task', publish)
    monkeypatch.setattr(celery_app, 'AsyncResult', lambda identifier: SimpleNamespace(id=identifier))
    yield published, values
    background_wakes.client.cache_clear()


def test_periodic_and_continuation_wakes_share_one_pending_delivery(wake_broker):
    published, _values = wake_broker
    results = [celery_app.send_task('app.tasks.poll_background_tasks', queue='background')
               for _ in range(1000)]
    assert len(published) == 1
    assert len({result.id for result in results}) == 1


def test_wake_delivery_releases_one_slot_and_rejects_obsolete_replay(wake_broker):
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    first = celery_app.send_task(task_name)
    assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, first.id)
    next_wake = celery_app.send_task(task_name)
    assert next_wake.id != first.id and len(published) == 2
    assert not background_wakes.consume_wake(celery_app.conf.broker_url, task_name, first.id)
    assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, None)
    assert next_wake.id in values.values()
    assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, next_wake.id)
    assert not values


def test_wake_classes_are_independent_and_payload_commands_never_coalesce(wake_broker):
    published, _values = wake_broker
    for task_name in background_wakes.WAKE_TASK_NAMES:
        for _ in range(10):
            celery_app.send_task(task_name)
    assert len(published) == 4
    for _ in range(10):
        celery_app.send_task('app.tasks.execute_background_command', args=['receipt', 'command', {}])
        celery_app.send_task('app.tasks.poll_background_tasks', args=['legacy-payload'])
    assert len(published) == 24
    assert all(background_wakes.WAKE_HEADER not in options.get('headers', {})
               for _name, _args, _kwargs, options in published[4:])


def test_failed_wake_publication_releases_only_its_reservation(wake_broker, monkeypatch):
    from kombu.exceptions import OperationalError
    _published, values = wake_broker
    original = Celery.send_task
    def unavailable(*args, **kwargs):
        raise OperationalError('controlled failure')
    monkeypatch.setattr(Celery, 'send_task', unavailable)
    with pytest.raises(OperationalError):
        celery_app.send_task('app.tasks.poll_background_tasks')
    assert not values
    monkeypatch.setattr(Celery, 'send_task', original)
    assert celery_app.send_task('app.tasks.poll_background_tasks').id in values.values()


def test_wake_redis_outage_retains_durable_intent_without_publishing(wake_broker, monkeypatch):
    from kombu.exceptions import OperationalError
    published, _values = wake_broker
    def unavailable(*args):
        raise redis.ConnectionError('controlled Redis outage')
    monkeypatch.setattr(background_wakes.client(celery_app.conf.broker_url), 'eval', unavailable)
    with pytest.raises(OperationalError, match='durable work is retained'):
        celery_app.send_task('app.tasks.poll_background_tasks')
    assert not published


def test_wake_consumer_retries_unavailable_ownership_without_running(wake_broker, monkeypatch):
    from app import tasks
    calls = []
    task = tasks.poll_background_tasks
    def unavailable(*args):
        raise redis.ConnectionError('controlled outage')
    monkeypatch.setattr(background_wakes, 'consume_wake', unavailable)
    class SameDeliveryRetry(Exception):
        pass
    def retry(**options):
        calls.append((task.request.id, task.request.headers, options))
        return SameDeliveryRetry()
    monkeypatch.setattr(task, 'retry', retry)
    monkeypatch.setattr(task, 'run', lambda *args: pytest.fail('unknown ownership cannot run'))
    task.push_request(id='owned-delivery', headers={background_wakes.WAKE_HEADER: 'owned-delivery'})
    try:
        with pytest.raises(SameDeliveryRetry):
            task()
    finally:
        task.pop_request()
    assert calls[0][:2] == ('owned-delivery', {background_wakes.WAKE_HEADER: 'owned-delivery'})
    assert calls[0][2]['countdown'] == 1 and task.max_retries is None


def test_fast_wake_execution_preserves_newer_pending_ownership(wake_broker, monkeypatch):
    published, values = wake_broker
    original = Celery.send_task
    task_name = 'app.tasks.poll_background_tasks'
    def fast_publish(app, name, args=None, kwargs=None, **options):
        result = original(app, name, args, kwargs, **options)
        assert background_wakes.consume_wake(app.conf.broker_url, name, result.id)
        values[background_wakes._KEY_PREFIX + name] = 'newer-owner'
        return result
    monkeypatch.setattr(Celery, 'send_task', fast_publish)
    result = celery_app.send_task(task_name)
    assert result.id == published[0][3]['task_id']
    assert values[background_wakes._KEY_PREFIX + task_name] == 'newer-owner'


def deliver_wake(token, monkeypatch, useful_executions):
    """Exercise the actual consumer wrapper without maintenance database work."""
    from app import tasks
    task = tasks.poll_background_tasks
    monkeypatch.setattr(task, 'run', lambda: useful_executions.append(token) or True)
    task.push_request(id=token, headers={background_wakes.WAKE_HEADER: token})
    try:
        return task()
    finally:
        task.pop_request()


def test_slow_publication_preserves_newer_owner_and_fences_obsolete_execution(wake_broker, monkeypatch):
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    ownership_key = background_wakes._KEY_PREFIX + task_name
    original_publish = Celery.send_task
    useful_executions = []
    def pause_first_publication(app, name, args=None, kwargs=None, **options):
        if options['task_id'] == 'slow-A':
            assert values[ownership_key] == 'slow-A'
            del values[ownership_key]  # advance the reservation clock without sleeping
            celery_app.send_task(task_name, task_id='new-B')
        return original_publish(app, name, args, kwargs, **options)
    monkeypatch.setattr(Celery, 'send_task', pause_first_publication)
    celery_app.send_task(task_name, task_id='slow-A')
    assert values[ownership_key] == 'new-B'
    assert not deliver_wake('slow-A', monkeypatch, useful_executions)
    assert values[ownership_key] == 'new-B'
    assert deliver_wake('new-B', monkeypatch, useful_executions)
    assert useful_executions == ['new-B']
    celery_app.send_task(task_name, task_id='following-C')
    assert values[ownership_key] == 'following-C'
    assert [options['task_id'] for _, _, _, options in published] == ['new-B', 'following-C']


def test_repeated_expired_publications_cannot_accumulate_unowned_deliveries(wake_broker, monkeypatch):
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    ownership_key = background_wakes._KEY_PREFIX + task_name
    original_publish = Celery.send_task
    def expire_before_enqueue(app, name, args=None, kwargs=None, **options):
        del values[ownership_key]
        return original_publish(app, name, args, kwargs, **options)
    monkeypatch.setattr(Celery, 'send_task', expire_before_enqueue)
    results = [celery_app.send_task(task_name) for _ in range(100)]
    assert len(published) == 1
    assert len({result.id for result in results}) == 1
    useful_executions = []
    assert deliver_wake(results[0].id, monkeypatch, useful_executions)
    assert useful_executions == [results[0].id]
    assert ownership_key not in values


def test_late_publication_cannot_revive_after_successor_consumption(wake_broker, monkeypatch):
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    ownership_key = background_wakes._KEY_PREFIX + task_name
    original_publish = Celery.send_task
    useful_executions = []
    def publish_successor_then_resume(app, name, args=None, kwargs=None, **options):
        if options['task_id'] == 'slow-A':
            del values[ownership_key]
            celery_app.send_task(task_name, task_id='new-B')
            assert deliver_wake('new-B', monkeypatch, useful_executions)
        return original_publish(app, name, args, kwargs, **options)
    monkeypatch.setattr(Celery, 'send_task', publish_successor_then_resume)
    celery_app.send_task(task_name, task_id='slow-A')
    assert ownership_key not in values
    assert not deliver_wake('slow-A', monkeypatch, useful_executions)
    assert useful_executions == ['new-B']
    assert [options['task_id'] for _, _, _, options in published] == ['new-B']


def test_stranded_persistent_wake_recovers_on_next_request(wake_broker, monkeypatch):
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    first = celery_app.send_task(task_name, task_id='lost-delivery')
    published.clear()  # broker removed delivery, interrupted before unacked registration
    background_wakes.client(celery_app.conf.broker_url).queues.clear()
    replacement = celery_app.send_task(task_name, task_id='recovery')
    assert replacement.id != first.id
    assert len(published) == 1
    assert values[background_wakes._KEY_PREFIX + task_name] == replacement.id
    useful_executions = []
    assert not deliver_wake(first.id, monkeypatch, useful_executions)
    assert deliver_wake(replacement.id, monkeypatch, useful_executions)
    assert useful_executions == [replacement.id]


def test_committed_wake_survives_publication_response_failure(wake_broker, monkeypatch):
    from kombu.exceptions import OperationalError
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    original_publish = Celery.send_task
    def lose_publication_response(app, name, args=None, kwargs=None, **options):
        original_publish(app, name, args, kwargs, **options)
        raise OperationalError('publication committed, response lost')
    monkeypatch.setattr(Celery, 'send_task', lose_publication_response)
    with pytest.raises(OperationalError, match='response lost'):
        celery_app.send_task(task_name, task_id='committed')
    assert len(published) == 1
    assert values.get(background_wakes._KEY_PREFIX + task_name) == 'committed'
    useful_executions = []
    assert deliver_wake('committed', monkeypatch, useful_executions)
    assert useful_executions == ['committed']


def test_failed_slow_publisher_cannot_cancel_newer_owner(wake_broker, monkeypatch):
    from kombu.exceptions import OperationalError
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    ownership_key = background_wakes._KEY_PREFIX + task_name
    original_publish = Celery.send_task
    def fail_after_successor(app, name, args=None, kwargs=None, **options):
        if options['task_id'] == 'slow-A':
            del values[ownership_key]
            celery_app.send_task(task_name, task_id='new-B')
            raise OperationalError('old producer failed late')
        return original_publish(app, name, args, kwargs, **options)
    monkeypatch.setattr(Celery, 'send_task', fail_after_successor)
    with pytest.raises(OperationalError, match='failed late'):
        celery_app.send_task(task_name, task_id='slow-A')
    assert values[ownership_key] == 'new-B'
    assert len(published) == 1
    useful_executions = []
    assert deliver_wake('new-B', monkeypatch, useful_executions)
    assert useful_executions == ['new-B']


def test_existing_unacked_wake_remains_authoritative(wake_broker):
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    first = celery_app.send_task(task_name)
    server = background_wakes.client(celery_app.conf.broker_url)
    metadata = server.metadata[background_wakes.ownership_keys(task_name)[1]]
    server.queues.clear()
    server.unacked[metadata['tag']] = metadata['message']
    assert celery_app.send_task(task_name).id == first.id
    assert len(published) == 1
    assert values[background_wakes._KEY_PREFIX + task_name] == first.id


def test_payload_kwargs_callbacks_and_unmarked_tokens_keep_standard_transport(wake_broker):
    published, _values = wake_broker
    for _ in range(3):
        celery_app.send_task('app.tasks.poll_background_tasks', kwargs={'legacy': 'payload'})
        celery_app.send_task('app.tasks.result_callback', args=['receipt', {'result': True}])
        celery_app.send_task('app.tasks.poll_background_tasks', headers={background_wakes.WAKE_HEADER: 'legacy-token'})
    assert len(published) == 9
    assert all(background_wakes._PUBLICATION_HEADER not in options.get('headers', {})
               for _, _, _, options in published)


def test_consumed_metadata_is_bounded_and_rejects_late_publication(wake_broker):
    _published, _values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    server = background_wakes.client(celery_app.conf.broker_url)
    for _ in range(100):
        current = celery_app.send_task(task_name)
        assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, current.id)
    assert len(server.metadata) == 1
    assert server.metadata[background_wakes.ownership_keys(task_name)[1]] == {
        'token': current.id, 'phase': 'consumed',
    }

    publication_count = len(_published)
    celery_app.send_task(task_name, task_id=current.id, headers={
        background_wakes.WAKE_HEADER: current.id, background_wakes._PUBLICATION_HEADER: True,
    })
    assert len(_published) == publication_count


def test_queue_refresh_capacity_marker_survives_a_pending_generic_poll(wake_broker, monkeypatch):
    from app import tasks

    published, values = wake_broker
    task = tasks.poll_background_tasks
    generic = celery_app.send_task(task.name, queue='background')
    marked = celery_app.send_task(task.name, queue='background', headers={'queue_refresh_wake': True})
    assert generic.id == marked.id
    assert len(published) == 1 and len(values) == 1
    effective_headers = []
    monkeypatch.setattr(task, 'run', lambda: effective_headers.append(dict(task.request.headers)) or True)
    task.push_request(id=generic.id, headers=dict(published[0][3]['headers']))
    try:
        assert task()
    finally:
        task.pop_request()
    assert effective_headers[0].get('queue_refresh_wake') is True


@pytest.mark.parametrize('first_marked,second_marked', [(False, False), (False, True), (True, False), (True, True)])
def test_coalesced_poll_requirements_join_without_downgrade(wake_broker, first_marked, second_marked):
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    first = celery_app.send_task(task_name, headers={'queue_refresh_wake': first_marked})
    second = celery_app.send_task(task_name, headers={'queue_refresh_wake': second_marked})
    assert first.id == second.id and len(published) == len(values) == 1
    headers = {}
    assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, first.id, headers)
    assert (headers.get('queue_refresh_wake') is True) == (first_marked or second_marked)
    following = celery_app.send_task(task_name)
    headers = {}
    assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, following.id, headers)
    assert 'queue_refresh_wake' not in headers  # consumed requirements do not self-perpetuate


@pytest.mark.parametrize('lost_phase', ['reserved', 'published'])
def test_marked_requirement_transfers_to_recovered_owner(wake_broker, lost_phase):
    published, values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    server = background_wakes.client(celery_app.conf.broker_url)
    keys = background_wakes.ownership_keys(task_name)
    if lost_phase == 'reserved':
        server.eval(background_wakes._RESERVE, 2, *keys, 'crashed', '1')
        del values[keys[0]]
    else:
        celery_app.send_task(task_name, task_id='crashed', headers={'queue_refresh_wake': True})
        server.queues.clear()  # interrupted pop before unacked registration
        published.clear()
    recovered = celery_app.send_task(task_name)
    assert recovered.id != 'crashed' and len(published) == 1
    headers = {}
    assert not background_wakes.consume_wake(celery_app.conf.broker_url, task_name, 'crashed', headers)
    assert values[keys[0]] == recovered.id
    assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, recovered.id, headers)
    assert headers['queue_refresh_wake'] is True


def test_marked_request_upgrades_reserved_slow_publisher(wake_broker, monkeypatch):
    published, _values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    original_publish = Celery.send_task

    def upgrade_before_enqueue(app, name, args=None, kwargs=None, **options):
        assert celery_app.send_task(name, headers={'queue_refresh_wake': True}).id == options['task_id']
        return original_publish(app, name, args, kwargs, **options)

    monkeypatch.setattr(Celery, 'send_task', upgrade_before_enqueue)
    result = celery_app.send_task(task_name)
    assert len(published) == 1
    headers = {}
    assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, result.id, headers)
    assert headers['queue_refresh_wake'] is True


def test_upgraded_unacked_poll_retains_semantics_through_restoration(wake_broker):
    published, _values = wake_broker
    task_name = 'app.tasks.poll_background_tasks'
    result = celery_app.send_task(task_name)
    server = background_wakes.client(celery_app.conf.broker_url)
    metadata = server.metadata[background_wakes.ownership_keys(task_name)[1]]
    server.queues.clear()
    server.unacked[metadata['tag']] = metadata['message']
    assert celery_app.send_task(task_name, headers={'queue_refresh_wake': True}).id == result.id
    channel = object.__new__(background_wakes.WakeRedisChannel)
    envelope = {'headers': {'task': task_name, **published[0][3]['headers']},
                'properties': {'delivery_tag': metadata['tag'], 'delivery_info': {}}}
    channel._publish_wake('background', envelope, server, leftmost=False)
    assert len(server.queues['background']) == 1
    assert celery_app.send_task(task_name).id == result.id
    headers = {}
    assert background_wakes.consume_wake(celery_app.conf.broker_url, task_name, result.id, headers)
    assert headers['queue_refresh_wake'] is True


def test_upgraded_poll_foreground_denial_retains_only_bounded_marked_opportunity(wake_broker, monkeypatch):
    from app import tasks
    from app.services.activity_gate import BackgroundAdmissionDeferred

    published, _values = wake_broker
    task = tasks.poll_background_tasks
    first = celery_app.send_task(task.name)
    celery_app.send_task(task.name, headers={'queue_refresh_wake': True})
    monkeypatch.setattr(tasks.activity_gate, 'check_background_admission',
                        lambda: (_ for _ in ()).throw(BackgroundAdmissionDeferred('foreground active')))
    monkeypatch.setattr(tasks, 'claim_task', lambda **options: pytest.fail('denied wake cannot claim work'))
    task.push_request(id=first.id, headers=dict(published[0][3]['headers']))
    try:
        assert task() is False
    finally:
        task.pop_request()
    assert len(published) == 2
    delayed = published[-1][3]
    assert delayed['headers']['queue_refresh_wake'] is True
    from datetime import datetime, timezone
    assert 0 < (delayed['eta'] - datetime.now(timezone.utc)).total_seconds() <= 1
    assert celery_app.send_task(task.name).id == delayed['task_id']
    assert len(published) == 2
