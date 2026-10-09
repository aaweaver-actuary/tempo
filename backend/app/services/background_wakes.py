"""Token-fenced publication and recovery for payload-free maintenance wakes."""
from functools import lru_cache
import uuid

from celery import Celery, Task
from kombu.exceptions import OperationalError as BrokerUnavailable
from kombu.transport.redis import Channel as RedisChannel, Transport as RedisTransport
from kombu.utils.json import dumps
import redis


WAKE_TASK_NAMES = frozenset({
    'app.tasks.poll_background_tasks', 'app.tasks.recover_operations',
    'app.tasks.recover_active_coverage', 'app.tasks.ensure_daily_queue',
})
WAKE_HEADER = 'tempo-wake-token'
_PUBLICATION_HEADER = 'tempo-wake-publication'
_KEY_PREFIX = 'tempo:wake:'

# Five seconds is a producer-crash lease, not a publication deadline. The latest
# token remains in one bounded metadata record after expiry/consumption, fencing
# publishers even when the owner key is absent. No per-delivery keys accumulate.
_RESERVE = """-- reserve
local existing = redis.call('GET', KEYS[1])
if existing then
    if redis.call('HGET', KEYS[2], 'token') ~= existing or
       redis.call('HGET', KEYS[2], 'phase') ~= 'published' then
        return {0, existing}
    end
    local queue = redis.call('HGET', KEYS[2], 'queue')
    local message = redis.call('HGET', KEYS[2], 'message')
    local unacked = redis.call('HGET', KEYS[2], 'unacked')
    local tag = redis.call('HGET', KEYS[2], 'tag')
    if redis.call('HEXISTS', unacked, tag) == 1 or
       redis.call('LPOS', queue, message) then
        return {0, existing}
    end
    -- Exact tracked delivery is absent, including the pop-to-unacked crash gap.
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', 5000)
redis.call('DEL', KEYS[2])
redis.call('HSET', KEYS[2], 'token', ARGV[1], 'phase', 'reserved')
return {1, ARGV[1]}
"""
_PUBLISH = """-- publish
if redis.call('HGET', KEYS[2], 'token') ~= ARGV[1] then return 0 end
local phase = redis.call('HGET', KEYS[2], 'phase')
if phase ~= 'reserved' and phase ~= 'published' then return 0 end
local owner = redis.call('GET', KEYS[1])
if owner and owner ~= ARGV[1] then return 0 end
if phase == 'published' then
    if owner ~= ARGV[1] then return 0 end
    -- A retried/ambiguous publication must not enqueue a second pending copy.
    local queue = redis.call('HGET', KEYS[2], 'queue')
    local message = redis.call('HGET', KEYS[2], 'message')
    if redis.call('LPOS', queue, message) then return 1 end
end
-- Enqueue and persistent ownership share one atomic Redis/AOF operation.
redis.call(ARGV[4], KEYS[3], ARGV[2])
redis.call('SET', KEYS[1], ARGV[1])
redis.call('HSET', KEYS[2], 'phase', 'published', 'queue', KEYS[3],
           'message', ARGV[2], 'tag', ARGV[3], 'unacked', KEYS[4])
return 1
"""
_ABORT = """-- abort
if redis.call('HGET', KEYS[2], 'token') ~= ARGV[1] or
   redis.call('HGET', KEYS[2], 'phase') ~= 'reserved' then return 0 end
if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]) end
redis.call('HSET', KEYS[2], 'phase', 'aborted')
return 1
"""
_CONSUME = """-- consume
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
if redis.call('HGET', KEYS[2], 'token') == ARGV[1] then
    redis.call('DEL', KEYS[2])
    redis.call('HSET', KEYS[2], 'token', ARGV[1], 'phase', 'consumed')
end
return 1
"""


def ownership_keys(task_name):
    ownership_key = _KEY_PREFIX + task_name
    return ownership_key, ownership_key + ':publication'


@lru_cache(maxsize=4)
def client(broker_url):
    return redis.Redis.from_url(broker_url, decode_responses=True,
                               socket_connect_timeout=1, socket_timeout=1)


def consume_wake(broker_url, task_name, ownership_token):
    """Consume before work; retain the latest-token fence for late publishers.

    Tokenless pre-upgrade messages remain compatible and cannot remove a newer
    owner. Broker retry/redelivery uses the same fenced identity.
    """
    if not ownership_token:
        return True
    return bool(client(broker_url).eval(_CONSUME, 2, *ownership_keys(task_name), ownership_token))


def is_owned_publication(message):
    headers = message.get('headers') or {}
    return (headers.get('task') in WAKE_TASK_NAMES and headers.get(WAKE_HEADER)
            and headers.get(_PUBLICATION_HEADER))


class WakeRedisChannel(RedisChannel):
    """Keep Kombu routing/acks; fence only explicitly coalesced wake enqueues."""

    def _publish_wake(self, queue, message, server, leftmost=True):
        priority = self._get_message_priority(message, reverse=False)
        # Kombu prefixes ordinary commands but not EVAL. Store physical key names
        # so the ownership client can check the exact queue/unacked locations.
        queue_key = self.global_keyprefix + self._q_for_pri(queue, priority)
        unacked_key = self.global_keyprefix + self.unacked_key
        headers = message['headers']
        return server.eval(_PUBLISH, 4, *ownership_keys(headers['task']),
                           queue_key, unacked_key, headers[WAKE_HEADER],
                           dumps(message), message['properties']['delivery_tag'],
                           'LPUSH' if leftmost else 'RPUSH')

    def _put(self, queue, message, **kwargs):
        if not is_owned_publication(message):
            return super()._put(queue, message, **kwargs)
        with self.conn_or_acquire() as server:
            return self._publish_wake(queue, message, server)

    def _do_restore_message(self, payload, exchange, routing_key, pipe, leftmost=False):
        if not is_owned_publication(payload):
            return super()._do_restore_message(payload, exchange, routing_key, pipe, leftmost)
        # Runs inside Kombu's existing unacked-removal transaction. Redelivery
        # mutates the envelope, so update its locator in that same transaction.
        payload['headers']['redelivered'] = True
        payload['properties']['delivery_info']['redelivered'] = True
        for queue in self._lookup(exchange, routing_key):
            self._publish_wake(queue, payload, pipe, leftmost)


class WakeRedisTransport(RedisTransport):
    Channel = WakeRedisChannel


class CoalescingCelery(Celery):
    def send_task(self, name, args=None, kwargs=None, **options):
        headers = options.get('headers') or {}
        if name not in WAKE_TASK_NAMES or args or kwargs or headers.get(WAKE_HEADER):
            return super().send_task(name, args=args, kwargs=kwargs, **options)
        ownership_token = options.get('task_id') or uuid.uuid4().hex
        server = client(self.conf.broker_url)
        try:
            reserved, existing_token = server.eval(_RESERVE, 2, *ownership_keys(name), ownership_token)
        except redis.RedisError as error:
            raise BrokerUnavailable('Maintenance wake queue is unavailable; durable work is retained') from error
        if not reserved:
            return self.AsyncResult(existing_token)
        try:
            return super().send_task(name, args=args, kwargs=kwargs, **{
                **options, 'task_id': ownership_token,
                'headers': {**headers, WAKE_HEADER: ownership_token, _PUBLICATION_HEADER: True},
            })
        except Exception as error:
            try:
                # An ambiguous response may follow a committed enqueue. Only a
                # still-unpublished reservation can be cancelled, never its wake.
                server.eval(_ABORT, 2, *ownership_keys(name), ownership_token)
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
