"""Select retained Games commands after their original worker attempt."""
from threading import RLock

_GAME = frozenset({'games.exclusion.set', 'games.defensive_threats.refresh'})
_REVIEW = frozenset({'product_games.configure', 'product_games.sync', 'product_games.refresh', 'product_games.retry_feedback'})
_ANALYSIS = frozenset({'games.analysis.claim', 'games.analysis.position.claim', 'games.analysis.position.report',
    'games.analysis.position.release', 'games.analysis.failure', 'games.analysis.heartbeat',
    'games.analysis.release', 'games.analysis.retry', 'games.analysis.repair_timeout', 'games.analysis.repair_provenance'})
_PUBLICATION = frozenset({'games.analysis.finalize.admit', 'games.analysis.manual.admit'})
_game_lock, _review_lock, _analysis_lock, _publication_lock = RLock(), RLock(), RLock(), RLock()
_loaded: set[str] = set()


def resolve_worker_game_command(name: str) -> None:
    if name in _GAME:
        lock = _game_lock
    elif name in _REVIEW:
        lock = _review_lock
    elif name in _ANALYSIS:
        lock = _analysis_lock
    elif name in _PUBLICATION:
        lock = _publication_lock
    else:
        return
    with lock:
        if name in _loaded:
            return
        if name in _GAME:
            from app.game_commands import register_commands
        elif name in _REVIEW:
            from .game_review_commands import register_commands
        elif name in _ANALYSIS:
            from app.game_analysis_commands import register_commands
        else:
            from app.game_analysis_publication import register_commands
        register_commands(name)
        _loaded.add(name)
