"""Explicit process entry point; importing it constructs no runtime."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from asyncio import Event

async def run_legacy_worker(*, stopping: Event | None = None) -> None:
    from ..worker import run_legacy_worker as run
    await run(stopping=stopping)

__all__ = ["run_legacy_worker"]
