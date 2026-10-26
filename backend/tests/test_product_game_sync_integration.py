"""T10's currently available provider/HTTP seams; no live app or database.

Coordinator/Study/browser/process acceptance is separately pending, not simulated
by these checks. Concrete acquisition adapters use injected synthetic HTTP.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app.contracts.product_v02.games import GameWindow, ProviderSyncStatus, RecentGames
from app.contracts.product_v02.learning import LearningReceipt
from app.contracts.product_v02.presentation import GameDetail
from app.games.feedback.http import create_game_review_router
from app.games.providers.chesscom_acquisition import ChesscomAcquisitionError
from app.product_composition.game_sync import AcquisitionDeadline, AcquisitionPolicy, ProviderAcquisition

ROOT = Path(__file__).resolve().parents[2]
WINDOW = GameWindow(start_at="2026-09-01T00:00:00Z", end_at="2026-11-01T00:00:00Z", precision="millisecond")
PGN = '[Event "Synthetic"]\n[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'


def fixture(name):
    return json.loads((ROOT / f"contracts/product-v02/fixtures/{name}.json").read_text())["payload"]


def policy(provider, **changes):
    return AcquisitionPolicy(provider=provider, speeds=("rapid",), rated=True,
                             source_revision=1, **changes)


def test_concrete_both_provider_adapters_preserve_exact_ids_and_checkpoints():
    timestamp = int(datetime.fromisoformat("2026-09-01T00:00:00+00:00").timestamp() * 1000)
    rows = [{"id": f"synthetic{i:04d}", "createdAt": timestamp + i,
             "speed": "rapid", "rated": True, "pgn": PGN} for i in range(205)]
    requests = []

    def handle(request):
        requests.append(str(request.url))
        if request.url.host == "lichess.org":
            params = request.url.params
            selected = [row for row in rows if int(params["since"]) <= row["createdAt"] < int(params["until"])]
            return httpx.Response(200, content="\n".join(json.dumps(row) for row in selected[:int(params["max"])]) + "\n",
                                  headers={"Content-Type": "application/x-ndjson"})
        base = "https://api.chess.com/pub/player/learner/games/"
        if request.url.path.endswith("archives"):
            return httpx.Response(200, json={"archives": [base + "2026/09", base + "2026/10"]})
        month = request.url.path[-2:]
        return httpx.Response(200, json={"games": [{"uuid": "month-" + month,
            "url": "https://www.chess.com/game/live/123", "pgn": PGN, "rules": "chess",
            "end_time": int(datetime.fromisoformat(f"2026-{month}-05T00:00:00+00:00").timestamp()),
            "time_class": "rapid", "time_control": "600", "rated": True}]})

    expected = {"lichess": {row["id"] for row in rows}, "chesscom": {"month-09", "month-10"}}
    for provider in expected:
        token, seen, pages = None, set(), 0
        while True:
            # Recreate the outer adapter for every page: cursors cannot rely on
            # hidden client/process memory. This is not a durable-store proof.
            acquisition = ProviderAcquisition(policy(provider), transport_factory=lambda: httpx.MockTransport(handle))
            page = acquisition.fetch_page(account="Learner", window=WINDOW, continuation=token)
            pages += 1
            seen.update(record.source_game_id for record in page.records)
            assert all(record.pgn == PGN for record in page.records)
            if provider == "chesscom":
                assert all(record.source_metadata for record in page.records)
            if page.complete:
                break
            assert page.continuation is not None
            token = page.continuation
            assert pages < 10
        assert seen == expected[provider]
        assert pages == (3 if provider == "lichess" else 2)
    assert all("Learner" not in url for url in requests)  # Provider canonical account.


def test_failure_preserves_provider_checkpoint_and_does_not_run_another_provider():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "17"})
    acquisition = ProviderAcquisition(policy("chesscom"), transport_factory=lambda: httpx.MockTransport(handle))
    with pytest.raises(ChesscomAcquisitionError) as failed:
        acquisition.fetch_page(account="Learner", window=WINDOW)
    assert failed.value.continuation is None and failed.value.retry_after == "17"
    assert len(requests) == 1 and requests[0].url.host == "api.chess.com"


def test_explicit_filters_and_async_call_boundary():
    with pytest.raises(ValidationError):
        AcquisitionPolicy(provider="chesscom", speeds=("rapid",), rated=False, source_revision=1)
    with pytest.raises(ValidationError):
        AcquisitionPolicy(provider="lichess", speeds=("rapid",), source_revision=1)
    with pytest.raises(ValidationError):
        policy("lichess", total_timeout_seconds=float("inf"))
    async def scenario():
        with pytest.raises(RuntimeError, match="fetch_page_async"):
            ProviderAcquisition(policy("lichess")).fetch_page(account="Learner", window=WINDOW)
    asyncio.run(scenario())


def test_total_budget_closes_transport_without_checkpoint_advance():
    class SlowTransport(httpx.AsyncBaseTransport):
        closed = False
        async def handle_async_request(self, request):
            await asyncio.Event().wait()
        async def aclose(self):
            self.closed = True
    transport = SlowTransport()
    acquisition = ProviderAcquisition(policy("chesscom", total_timeout_seconds=0.01),
                                      transport_factory=lambda: transport)
    with pytest.raises(AcquisitionDeadline) as failed:
        acquisition.fetch_page(account="Learner", window=WINDOW)
    assert failed.value.continuation is None and transport.closed


def test_http_reads_are_inert_and_commands_preserve_operation_and_owner_receipt():
    detail = GameDetail.model_validate_json(json.dumps(fixture("game-detail")))
    receipt = LearningReceipt.model_validate_json(json.dumps(fixture("held-receipt")))
    calls = []
    class Operations:
        def recent_games(self):
            return RecentGames(games=(), sync=())
        def game_detail(self, **request):
            calls.append(("read", request))
            return detail
        def sync(self, **request):
            calls.append(("sync", request))
            return ProviderSyncStatus(provider=request["provider"], status="pending",
                                      operation_id=request["operation_id"], error=None)
        def retry_feedback(self, **request):
            calls.append(("retry", request))
            return receipt
    app = FastAPI()
    app.include_router(create_game_review_router(Operations()))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/api/product/game-review/games")).status_code == 200
            response = await client.get("/api/product/game-review/games/lichess/game-1?repertoire_id=repertoire-1")
            assert response.json() == detail.model_dump(mode="json")
            assert [name for name, _ in calls] == ["read"]
            result = await client.post("/api/product/game-review/sync", json={"provider": "lichess", "operation_id": "operation-1"})
            assert result.json()["status"] == "pending"
            result = await client.post("/api/product/game-review/feedback/retry", json={"finding": detail.findings[0].model_dump(mode="json"), "operation_id": "retry-1"})
            assert result.status_code == 200, result.text
            assert result.json() == receipt.model_dump(mode="json")
            assert calls[-1][1]["operation_id"] == "retry-1"
            assert calls[-1][1]["finding"] == detail.findings[0]
            for invalid in ({"provider": "unknown", "operation_id": "x"}, {"provider": "lichess"},
                            {"provider": "lichess", "operation_id": "x", "account": "overwrite"}):
                assert (await client.post("/api/product/game-review/sync", json=invalid)).status_code == 422
            assert [name for name, _ in calls] == ["read", "sync", "retry"]
    asyncio.run(scenario())


def test_unavailable_optional_projection_is_local_http_failure():
    class Unavailable:
        def recent_games(self):
            raise HTTPException(503, "Games unavailable")
    app = FastAPI()
    app.include_router(create_game_review_router(Unavailable()))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/product/game-review/games")
            assert response.status_code == 503
    asyncio.run(scenario())
