"""Bounded local next-card work; no provider, storage or startup implementation."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NextCardPreparation:
    progressed: bool = False
    pending: bool = False
    reason: str | None = None
    # Append fields: the historical three positional arguments retain meaning.
    # None marks legacy ambiguity, never authority to bypass the server's gate.
    blocks_issuance: bool | None = None

    @property
    def issuance_blocked(self) -> bool:
        return self.pending if self.blocks_issuance is None else self.blocks_issuance


def decode_next_card_preparation(payload: object) -> NextCardPreparation:
    """Read old receipts without rewriting their original immutable bytes."""
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_next_card_preparation")
    if any(type(payload.get(name)) is not bool for name in ("progressed", "pending")):
        raise ValueError("invalid_next_card_preparation")
    if "reason" not in payload or (
        payload["reason"] is not None and not isinstance(payload["reason"], str)
    ):
        raise ValueError("invalid_next_card_preparation")
    if "blocks_issuance" in payload and type(payload["blocks_issuance"]) is not bool:
        raise ValueError("invalid_next_card_preparation")
    return NextCardPreparation(
        payload["progressed"],
        payload["pending"],
        payload["reason"],
        payload.get("blocks_issuance"),
    )


def next_card_preparation_payload(value: NextCardPreparation) -> dict:
    """New server responses always state the owner decision explicitly."""
    return dict(
        progressed=value.progressed,
        pending=value.pending,
        reason=value.reason,
        blocks_issuance=value.issuance_blocked,
    )


class PreparedNextCardWork(Protocol):
    """Ephemeral owner preparation; apply only after the command receipt fence."""

    priority: dict[str, str] | None
    is_control: bool

    def apply(self, writer: object) -> NextCardPreparation: ...


@dataclass(frozen=True)
class NoNextCardWork:
    result: NextCardPreparation = NextCardPreparation()
    priority: None = None
    is_control: bool = False

    def apply(self, _writer: object) -> NextCardPreparation:
        return self.result
