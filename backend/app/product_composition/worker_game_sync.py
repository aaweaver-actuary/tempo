"""Select the existing Games sync command on worker dispatch."""

from threading import RLock

from app.command_gateway import is_command_registered

_SYNC_COMMAND = "games.sync.enqueue"
_sync_registration_lock = RLock()


def resolve_worker_game_sync_command(name: str) -> None:
    """Load the existing registration only for its named command."""
    if name != _SYNC_COMMAND or is_command_registered(name):
        return
    with _sync_registration_lock:
        if not is_command_registered(name):
            from app import game_sync_commands  # noqa: F401 - existing registration

            if not is_command_registered(name):
                raise RuntimeError("Games sync command did not register")
