"""Shared credentials live only in a separate, nonpersistent Redis session store."""
from functools import lru_cache
import hashlib
import os
from urllib.parse import urlsplit

import redis

TOKEN_KEY = 'tempo:coverage:explorer-session-token'
REJECTED_KEY = 'tempo:coverage:explorer-rejected-fingerprint'
SESSION_SECONDS = 24 * 60 * 60
_REGISTER = """
if redis.call('GET', KEYS[2]) == ARGV[2] then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
redis.call('DEL', KEYS[2])
return 1
"""
_REJECT = """
local current = redis.call('GET', KEYS[1])
if current and current ~= ARGV[1] then return 0 end
local rejected = redis.call('GET', KEYS[2])
if not current and rejected and rejected ~= ARGV[2] then return 0 end
redis.call('DEL', KEYS[1])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
return 1
"""


class ExplorerCredentialRejected(ValueError):
    pass


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@lru_cache(maxsize=4)
def _client(session_url: str):
    return redis.Redis.from_url(session_url, decode_responses=True,
                               socket_connect_timeout=1, socket_timeout=1)


def client():
    session_url = os.getenv('TEMPO_EXPLORER_SESSION_REDIS_URL')
    if not session_url:
        raise redis.RedisError('Configure the dedicated ephemeral Explorer session store')
    broker_url = os.getenv('TEMPO_REDIS_URL')
    try:
        session_address = urlsplit(session_url)
        if session_address.scheme not in {'redis', 'rediss'} or not session_address.hostname:
            raise ValueError('Invalid session address')
        session_identity = (session_address.hostname, session_address.port or 6379)
        broker_address = urlsplit(broker_url) if broker_url else None
        broker_identity = (broker_address.hostname, broker_address.port or 6379) if broker_address else None
        server = _client(session_url)
    except ValueError as error:
        raise redis.RedisError('Configure a valid dedicated Explorer session store address') from error
    if session_identity == broker_identity:
        raise redis.RedisError('Explorer session store must be separate from the durable work broker')
    _require_ephemeral(server)
    return server


def _require_ephemeral(server) -> None:
    configuration = server.config_get('save', 'appendonly')
    if configuration.get('save') != '' or configuration.get('appendonly') != 'no':
        raise redis.RedisError('Disable snapshot and append-only persistence on the dedicated Explorer session store')


def register(token: str | None) -> None:
    server = client()
    if token is None:
        server.delete(TOKEN_KEY)
        return
    _require_ephemeral(server)
    accepted = server.eval(_REGISTER, 2, TOKEN_KEY, REJECTED_KEY, token, fingerprint(token), SESSION_SECONDS)
    if not accepted:
        raise ExplorerCredentialRejected('Explorer rejected this credential. Reconnect with a new Lichess credential.')


def reject(token: str) -> bool:
    server = client()
    _require_ephemeral(server)
    return bool(server.eval(_REJECT, 2, TOKEN_KEY, REJECTED_KEY, token, fingerprint(token), SESSION_SECONDS))


def get_token() -> str | None:
    server = client()
    registered, rejected = server.mget(TOKEN_KEY, REJECTED_KEY)
    if registered:
        return registered
    environment_token = os.getenv('TEMPO_LICHESS_EXPLORER_TOKEN')
    if environment_token and fingerprint(environment_token) != rejected:
        register(environment_token)
        return environment_token
    return None


def status(browser_token: str | None = None) -> dict[str, str]:
    registered, rejected = client().mget(TOKEN_KEY, REJECTED_KEY)
    if not registered and rejected and browser_token and fingerprint(browser_token) != rejected:
        return {'status': 'registration_missing'}
    return {'status': 'available' if registered else 'credential_rejected' if rejected else 'registration_missing'}
