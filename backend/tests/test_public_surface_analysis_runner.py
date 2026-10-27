"""Actual existing lifecycle through its declared surface; no provider/process."""
import asyncio
import importlib

from test_public_surface_infrastructure import cold_import
from test_product_analysis_publications import prepared


def test_analysis_runner_public_import_has_no_operational_startup():
    cold_import("app.analysis.jobs.interfaces")


def test_analysis_runner_surface_preserves_canonical_objects():
    public = importlib.import_module("app.analysis.jobs.interfaces")
    lifecycle = importlib.import_module("app.analysis.jobs.application")
    ports = importlib.import_module("app.analysis.jobs.ports")
    assert set(public.__all__) == {"AnalysisRun", "AnalysisRunner", "AnalysisFence",
        "AnalysisPublications", "Cancellation", "PreparedAnalysis"}
    for name in public.__all__:
        assert getattr(public, name) is getattr(lifecycle if hasattr(lifecycle, name) else ports, name)


def test_public_runner_stop_cleans_only_its_work_and_preserves_borrowed_state():
    from app.analysis.jobs.interfaces import AnalysisRunner
    fence = prepared().fence
    class Publications:
        unrelated_attempt = object()
        publications = 0
        def current(self, repertoire_id):
            assert repertoire_id == fence.repertoire_id
            return fence
        def publish_if_current(self, result):
            self.publications += 1
            raise AssertionError("stopped work cannot publish")
        def close(self):
            raise AssertionError("runner cannot close borrowed infrastructure")
    async def scenario():
        owner = Publications(); protected = owner.unrelated_attempt
        runner = AnalysisRunner(owner, timeout_seconds=2)
        started = asyncio.Event(); cleaned = asyncio.Event()
        async def prepare(cancelled):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()
        run = asyncio.create_task(runner.run_once(fence, prepare))
        await asyncio.wait_for(started.wait(), 1)
        runner.request_stop()
        result = await asyncio.wait_for(run, 1)
        assert result.status == "stopped" and cleaned.is_set()
        assert owner.publications == 0 and owner.unrelated_attempt is protected
    asyncio.run(scenario())
