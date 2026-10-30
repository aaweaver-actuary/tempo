"""Token-fenced publication and recovery for payload-free maintenance wakes."""
import uuid

from celery import Celery, Task
from kombu.exceptions import OperationalError as BrokerUnavailable
from kombu.transport.redis import Channel as RedisChannel, Transport as RedisTransport
from kombu.utils.json import dumps
import redis

from tempo_legacy_database.adapters.redis_control.wake_runtime import wakes as storage
from tempo_legacy_database.adapters.redis_control.wake_runtime.wakes import (
    _KEY_PREFIX, _RESERVE, _PUBLISH, _ABORT, _CONSUME, client,
)


WAKE_TASK_NAMES = frozenset({
    'app.tasks.poll_background_tasks', 'app.tasks.recover_operations',
    'app.tasks.recover_active_coverage', 'app.tasks.ensure_daily_queue',
    'app.tasks.maintain_game_feedback',
})
WAKE_HEADER = 'tempo-wake-token'
QUEUE_REFRESH_HEADER = 'queue_refresh_wake'
_POLL_TASK_NAME = 'app.tasks.poll_background_tasks'
_PUBLICATION_HEADER = 'tempo-wake-publication'


def ownership_keys(task_name):
    return storage.ownership_keys(task_name, _KEY_PREFIX)


def consume_wake(broker_url, task_name, ownership_token, request_headers=None):
    """Consume before work; retain the latest-token fence for late publishers.

    Tokenless pre-upgrade messages remain compatible and cannot remove a newer
    owner. Broker retry/redelivery uses the same fenced identity.
    Read stronger requirements in the same atomic operation as consumption, so
    a raced marked producer either upgrades this execution or reserves a successor.
    """
    if not ownership_token:
        return True
    consumed, queue_refresh_required = storage.consume_wake(
        client(broker_url), ownership_keys(task_name), ownership_token,
    )
    if consumed and queue_refresh_required and task_name == _POLL_TASK_NAME and request_headers is not None:
        request_headers[QUEUE_REFRESH_HEADER] = True
    return bool(consumed)


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
        return storage.publish_wake(
            server, ownership_keys(headers['task']), queue_key, unacked_key,
            headers[WAKE_HEADER], dumps(message), message['properties']['delivery_tag'], leftmost,
        )

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
        # Canvas continuations and membership require their own publication.
        if (name not in WAKE_TASK_NAMES or args or kwargs or headers.get(WAKE_HEADER)
                or any(options.get(canvas_option_name) for canvas_option_name in (
                    'link', 'link_error', 'chord', 'chain', 'group_id', 'replaced_task_nesting',
                )) or options.get('group_index') is not None):
            return super().send_task(name, args=args, kwargs=kwargs, **options)
        ownership_token = options.get('task_id') or uuid.uuid4().hex
        server = client(self.conf.broker_url)
        try:
            reserved, existing_token = storage.reserve_wake(
                server, ownership_keys(name), ownership_token,
                '1' if name == _POLL_TASK_NAME and headers.get(QUEUE_REFRESH_HEADER) is True else '0',
            )
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
                storage.abort_wake(server, ownership_keys(name), ownership_token)
            except redis.RedisError:
                pass  # Unpublished reservations expire without sweeping shared keys.
            if isinstance(error, redis.RedisError):
                raise BrokerUnavailable('Maintenance wake queue is unavailable; durable work is retained') from error
            raise


class CoalescedWakeTask(Task):
    max_retries = None

    def __call__(self, *args, **kwargs):
        request_headers = dict(self.request.headers or {})
        try:
            consumed = consume_wake(self.app.conf.broker_url, self.name,
                                    request_headers.get(WAKE_HEADER), request_headers)
        except redis.RedisError as error:
            # Retry the same delivery, never abandon a persistent queued owner.
            raise self.retry(exc=error, countdown=1)
        if not consumed:
            return False
        self.request.headers = request_headers
        return super().__call__(*args, **kwargs)
