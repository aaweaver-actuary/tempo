"""T10's currently available provider/HTTP seams; no live app or database.

Coordinator/Study/browser/process acceptance is separately pending, not simulated
by these checks. Concrete acquisition adapters use injected synthetic HTTP.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app.contracts.product_v02.games import GameWindow, ProviderSyncStatus, RecentGames
from app.contracts.product_v02.learning import LearningReceipt
from app.contracts.product_v02.presentation import GameDetail
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.games.feedback.http import create_game_review_router
from app.games.feedback.ports import AcquisitionFailure
from app.games.providers.chesscom_acquisition import ChesscomAcquisitionError
from app.product_composition.game_sync import (AcquisitionDeadline, AcquisitionPolicy, ProviderAcquisition,
    WorkflowAcquisition, create_game_sync, sync_job)
from app.study.game_feedback import GameFeedback
from test_product_feedback_jobs import FakeClock, MemoryStore as GamesMemoryStore
from test_product_game_lapse import Decisions, MemoryStore as StudyMemoryStore, NOW

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


@pytest.mark.parametrize("header,expected", [("17", 17), ("Mon, 26 Oct 2026 12:00:30 GMT", 30)])
def test_workflow_receives_numeric_retry_after_from_concrete_provider(header, expected):
    acquisition = ProviderAcquisition(policy("lichess"), transport_factory=lambda: httpx.MockTransport(
        lambda request: httpx.Response(429, headers={"Retry-After": header})))
    adapter = WorkflowAcquisition(acquisition, now=lambda: NOW.timestamp())
    with pytest.raises(AcquisitionFailure) as failed:
        adapter.fetch_page(account="learner", window=WINDOW)
    assert failed.value.retryable and failed.value.retry_after == expected
    assert str(failed.value) == "lichess_rate_limited"


def test_both_provider_workflow_uses_existing_study_owner_and_overlap_has_one_effect():
    """Concrete source seams with reused semantic fake stores, not PostgreSQL.

    Exercises owner policy and real scheduling through #487, while preserving
    the distinction between source integration and ordinary-product acceptance.
    """
    miss = PGN.replace("1. e4 e5 2. Nf3 Nc6", "1. d4")
    gap = PGN.replace("1. e4 e5 2. Nf3 Nc6", "1. e4 c5")
    at = lambda value: int(datetime.fromisoformat(value).timestamp())
    rows = [{"id": f"miss{i:04d}", "createdAt": at("2026-10-25T12:00:00+00:00") * 1000 + i,
             "speed": "rapid", "rated": True, "pgn": miss} for i in range(101)]
    outage = [True]
    def handle(request):
        if request.url.host == "lichess.org":
            if outage[0]:
                return httpx.Response(429, headers={"Retry-After": "5"})
            params = request.url.params
            selected = [row for row in rows if int(params["since"]) <= row["createdAt"] < int(params["until"])]
            return httpx.Response(200, content="\n".join(json.dumps(row) for row in selected[:int(params["max"])]) + "\n",
                                  headers={"Content-Type": "application/x-ndjson"})
        base = "https://api.chess.com/pub/player/learner/games/"
        if request.url.path.endswith("archives"):
            return httpx.Response(200, json={"archives": [base + "2026/09", base + "2026/10"]})
        month = request.url.path[-2:]
        source_games = [("unintroduced", miss)] if month == "09" else [("miss", miss), ("gap", gap)]
        return httpx.Response(200, json={"games": [{"uuid": name, "url": "https://www.chess.com/game/live/123",
            "pgn": text, "end_time": at(f"2026-{month}-25T12:00:00+00:00"),
            "rules": "chess", "time_class": "rapid", "time_control": "600", "rated": True}
            for name, text in source_games]})

    payload = fixture("snapshot")
    payload["membership"]["routes"][0]["moves"] = ["e2e4", "e7e5"]
    payload["boundaries"][0]["terminal_ply"] = 2
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(payload))
    games, study, clock = GamesMemoryStore(), StudyMemoryStore(), FakeClock()
    decisions = Decisions()
    decisions.mappings = snapshot.decision_presentations
    owner = GameFeedback(study, decisions, clock=lambda: NOW)
    def runtime():
        return create_game_sync(policies=tuple(policy(provider) for provider in ("lichess", "chesscom")),
            store=games, snapshots=SimpleNamespace(snapshot=lambda repertoire: snapshot),
            learning=owner, clock=clock, transport_factory=lambda: httpx.MockTransport(handle))
    runner = runtime()
    for provider in ("lichess", "chesscom"):
        job = sync_job(job_id=provider, workspace_id="fixture", account="Learner", window=WINDOW,
                       repertoire_ids=("white",), policy=policy(provider))
        assert job.account == "learner"  # Bind the actual provider identity.
        games.enqueue(job)

    # A provider 429 cannot prevent the other provider's two-month job completing.
    for _ in range(20):
        if runner.run_once() == "idle":
            break
    assert games.load_job("lichess").status == "retryable"
    assert games.load_job("lichess").next_attempt_at == clock.now() + 5
    assert games.load_job("chesscom").status == "complete"
    assert games.load_job("chesscom").pages_committed == 2
    findings = [finding for source in games.sources.values() for finding in source.findings]
    assert {finding.kind for finding in findings} >= {"confirmed_miss", "opponent_gap", "known_unintroduced"}

    outage[0] = False
    clock.value += 5
    for _ in range(230):
        if runner.run_once() == "idle":
            break
    else:
        pytest.fail("bounded fixture workflow failed to drain")
    assert games.load_job("lichess").status == "complete"
    assert games.load_job("lichess").pages_committed == 2
    assert {source.original.source_game_id for source in games.sources.values()} == {
        *(row["id"] for row in rows), "miss", "gap", "unintroduced"}
    assert len(study.effects) == len(study.reviews) == 102
    assert all(delivery.receipt.status == "applied" for delivery in games.deliveries.values())

    # Repeat overlapping jobs and a new runner retain the exact logical effects.
    for provider in ("lichess", "chesscom"):
        games.enqueue(sync_job(job_id=provider + "-overlap", workspace_id="fixture", account="Learner",
            window=WINDOW, repertoire_ids=("white",), policy=policy(provider)))
    restarted = runtime()
    for _ in range(20):
        if restarted.run_once() == "idle":
            break
    assert len(study.effects) == len(study.reviews) == 102
    assert study.history == ["retained-original-review"]
    assert len(games.deliveries) == 102
    runner.close()
    restarted.close()
    assert runner.run_once() == restarted.run_once() == "stopped"
