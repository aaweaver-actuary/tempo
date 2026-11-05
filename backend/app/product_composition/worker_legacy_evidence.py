"""Select legacy evidence commands only for their retained worker deliveries."""

from threading import RLock

from app.command_gateway import register_command

_NAMES = frozenset({"game_evidence.receive_legacy", "game_evidence.admit_legacy"})
_lock = RLock()
_loaded: set[str] = set()


def resolve_worker_legacy_evidence(name: str) -> None:
    if name not in _NAMES:
        return
    with _lock:
        if name in _loaded:
            return
        from app.game_evidence_commands import legacy_evidence_registration

        selected = legacy_evidence_registration(name)
        register_command(name, selected.handler, prepare=selected.prepare)
        _loaded.add(name)
