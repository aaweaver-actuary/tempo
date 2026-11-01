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
