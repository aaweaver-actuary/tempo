"""Bounded local next-card work; no provider, storage or startup implementation."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NextCardPreparation:
    progressed: bool = False
    pending: bool = False
    reason: str | None = None


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
