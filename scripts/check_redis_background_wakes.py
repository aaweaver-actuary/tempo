"""Real broker coalescing proof, restricted to explicitly disposable Redis."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import tasks
from app.celery_app import celery_app
from app.services import background_wakes


def proof_background_wakes():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Wake proof requires explicitly disposable Redis')
    queue_name = 'tempo-wake-proof-' + uuid.uuid4().hex
    broker_url = os.environ['TEMPO_REDIS_URL']
    ownership_prefix = queue_name + ':owner:'
    server = background_wakes.client(broker_url)
    # Kombu's four documented/default Redis priority-list keys. No global scans
    # or deletes: only this invocation's queue and ownership keys are disposable.
    queue_keys = [queue_name + ('\x06\x16' + str(priority) if priority else '')
                  for priority in (0, 3, 6, 9)]
    task_name = tasks.poll_background_tasks.name
    def publish(index):
        if index % 2:
            return tasks.poll_background_tasks.apply_async(queue=queue_name, ignore_result=True).id
        return celery_app.send_task(task_name, queue=queue_name, ignore_result=True).id
    def dequeue():
        messages = [server.lpop(key) for key in queue_keys]
        messages = [json.loads(message) for message in messages if message]
        assert len(messages) == 1, messages
        return messages[0]['headers'][background_wakes.WAKE_HEADER]
    try:
        with patch.object(background_wakes, '_KEY_PREFIX', ownership_prefix):
            with ThreadPoolExecutor(max_workers=8) as producers:
                identifiers = list(producers.map(publish, range(1000)))
            assert len(set(identifiers)) == 1
            assert sum(server.llen(key) for key in queue_keys) == 1
            first_token = dequeue()
            assert first_token == identifiers[0]
            assert server.pttl(ownership_prefix + task_name) == -1
            # New process/client sees the same pending ownership after restart.
            background_wakes.client.cache_clear()
            assert publish(0) == first_token
            assert sum(server.llen(key) for key in queue_keys) == 0
            assert background_wakes.consume_wake(broker_url, task_name, first_token)
            next_token = publish(1)
            assert next_token != first_token
            assert not background_wakes.consume_wake(broker_url, task_name, first_token)
            assert background_wakes.consume_wake(broker_url, task_name, None)
            assert server.get(ownership_prefix + task_name) == next_token
            assert dequeue() == next_token
            assert background_wakes.consume_wake(broker_url, task_name, next_token)
            # Exact pre-publication crash expiry; no wait/TTL increase required.
            server.set(ownership_prefix + task_name, 'crashed-publisher', px=5000)
            assert publish(0) == 'crashed-publisher'
            server.pexpire(ownership_prefix + task_name, 0)
            recovered_token = publish(1)
            assert recovered_token != 'crashed-publisher'
            assert dequeue() == recovered_token
            assert background_wakes.consume_wake(broker_url, task_name, recovered_token)
        print(json.dumps({'test': 'test_real_broker_wakes_coalesce_restart_and_fence_delivery',
                          'requests': 1000, 'pending_messages': 1,
                          'publication_crash_recovery': True, 'obsolete_replay_rejected': True}))
    finally:
        server.delete(*queue_keys, *(ownership_prefix + name for name in background_wakes.WAKE_TASK_NAMES))
        background_wakes.client.cache_clear()


if __name__ == '__main__':
    proof_background_wakes()
