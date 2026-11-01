"""Select the retained product feedback command on its worker delivery."""
from threading import RLock

_COMMAND = 'game_evidence.product_accept'
_lock = RLock()
_loaded = False


def resolve_worker_product_feedback(name: str) -> None:
    if name != _COMMAND:
        return
    global _loaded
    with _lock:
        if _loaded:
            return
        from .feedback_commands import register_commands
        register_commands()
        _loaded = True
