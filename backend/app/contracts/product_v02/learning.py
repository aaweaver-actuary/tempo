"""Immutable queue-owner first-issuance receipt."""

from pydantic import Field

from . import Identifier, Revision, UtcTimestamp, Value


class IssuedAttemptEvidence(Value):
    """Queue-owner first issuance, never a prefetch/reload or invented old stamp."""

    queue_entry_id: int = Field(ge=1, le=2**53 - 1)
    card_id: Identifier
    card_revision: Revision
    issuance_sequence: int = Field(ge=1, le=2**53 - 1)
    issued_at: UtcTimestamp
