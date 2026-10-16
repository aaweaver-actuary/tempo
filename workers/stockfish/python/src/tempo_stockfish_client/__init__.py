"""Passive typed client. Durable execution authority belongs to the consumer."""
from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import json
import os
from typing import AsyncIterator, Mapping, Sequence

from tempo_analysis_jobs_contracts import v1

__version__ = "1.0.0"
__all__ = ["StockfishClient", "WorkerSessionLost", "stockfish_input_digest"]
WorkerOutput = v1.JobStatus | v1.StockfishResult | v1.CancellationAcknowledgement
_OUTPUT_TYPES = {model.__name__: model for model in (
    v1.JobStatus, v1.StockfishResult, v1.CancellationAcknowledgement
)}


def stockfish_input_digest(job: v1.StockfishJob | dict) -> str:
    payload = job.model_dump(mode="json") if isinstance(job, v1.StockfishJob) else v1.StockfishJob.model_validate({**job, "input_digest": "digest-preimage"}).model_dump(mode="json")
    payload.pop("input_digest", None)
    payload.pop("execution_generation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return "sha256:" + hashlib.sha256(("tempo.stockfish.input.v1\n" + canonical).encode("utf-8")).hexdigest()


class WorkerSessionLost(RuntimeError):
    """Receipt unknown: advance execution generation before executing again."""


class StockfishClient:
    def __init__(self, command: Sequence[str], *, startup_timeout: float = 35, env: Mapping[str, str] | None = None):
        if not command or isinstance(command, str):
            raise ValueError("command must be a nonempty argument sequence")
        self.command = list(command)
        # Read values only for allowed names; never copy domain credential values.
        self.env = ({name: os.environ[name] for name in os.environ if name in {"PATH", "HOME", "TMPDIR", "LANG"} or name.startswith("LC_")} if env is None else dict(env))
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()):
            raise TypeError("env requires string names and values")
        self.startup_timeout = startup_timeout
        self.process = None
        self.identity = None
        self.diagnostics = deque(maxlen=128)
        self._records = asyncio.Queue(maxsize=16)
        self._tasks = []
        self._ready = None
        self._closing = False
        self._write_lock = asyncio.Lock()

    async def __aenter__(self):
        if self.process is not None:
            raise RuntimeError("client sessions cannot be restarted")
        self._ready = asyncio.get_running_loop().create_future()
        self.process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=1024 * 1024 + 1, env=self.env,
        )
        self._tasks = [asyncio.create_task(self._read_records()), asyncio.create_task(self._read_diagnostics())]
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), self.startup_timeout)
        except BaseException as error:
            await self.close()
            if self._ready.done() and not self._ready.cancelled():
                self._ready.exception()
            if isinstance(error, asyncio.TimeoutError):
                raise WorkerSessionLost("worker startup deadline expired") from error
            raise
        return self

    async def _read_records(self):
        try:
            while line := await self.process.stdout.readline():
                if len(line) > 1024 * 1024:
                    raise WorkerSessionLost("worker output exceeded record limit")
                record = json.loads(line)
                if set(record) != {"model", "payload"} or record["model"] not in _OUTPUT_TYPES:
                    raise WorkerSessionLost("invalid worker record")
                await self._records.put(_OUTPUT_TYPES[record["model"]].model_validate(record["payload"]))
            if not self._closing:
                raise WorkerSessionLost("worker transport ended; advance execution generation")
        except Exception as error:
            failure = error if isinstance(error, WorkerSessionLost) else WorkerSessionLost("invalid or lost worker stream")
            if not self._ready.done():
                self._ready.set_exception(failure)
            await self._records.put(failure)

    async def _read_diagnostics(self):
        try:
            while line := await self.process.stderr.readline():
                if len(line) > 64 * 1024:
                    raise WorkerSessionLost("oversized worker diagnostic")
                event = json.loads(line)
                self.diagnostics.append(event)
                if event.get("event") == "ready" and not self._ready.done():
                    self.identity = event["identity"]
                    self._ready.set_result(event)
                elif event.get("event") in {"startup_failed", "fatal"} and not self._ready.done():
                    self._ready.set_exception(WorkerSessionLost(event.get("reason", "startup_failed")))
        except Exception:
            failure = WorkerSessionLost("worker readiness/diagnostic stream failed")
            if not self._ready.done():
                self._ready.set_exception(failure)
            else:
                await self._records.put(failure)

    async def _send(self, model, payload):
        if self.process is None or self.process.returncode is not None or self._closing:
            raise WorkerSessionLost("worker session unavailable")
        encoded = (json.dumps({"model": model, "payload": payload.model_dump(mode="json")}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ValueError("request exceeds line limit")
        async with self._write_lock:
            try:
                self.process.stdin.write(encoded)
                await asyncio.wait_for(self.process.stdin.drain(), 5)
            except (OSError, asyncio.TimeoutError) as error:
                raise WorkerSessionLost("delivery failed; receipt is unknown") from error

    async def send_job(self, job: v1.StockfishJob) -> None:
        if not isinstance(job, v1.StockfishJob):
            raise TypeError("send_job requires StockfishJob v1")
        await self._send("StockfishJob", job)

    async def send_cancellation(self, request: v1.CancellationRequest) -> None:
        if type(request) is not v1.CancellationRequest:
            raise TypeError("send_cancellation requires CancellationRequest v1")
        await self._send("CancellationRequest", request)

    async def records(self) -> AsyncIterator[WorkerOutput]:
        while True:
            value = await self._records.get()
            if isinstance(value, Exception):
                raise value
            yield value

    async def close(self):
        self._closing = True
        if self.process is not None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 7)
            except asyncio.TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def __aexit__(self, *_):
        await self.close()
