"""Broker-side ownership for bounded, payload-free maintenance wake-ups."""
from functools import lru_cache
import uuid

from celery import Celery, Task
from kombu.exceptions import OperationalError as BrokerUnavailable
import redis


WAKE_TASK_NAMES = frozenset({
    'app.tasks.poll_background_tasks', 'app.tasks.recover_operations',
    'app.tasks.recover_active_coverage', 'app.tasks.ensure_daily_queue',
    'app.tasks.monitor_activity_health',
})
WAKE_HEADER = 'tempo-wake-token'
_KEY_PREFIX = 'tempo:wake:'
_RESERVE = """-- reserve
local existing = redis.call('GET', KEYS[1])
if existing then return {0, existing} end
redis.call('SET', KEYS[1], ARGV[1], 'PX', 5000)
return {1, ARGV[1]}
"""
_PERSIST = """-- persist
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('PERSIST', KEYS[1])
return 1
"""
_CONSUME = """-- consume
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


@lru_cache(maxsize=4)
def client(broker_url):
    return redis.Redis.from_url(broker_url, decode_responses=True,
                               socket_connect_timeout=1, socket_timeout=1)


def consume_wake(broker_url, task_name, ownership_token):
    """Consume before work, permitting one subsequent wake during execution.

    Tokenless pre-upgrade messages remain compatible and cannot remove a newer
    owner. Broker retry/redelivery uses the same fenced identity.
    """
    if not ownership_token:
        return True
    return bool(client(broker_url).eval(_CONSUME, 1, _KEY_PREFIX + task_name, ownership_token))


class CoalescingCelery(Celery):
    def send_task(self, name, args=None, kwargs=None, **options):
        headers = options.get('headers') or {}
        if name not in WAKE_TASK_NAMES or args or kwargs or headers.get(WAKE_HEADER):
            return super().send_task(name, args=args, kwargs=kwargs, **options)
        ownership_token = options.get('task_id') or uuid.uuid4().hex
        ownership_key = _KEY_PREFIX + name
        server = client(self.conf.broker_url)
        try:
            reserved, existing_token = server.eval(_RESERVE, 1, ownership_key, ownership_token)
        except redis.RedisError as error:
            raise BrokerUnavailable('Maintenance wake queue is unavailable; durable work is retained') from error
        if not reserved:
            return self.AsyncResult(existing_token)
        try:
            result = super().send_task(name, args=args, kwargs=kwargs, **{
                **options, 'task_id': ownership_token,
                'headers': {**headers, WAKE_HEADER: ownership_token},
            })
            # A pre-publication crash releases its short reservation. A queued
            # message owns the key until delivery, even if the worker is stuck.
            # Redis broker data and these ownership keys must be preserved together.
            # A fast worker may already have consumed this token and queued
            # the next one. A fenced no-op must never remove that newer owner.
            server.eval(_PERSIST, 1, ownership_key, ownership_token)
            return result
        except Exception as error:
            try:
                server.eval(_CONSUME, 1, ownership_key, ownership_token)
            except redis.RedisError:
                pass  # Unpublished reservations expire without sweeping shared keys.
            if isinstance(error, redis.RedisError):
                raise BrokerUnavailable('Maintenance wake queue is unavailable; durable work is retained') from error
            raise


class CoalescedWakeTask(Task):
    max_retries = None

    def __call__(self, *args, **kwargs):
        try:
            consumed = consume_wake(self.app.conf.broker_url, self.name,
                                    (self.request.headers or {}).get(WAKE_HEADER))
        except redis.RedisError as error:
            # Retry the same delivery, never abandon a persistent queued owner.
            raise self.retry(exc=error, countdown=1)
        if not consumed:
            return False
        return super().__call__(*args, **kwargs)
