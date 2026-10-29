"""Task-only worker process: actual runner/provider loop, local HTTP, no live DB."""
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx


class LocalTransport(httpx.AsyncBaseTransport):
    def __init__(self, port):
        self.port = port
        self.http = httpx.AsyncHTTPTransport(retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))

    async def handle_async_request(self, request):
        local = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
        return await self.http.handle_async_request(httpx.Request(request.method, local, headers=request.headers))

    async def aclose(self):
        await self.http.aclose()


def main():
    ROOT = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(ROOT / "backend/tests"))
    from test_product_feedback_jobs import FakeClock, MemoryStore
    from app.contracts.product_v02.games import GameWindow
    from app.product_composition.game_sync import AcquisitionPolicy, create_game_sync, sync_job
    from app.product_composition.game_worker import run_optional_worker
    port = int(sys.argv[1])
    policy = AcquisitionPolicy(provider="lichess", speeds=("rapid",), rated=True,
                               source_revision=1, timeout_seconds=15)
    other = AcquisitionPolicy(provider="chesscom", speeds=("rapid",), rated=True, source_revision=1)
    store, clock = MemoryStore(), FakeClock()
    window = GameWindow(start_at="2026-10-01T00:00:00Z", end_at="2026-11-01T00:00:00Z", precision="millisecond")
    store.enqueue(sync_job(job_id="task-worker", workspace_id="fixture", account="Learner", window=window,
                           repertoire_ids=("white",), policy=policy))
    runtime = create_game_sync(policies=(policy, other), store=store,
        snapshots=SimpleNamespace(), learning=SimpleNamespace(), clock=clock,
        transport_factory=lambda: LocalTransport(port))
    run_optional_worker(runtime, enqueue_due=lambda: None, idle_seconds=0.05)


if __name__ == "__main__":
    main()

