"""Real broker proof; only invocation-owned keys in explicitly disposable Redis."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import os
from pathlib import Path
import sys
from threading import Event
import uuid
from unittest.mock import patch

import redis
from celery.exceptions import Retry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import tasks
from app.celery_app import celery_app
from app.services import background_wakes


def disposable_broker():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Wake proof requires explicitly disposable Redis')
    broker_url = os.environ['TEMPO_REDIS_URL']
    return broker_url, background_wakes.client(broker_url)


def queue_keys(queue_name):
    return [queue_name + ('\x06\x16' + str(priority) if priority else '')
            for priority in (0, 3, 6, 9)]


def cleanup(server, queue_name, ownership_prefix, delivery_tags=()):
    # Four exact priority lists, eight exact ownership/metadata keys, and only
    # this proof's unacked fields. No key scans, shared hash deletes or flushes.
    server.delete(*queue_keys(queue_name), '_kombu.binding.' + queue_name, *(key for name in background_wakes.WAKE_TASK_NAMES
                  for key in (ownership_prefix + name, ownership_prefix + name + ':publication')))
    if delivery_tags:
        server.hdel('unacked', *delivery_tags)
        server.zrem('unacked_index', *delivery_tags)
    background_wakes.client.cache_clear()


def proof_background_wakes():
    broker_url, server = disposable_broker()
    queue_name = 'tempo-wake-proof-' + uuid.uuid4().hex
    ownership_prefix = queue_name + ':owner:'
    task_name = tasks.poll_background_tasks.name
    ownership_key = ownership_prefix + task_name
    publication_key = ownership_key + ':publication'
    useful_executions = []
    effective_headers = []
    delivery_tags = set()

    def pending_count():
        return sum(server.llen(key) for key in queue_keys(queue_name))

    def publish(index=0, **options):
        if index % 2:
            return tasks.poll_background_tasks.apply_async(queue=queue_name, ignore_result=True, **options).id
        return celery_app.send_task(task_name, queue=queue_name, ignore_result=True, **options).id

    def dequeue():
        messages = [server.rpop(key) for key in queue_keys(queue_name)]
        messages = [json.loads(message) for message in messages if message]
        assert len(messages) == 1, messages
        delivery_tags.add(messages[0]['properties']['delivery_tag'])
        assert messages[0]['headers']['expires'] is None
        return messages[0]

    def deliver(token):
        task = tasks.poll_background_tasks
        task.push_request(id=token, headers={background_wakes.WAKE_HEADER: token})
        try:
            result = task()
            if result:
                effective_headers.append(dict(task.request.headers))
            return result
        finally:
            task.pop_request()

    def execute_pending(expected_token):
        assert dequeue()['headers'][background_wakes.WAKE_HEADER] == expected_token
        assert deliver(expected_token)
        assert server.get(ownership_key) is None

    def slow_pair(consume_successor=False):
        slow_token, successor_token = uuid.uuid4().hex, uuid.uuid4().hex
        paused, resume = Event(), Event()
        original_put = background_wakes.WakeRedisChannel._put
        def paused_put(channel, queue, message, **options):
            if message['headers'].get(background_wakes.WAKE_HEADER) == slow_token:
                paused.set()
                assert resume.wait(5), 'paused publisher was not released'
            return original_put(channel, queue, message, **options)
        with patch.object(background_wakes.WakeRedisChannel, '_put', paused_put):
            with ThreadPoolExecutor(max_workers=1) as producers:
                slow_publication = producers.submit(publish, 0, task_id=slow_token)
                try:
                    assert paused.wait(5), 'publisher did not reach actual broker enqueue'
                    assert server.get(ownership_key) == slow_token
                    server.pexpire(ownership_key, 0)
                    assert publish(task_id=successor_token, headers={'queue_refresh_wake': True}) == successor_token
                    if consume_successor:
                        execute_pending(successor_token)
                    resume.set()
                    assert slow_publication.result(timeout=5) == slow_token
                finally:
                    resume.set()
        assert not deliver(slow_token)
        if consume_successor:
            assert pending_count() == 0
        else:
            assert server.get(ownership_key) == successor_token
            assert pending_count() == 1
            execute_pending(successor_token)
        assert effective_headers[-1]['queue_refresh_wake'] is True
        assert not deliver(slow_token)

    try:
        with patch.object(background_wakes, '_KEY_PREFIX', ownership_prefix), \
             patch.object(tasks.poll_background_tasks, 'run',
                          lambda: useful_executions.append(tasks.poll_background_tasks.request.id) or True):
            with ThreadPoolExecutor(max_workers=8) as producers:
                identifiers = list(producers.map(publish, range(1000)))
            assert len(set(identifiers)) == 1
            assert pending_count() == 1
            first_token = identifiers[0]
            assert server.pttl(ownership_key) == -1
            assert server.pttl(queue_name) == -1
            background_wakes.client.cache_clear()
            assert publish() == first_token and pending_count() == 1

            # Real Kombu dequeue/ack ledger and both restoration directions.
            with celery_app.connection_for_read() as connection:
                channel = connection.channel()
                for leftmost in (False, True):
                    message = channel.basic_get(queue_name)
                    assert message.headers[background_wakes.WAKE_HEADER] == first_token
                    delivery_tags.add(message.delivery_tag)
                    assert pending_count() == 0
                    assert server.hexists('unacked', message.delivery_tag)
                    assert publish(headers={'queue_refresh_wake': True}) == first_token and pending_count() == 0
                    channel.qos.restore_by_tag(message.delivery_tag, leftmost=leftmost)
                    assert pending_count() == 1
                    assert publish() == first_token and pending_count() == 1
                message = channel.basic_get(queue_name)
                assert deliver(first_token)
                assert effective_headers[-1]['queue_refresh_wake'] is True
                message.ack()
                assert pending_count() == 0

            # Requirements form an OR join, independent of the queued envelope.
            for first_marked, second_marked in ((False, False), (False, True), (True, False), (True, True)):
                token = publish(headers={'queue_refresh_wake': first_marked})
                assert publish(headers={'queue_refresh_wake': second_marked}) == token
                assert pending_count() == 1
                execute_pending(token)
                assert (effective_headers[-1].get('queue_refresh_wake') is True) == (first_marked or second_marked)

            # Linearize upgrades on both sides of consumption while a real
            # Kombu delivery is unacked. Barriers replace timing assumptions.
            for consume_first in (False, True):
                token = publish()
                with celery_app.connection_for_read() as connection:
                    channel = connection.channel()
                    message = channel.basic_get(queue_name)
                    delivery_tags.add(message.delivery_tag)
                    paused, resume = Event(), Event()
                    original_consume = background_wakes.consume_wake

                    def paused_consume(*args, **kwargs):
                        if consume_first:
                            result = original_consume(*args, **kwargs)
                        paused.set()
                        assert resume.wait(5), 'consumer barrier was not released'
                        return result if consume_first else original_consume(*args, **kwargs)

                    with patch.object(background_wakes, 'consume_wake', paused_consume):
                        with ThreadPoolExecutor(max_workers=1) as workers:
                            execution = workers.submit(deliver, token)
                            try:
                                assert paused.wait(5), 'consumer did not reach ownership boundary'
                                successor = publish(headers={'queue_refresh_wake': True})
                                assert (successor != token) == consume_first
                                assert pending_count() == int(consume_first)
                                resume.set()
                                assert execution.result(timeout=5)
                            finally:
                                resume.set()
                    message.ack()
                    assert (effective_headers[-1].get('queue_refresh_wake') is True) == (not consume_first)
                    if consume_first:
                        execute_pending(successor)
                        assert effective_headers[-1]['queue_refresh_wake'] is True
                    assert server.get(ownership_key) is None

            assert background_wakes.consume_wake(broker_url, task_name, None)
            slow_pair()
            slow_pair(consume_successor=True)

            # One serial publisher is enough to expose the old unbounded backlog.
            original_put = background_wakes.WakeRedisChannel._put
            def expired_put(channel, queue, message, **options):
                server.pexpire(ownership_key, 0)
                return original_put(channel, queue, message, **options)
            with patch.object(background_wakes.WakeRedisChannel, '_put', expired_put):
                identifiers = [publish(index) for index in range(100)]
            assert len(set(identifiers)) == 1 and pending_count() == 1
            execute_pending(identifiers[0])

            # A broker-pop interruption, then a late unacked registration of the
            # obsolete delivery, cannot suppress the replacement or remove it.
            stranded_token = publish()
            lost_message = dequeue()
            assert server.pttl(ownership_key) == -1
            replacement_token = publish(1)
            assert replacement_token != stranded_token and pending_count() == 1
            assert not deliver(stranded_token)
            assert server.get(ownership_key) == replacement_token
            with celery_app.connection_for_read() as connection:
                channel = connection.channel()
                obsolete = channel.Message(lost_message, channel=channel)
                channel.qos.append(obsolete, obsolete.delivery_tag)
                channel.qos.restore_by_tag(obsolete.delivery_tag)
                obsolete.ack()
                assert pending_count() == 1
                assert server.get(ownership_key) == replacement_token
            execute_pending(replacement_token)

            # Publication response loss: no cleanup deletion, no retry duplicate.
            original_publish_wake = background_wakes.WakeRedisChannel._publish_wake
            def lost_response(channel, *args, **kwargs):
                original_publish_wake(channel, *args, **kwargs)
                raise redis.ConnectionError('controlled committed response loss')
            committed_token = uuid.uuid4().hex
            with patch.object(background_wakes.WakeRedisChannel, '_publish_wake', lost_response):
                try:
                    publish(task_id=committed_token, retry=False)
                except Exception as error:
                    from kombu.exceptions import OperationalError
                    assert isinstance(error, OperationalError), type(error)
                else:
                    raise AssertionError('controlled publication error was hidden')
            assert server.get(ownership_key) == committed_token and pending_count() == 1
            assert publish(headers={'queue_refresh_wake': True}) == committed_token
            committed_headers = json.loads(server.lindex(queue_name, 0))['headers']
            publish(task_id=committed_token, headers=committed_headers)
            assert pending_count() == 1

            # A real same-token retry after ownership lookup failed: old delivery
            # is unacked; the retry queues one successor with the same identity.
            with celery_app.connection_for_read() as connection:
                channel = connection.channel()
                message = channel.basic_get(queue_name)
                delivery_tags.add(message.delivery_tag)
                task = tasks.poll_background_tasks
                task.push_request(id=committed_token, headers=message.headers,
                                  args=(), kwargs={}, retries=0, called_directly=False,
                                  delivery_info={'exchange': '', 'routing_key': queue_name})
                try:
                    with patch.object(background_wakes, 'consume_wake',
                                      side_effect=redis.ConnectionError('controlled ownership outage')):
                        try:
                            task()
                        except Retry:
                            pass
                        else:
                            raise AssertionError('ownership outage did not retry the same delivery')
                finally:
                    task.pop_request()
                assert pending_count() == 1
                assert publish() == committed_token
                message.ack()
                execute_pending(committed_token)
                assert effective_headers[-1]['queue_refresh_wake'] is True

            # Reserve-only crash recovery and aborted-publication resurrection.
            server.eval(background_wakes._RESERVE, 2, ownership_key, publication_key, 'crashed-publisher')
            assert publish() == 'crashed-publisher'
            server.pexpire(ownership_key, 0)
            recovered_token = publish(1)
            assert recovered_token != 'crashed-publisher'
            execute_pending(recovered_token)
            aborted_token = uuid.uuid4().hex
            server.eval(background_wakes._RESERVE, 2, ownership_key, publication_key, aborted_token)
            assert server.eval(background_wakes._ABORT, 2, ownership_key, publication_key, aborted_token)
            publish(task_id=aborted_token, headers={background_wakes.WAKE_HEADER: aborted_token,
                                                   background_wakes._PUBLICATION_HEADER: True})
            assert pending_count() == 0 and server.get(ownership_key) is None
            following_token = publish()
            assert server.get(ownership_key) == following_token
            assert not deliver(recovered_token)
            execute_pending(following_token)
            assert len(useful_executions) == len(set(useful_executions))
        print(json.dumps({'test': 'test_real_broker_wakes_coalesce_restart_and_fence_delivery',
                          'concurrent_requests': 1000, 'serial_expired_requests': 100,
                          'pending_messages': 1, 'slow_publication_fenced': True,
                          'next_request_stranded_owner_recovery': True,
                          'unacked_and_restore_preserved': True,
                          'committed_response_loss_preserved': True,
                          'same_token_retry_preserved': True,
                          'useful_executions': len(useful_executions)}))
    finally:
        cleanup(server, queue_name, ownership_prefix, delivery_tags)


def prepare_restart_proof(evidence_path):
    _broker_url, server = disposable_broker()
    assert server.config_get('appendonly')['appendonly'] == 'yes'
    assert server.config_get('appendfsync')['appendfsync'] == 'everysec'
    queue_name = 'tempo-wake-proof-' + uuid.uuid4().hex
    ownership_prefix = queue_name + ':owner:'
    task_name = tasks.poll_background_tasks.name
    try:
        with patch.object(background_wakes, '_KEY_PREFIX', ownership_prefix):
            token = celery_app.send_task(task_name, queue=queue_name, ignore_result=True).id
            assert celery_app.send_task(task_name, queue=queue_name, ignore_result=True,
                                        headers={'queue_refresh_wake': True}).id == token
            envelope = server.lindex(queue_name, 0)
            assert server.llen(queue_name) == 1
            assert server.pttl(ownership_prefix + task_name) == -1
            stranded_task = tasks.recover_operations.name
            stranded_token = celery_app.send_task(stranded_task, queue=queue_name, ignore_result=True).id
            # Actual Kombu pop, interrupted before basic_get/QoS.append can run.
            with celery_app.connection_for_read() as connection:
                popped = connection.channel()._get(queue_name)
                # Redis queues are FIFO: first remove/reinsert the intact poll,
                # then remove recovery's delivery and leave its persistent owner.
                assert popped['headers'][background_wakes.WAKE_HEADER] == token
                lost = connection.channel()._get(queue_name)
                assert lost['headers'][background_wakes.WAKE_HEADER] == stranded_token
                server.rpush(queue_name, envelope)
            assert server.get(ownership_prefix + stranded_task) == stranded_token
            assert server.llen(queue_name) == 1
            Path(evidence_path).write_text(json.dumps({'queue': queue_name, 'token': token,
                                                       'message': envelope, 'stranded_token': stranded_token}))
        print(json.dumps({'restart_proof': 'prepared', 'queue': queue_name,
                          'persistence': 'AOF appendfsync everysec'}))
    except BaseException:
        cleanup(server, queue_name, ownership_prefix)
        raise


def verify_restart_proof(evidence_path):
    broker_url, server = disposable_broker()
    evidence = json.loads(Path(evidence_path).read_text())
    queue_name = evidence['queue']
    # Do not trust an evidence file to authorize arbitrary Redis key cleanup.
    assert queue_name.startswith('tempo-wake-proof-')
    assert uuid.UUID(hex=queue_name.removeprefix('tempo-wake-proof-')).hex == queue_name.removeprefix('tempo-wake-proof-')
    ownership_prefix = queue_name + ':owner:'
    task_name = tasks.poll_background_tasks.name
    try:
        with patch.object(background_wakes, '_KEY_PREFIX', ownership_prefix):
            assert server.get(ownership_prefix + task_name) == evidence['token']
            assert server.pttl(ownership_prefix + task_name) == -1
            assert server.llen(queue_name) == 1
            assert server.lindex(queue_name, 0) == evidence['message']
            assert celery_app.send_task(task_name, queue=queue_name).id == evidence['token']
            assert server.llen(queue_name) == 1
            assert json.loads(server.rpop(queue_name))['headers'][background_wakes.WAKE_HEADER] == evidence['token']
            headers = {}
            assert background_wakes.consume_wake(broker_url, task_name, evidence['token'], headers)
            assert headers['queue_refresh_wake'] is True
            stranded_task = tasks.recover_operations.name
            assert server.get(ownership_prefix + stranded_task) == evidence['stranded_token']
            assert server.pttl(ownership_prefix + stranded_task) == -1
            recovered = celery_app.send_task(stranded_task, queue=queue_name, ignore_result=True).id
            assert recovered != evidence['stranded_token']
            assert server.llen(queue_name) == 1
            assert not background_wakes.consume_wake(broker_url, stranded_task, evidence['stranded_token'])
            assert json.loads(server.rpop(queue_name))['headers'][background_wakes.WAKE_HEADER] == recovered
            assert background_wakes.consume_wake(broker_url, stranded_task, recovered)
        print(json.dumps({'test': 'test_real_broker_persistent_wake_survives_aof_restart',
                          'pending_messages': 1, 'persistent_owner_and_message_preserved': True,
                          'pop_gap_owner_recovered_after_restart': True}))
    finally:
        cleanup(server, queue_name, ownership_prefix)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    restart = parser.add_mutually_exclusive_group()
    restart.add_argument('--prepare-restart-proof')
    restart.add_argument('--verify-restart-proof')
    arguments = parser.parse_args()
    if arguments.prepare_restart_proof:
        prepare_restart_proof(arguments.prepare_restart_proof)
    elif arguments.verify_restart_proof:
        verify_restart_proof(arguments.verify_restart_proof)
    else:
        proof_background_wakes()
