"""Concrete, bounded provider wiring for the optional Games worker only.

No process, credential, connection, timer or Study work starts on import.
Games' coordinator owns durable revisions/checkpoints and retries.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

import httpx
from pydantic import Field, model_validator

from app.contracts.product_v02 import Revision, Value
from app.contracts.product_v02.games import GamePage, GameWindow
from app.games.providers.chesscom_acquisition import fetch_chesscom_page
from app.games.providers.lichess_acquisition import fetch_lichess_page


class AcquisitionPolicy(Value):
    """Explicit user filters plus finite integration resource bounds.

    No population, account, window or game filter is inferred here.
    source_revision is an acquisition label; the Games store reconciles evidence.
    """
    provider: Literal["lichess", "chesscom"]
    speeds: tuple[Literal["bullet", "blitz", "rapid", "classical"], ...] = Field(min_length=1)
    rated: bool | None
    source_revision: Revision
    page_size: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: float = Field(default=10, gt=0, le=15, allow_inf_nan=False)
    total_timeout_seconds: float = Field(default=30, gt=0, le=30, allow_inf_nan=False)
    max_response_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=32 * 1024 * 1024)

    @model_validator(mode="after")
    def supported_provider_bounds(self):
        if self.provider == "chesscom" and self.rated is False:
            raise ValueError("Chess.com casual-only filter is unavailable; choose rated-only or both explicitly")
        if self.provider == "lichess" and self.max_response_bytes > 8 * 1024 * 1024:
            raise ValueError("Lichess response bound cannot exceed 8 MiB")
        return self


class AcquisitionDeadline(TimeoutError):
    """A total I/O budget expired; the input checkpoint remains authoritative."""
    kind = "timeout"
    retry_after = None

    def __init__(self, continuation: str | None):
        super().__init__("provider page exceeded its total I/O budget")
        self.continuation = continuation


class ProviderAcquisition:
    """Implements synchronous GameAcquisitionPort at the worker boundary.

    Async callers use fetch_page_async; never call fetch_page on the API event
    loop. Each invocation owns and closes one client, with one HTTP connection.
    Cancellation and provider errors escape to the coordinator without retry or
    checkpoint changes. The returned provider subtype is preserved verbatim.
    """
    def __init__(self, policy: AcquisitionPolicy, *,
                 cancelled: Callable[[], bool] | None = None,
                 transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None):
        self.policy = policy
        self.cancelled = cancelled
        self.transport_factory = transport_factory

    def fetch_page(self, *, account: str, window: GameWindow,
                   continuation: str | None = None) -> GamePage:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("use fetch_page_async on an event loop; synchronous acquisition belongs to the optional worker")
        return asyncio.run(self.fetch_page_async(account=account, window=window,
                                                continuation=continuation))

    async def fetch_page_async(self, *, account: str, window: GameWindow,
                              continuation: str | None = None) -> GamePage:
        policy = self.policy
        transport = self.transport_factory() if self.transport_factory is not None else None
        try:
            async with asyncio.timeout(policy.total_timeout_seconds):
                async with httpx.AsyncClient(transport=transport, trust_env=False,
                        follow_redirects=False, timeout=policy.timeout_seconds,
                        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                    common = dict(account=account, window=window, client=client,
                        source_revision=policy.source_revision, continuation=continuation,
                        speeds=policy.speeds, timeout_seconds=policy.timeout_seconds,
                        max_response_bytes=policy.max_response_bytes, cancelled=self.cancelled)
                    if policy.provider == "lichess":
                        return await fetch_lichess_page(**common, rated=policy.rated,
                                                        page_size=policy.page_size)
                    return await fetch_chesscom_page(**common, rated_only=policy.rated is True)
        except TimeoutError:
            raise AcquisitionDeadline(continuation) from None
