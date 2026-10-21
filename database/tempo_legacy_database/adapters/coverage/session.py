"""Existing Explorer session storage; application retains token and fallback policy."""

from .. import postgres_store


_EXPLORER_SESSION_KEY = "tempo:coverage:explorer-session-token"


def configured() -> bool:
    return postgres_store.configured()


def store_explorer_token(server, token: str):
    return server.setex(_EXPLORER_SESSION_KEY, 24 * 60 * 60, token)


def clear_explorer_token(server):
    return server.delete(_EXPLORER_SESSION_KEY)


def read_explorer_token(server):
    return server.get(_EXPLORER_SESSION_KEY)
