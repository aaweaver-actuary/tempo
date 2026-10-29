"""Shared introduction outcome; no weighted storage/runtime import."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ports import Receipt


@dataclass(frozen=True)
class CommitResult:
    status: str
    receipt: Receipt | None = None
    reason: str | None = None


