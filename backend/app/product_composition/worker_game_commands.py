"""Select retained Games Review commands on worker delivery."""
from threading import RLock

_REVIEW = frozenset({'product_games.configure', 'product_games.sync', 'product_games.refresh', 'product_games.retry_feedback'})
_lock = RLock()
_loaded: set[str] = set()


def resolve_worker_game_command(name: str) -> None:
    if name == "games.exclusion.set":
        from app.game_commands import register_commands
        with _lock:
            if name not in _loaded:
                register_commands(name)
                _loaded.add(name)
        return
    if name not in _REVIEW:
        return
    with _lock:
        if name not in _loaded:
            from .game_review_commands import register_commands
            register_commands(name)
            _loaded.add(name)
