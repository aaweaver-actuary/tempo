"""Existing Redis wake ownership scripts, key construction and fixed storage operations."""
from functools import lru_cache

import redis


_KEY_PREFIX = 'tempo:wake:'

# Five seconds is a producer-crash lease, not a publication deadline. The latest
# token remains in one bounded metadata record after expiry/consumption, fencing
# publishers even when the owner key is absent. No per-delivery keys accumulate.
_RESERVE = """-- reserve
local existing = redis.call('GET', KEYS[1])
-- Join requirements without rewriting a queued/unacked envelope. Transfer
-- outstanding intent on crash/abort recovery; consumption alone fulfills it.
local requirement = ARGV[2] or '0'
local previous_phase = redis.call('HGET', KEYS[2], 'phase')
if (previous_phase == 'reserved' or previous_phase == 'published' or previous_phase == 'aborted') and
   redis.call('HGET', KEYS[2], 'queue_refresh_wake') == '1' then
    requirement = '1'
end
if existing then
    if redis.call('HGET', KEYS[2], 'token') == existing and requirement == '1' then
        redis.call('HSET', KEYS[2], 'queue_refresh_wake', '1')
    end
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
redis.call('HSET', KEYS[2], 'token', ARGV[1], 'phase', 'reserved',
           'queue_refresh_wake', requirement)
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
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return {0, 0} end
local requirement = 0
if redis.call('HGET', KEYS[2], 'token') == ARGV[1] and
   redis.call('HGET', KEYS[2], 'queue_refresh_wake') == '1' then
    requirement = 1
end
redis.call('DEL', KEYS[1])
if redis.call('HGET', KEYS[2], 'token') == ARGV[1] then
    redis.call('DEL', KEYS[2])
    redis.call('HSET', KEYS[2], 'token', ARGV[1], 'phase', 'consumed')
end
return {1, requirement}
"""


@lru_cache(maxsize=4)
def client(broker_url):
    return redis.Redis.from_url(broker_url, decode_responses=True,
                               socket_connect_timeout=1, socket_timeout=1)


def ownership_keys(task_name: str, key_prefix: str):
    ownership_key = key_prefix + task_name
    return ownership_key, ownership_key + ':publication'


def consume_wake(server, keys: tuple[str, str], ownership_token: str):
    return server.eval(_CONSUME, 2, *keys, ownership_token)


def publish_wake(server, keys: tuple[str, str], queue_key: str, unacked_key: str,
                 ownership_token: str, encoded_message: str, delivery_tag: str,
                 leftmost: bool):
    return server.eval(_PUBLISH, 4, *keys,
                      queue_key, unacked_key, ownership_token,
                      encoded_message, delivery_tag,
                      'LPUSH' if leftmost else 'RPUSH')


def reserve_wake(server, keys: tuple[str, str], ownership_token: str, requirement: str):
    return server.eval(_RESERVE, 2, *keys, ownership_token, requirement)


def abort_wake(server, keys: tuple[str, str], ownership_token: str):
    return server.eval(_ABORT, 2, *keys, ownership_token)
