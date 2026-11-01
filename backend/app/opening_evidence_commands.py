"""Register the existing opening evidence checkpoint command for composition."""

from .command_gateway import register_command
from .services.postgres_opening_evidence import (
    commit_standalone_checkpoint,
    prepare_standalone_checkpoint,
)

register_command('opening_evidence.checkpoint', commit_standalone_checkpoint, prepare=prepare_standalone_checkpoint)
