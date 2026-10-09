"""Periodic and continuation signals cannot accumulate behind one worker."""
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
        def eval(self, script, _keys, key, token):
            if script.startswith('-- reserve'):
                if key in values:
                    return [0, values[key]]
                values[key] = token
                return [1, token]
            if values.get(key) != token:
                return 0
            if script.startswith('-- consume'):
                del values[key]
            return 1
    monkeypatch.setattr(redis.Redis, 'from_url', lambda *args, **kwargs: Server())
    def publish(_app, name, args=None, kwargs=None, **options):
        published.append((name, args, kwargs, options))
        return SimpleNamespace(id=options.get('task_id', str(len(published))))
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
    wake_count = len(background_wakes.WAKE_TASK_NAMES)
    assert 'app.tasks.monitor_activity_health' in background_wakes.WAKE_TASK_NAMES
    assert len(published) == wake_count
    for _ in range(10):
        celery_app.send_task('app.tasks.execute_background_command', args=['receipt', 'command', {}])
        celery_app.send_task('app.tasks.poll_background_tasks', args=['legacy-payload'])
    assert len(published) == wake_count + 20
    assert all(background_wakes.WAKE_HEADER not in options.get('headers', {})
               for _name, _args, _kwargs, options in published[wake_count:])


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
