"""Run durable analysis outside the API process while sharing its SQLite store."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import httpx

from .main import coordinator  # Registers every durable task handler.
from .services.database_executor import database_writer


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


async def main() -> None:
    await wait_for_api()
    database_writer.start()
    await coordinator.start()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop_signal, stopping.set)
    try:
        await stopping.wait()
    finally:
        await coordinator.stop()
        database_writer.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
