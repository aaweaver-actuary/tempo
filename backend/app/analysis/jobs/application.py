"""One bounded optional analysis, with no background loop or Study dependency."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .ports import AnalysisFence, AnalysisPublications, Cancellation, PreparedAnalysis


@dataclass(frozen=True)
class AnalysisRun:
    status: str
    prepared: PreparedAnalysis | None = None
    reason: str | None = None


class AnalysisRunner:
    """Caller owns scheduling and borrowed adapters; stop affects only this runner.

    Storage calls must release their short connection before returning. The
    preparation callback owns and cleans up its finite I/O resources. This
    runner creates no process, queue, retry daemon, shared pool or Study writer.
    """
    def __init__(self, publications: AnalysisPublications, *, timeout_seconds: float):
        if not 0 < timeout_seconds <= 60:
            raise ValueError("analysis_requires_finite_timeout")
        self.publications = publications
        self.timeout_seconds = timeout_seconds
        self._stop = asyncio.Event()
        self._running = False

    def request_stop(self) -> None:
        self._stop.set()

    async def run_once(self, fence: AnalysisFence,
                       prepare: Callable[[Cancellation], Awaitable[PreparedAnalysis]]) -> AnalysisRun:
        if self._stop.is_set():
            return AnalysisRun("stopped")
        if self._running:
            return AnalysisRun("busy")
        self._running = True
        work = stopped = None
        try:
            if self.publications.current(fence.repertoire_id) != fence:
                return AnalysisRun("obsolete", reason="analysis_context_changed")
            async with asyncio.timeout(self.timeout_seconds):
                work = asyncio.create_task(prepare(self._stop))
                stopped = asyncio.create_task(self._stop.wait())
                await asyncio.wait((work, stopped), return_when=asyncio.FIRST_COMPLETED)
                if self._stop.is_set():
                    return AnalysisRun("stopped")
                prepared = await work
            if prepared.fence != fence:
                return AnalysisRun("unavailable", reason="analysis_result_context_mismatch")
            if prepared.status != "ready":
                return AnalysisRun(prepared.status, prepared, prepared.reason)
            ranking = prepared.ranking
            if (ranking.source_revision != fence.source_revision
                    or ranking.snapshot_digest != fence.snapshot_digest
                    or ranking.introductions.based_on.repertoire_revisions != (
                        (fence.repertoire_id, fence.repertoire_revision),)):
                return AnalysisRun("unavailable", reason="analysis_result_source_mismatch")
            if not self.publications.publish_if_current(prepared):
                return AnalysisRun("obsolete", reason="analysis_context_changed")
            return AnalysisRun("published", prepared)
        except TimeoutError:
            return AnalysisRun("unavailable", reason="analysis_timeout")
        except asyncio.CancelledError:
            # Cleanup still runs. Caller cancellation is never a successful job.
            raise
        except Exception:
            # Do not expose credentials, response payloads or private paths.
            return AnalysisRun("unavailable", reason="analysis_failed")
        finally:
            children = [child for child in (work, stopped) if child is not None]
            for child in children:
                if not child.done():
                    child.cancel()
            if children:
                await asyncio.gather(*children, return_exceptions=True)
            self._running = False
