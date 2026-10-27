"""Legacy worker process composition; no API-module import or startup at import."""
from __future__ import annotations

import asyncio
import os
import signal
import httpx

async def wait_for_api() -> None:
    api_url = os.getenv("TEMPO_API_URL", "http://api:8000")
    async with httpx.AsyncClient(timeout=2) as client:
        while True:
            try:
                response = await client.get(f"{api_url}/api/health", headers={"X-Tempo-Work-Class": "background"})
                if response.is_success:
                    return
            except httpx.RequestError:
                pass
            await asyncio.sleep(1)


async def run_legacy_worker(*, stopping: asyncio.Event | None = None) -> None:
    from tempo_legacy_database.adapters.database_executor import database_writer
    from .legacy_worker import coordinator, register_legacy_handlers
    register_legacy_handlers()
    await wait_for_api()
    install_signals = stopping is None
    stopping = stopping if stopping is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = []
    try:
        database_writer.start()
        await coordinator.start()
        if install_signals:
            for stop_signal in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(stop_signal, stopping.set)
                signals.append(stop_signal)
        await stopping.wait()
    finally:
        try:
            await coordinator.stop()
        finally:
            database_writer.stop()
            for stop_signal in signals:
                loop.remove_signal_handler(stop_signal)
