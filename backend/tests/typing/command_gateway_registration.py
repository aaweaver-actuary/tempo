"""Run mypy with --warn-unused-ignores to verify the negative example."""
from app.command_gateway import register_descriptor
from app.contracts.command_registry import CommandDescriptor, CommandRegistry
from tempo_legacy_database.adapters.postgres_store import PostgresConnection

class AlienTransaction:
    pass

def decode(payload: dict[str, object]) -> int:
    return 1

def prepare(value: int) -> str:
    return str(value)

def supported(tx: PostgresConnection, value: str) -> bool:
    return True

def alien(tx: AlienTransaction, value: str) -> bool:
    return True

valid: CommandDescriptor[PostgresConnection, int, str, bool] = CommandDescriptor('valid', decode, prepare, supported)
invalid: CommandDescriptor[AlienTransaction, int, str, bool] = CommandDescriptor('invalid', decode, prepare, alien)
register_descriptor(valid)
register_descriptor(invalid)  # type: ignore[arg-type]  # an unrelated transaction must remain rejected
CommandRegistry().register(invalid)  # independent registries retain generic transactions

def bool_hook(result: bool) -> None:
    pass

def wrong_hook(result: str) -> None:
    pass

with_hook: CommandDescriptor[PostgresConnection, int, str, bool] = CommandDescriptor('hook', decode, prepare, supported, after_commit=bool_hook)
bad_hook: CommandDescriptor[PostgresConnection, int, str, bool] = CommandDescriptor('bad-hook', decode, prepare, supported, after_commit=wrong_hook)  # type: ignore[arg-type]  # hook must accept the result type
register_descriptor(with_hook)
from contextlib import nullcontext
from typing import ContextManager

class BoolLifecycle:
    def writer_admission(self) -> ContextManager[object]: return nullcontext()
    def delayed_preparation_error(self, error: Exception) -> Exception: return error
    def reject_after_rollback(self, database: PostgresConnection, operation_id: str, error: Exception) -> bool: return False
    def is_pending(self, result: bool) -> bool: return result

class WrongResultLifecycle(BoolLifecycle):
    def is_pending(self, result: str) -> bool: return bool(result)  # type: ignore[override]

good_lifecycle: CommandDescriptor[PostgresConnection, int, str, bool] = CommandDescriptor(
    'good-lifecycle', decode, prepare, supported, transition_lifecycle=BoolLifecycle())
bad_lifecycle: CommandDescriptor[PostgresConnection, int, str, bool] = CommandDescriptor(
    'bad-lifecycle', decode, prepare, supported, transition_lifecycle=WrongResultLifecycle())  # type: ignore[arg-type]
