"""Disposable HTTP fixtures for the restartable acquisition boundary; no live account."""
import asyncio
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.contracts.product_v02.games import GamePage, GameWindow
from app.games.providers.chesscom_acquisition import ChesscomAcquisitionError, fetch_chesscom_page


ROOT = "https://api.chess.com/pub/player/learner/games/"
WINDOW = GameWindow(start_at="2026-09-01T00:00:00Z", end_at="2026-11-01T00:00:00Z", precision="second")


def game(identifier, *, at="2026-09-20T12:00:00+00:00", **changes):
    raw = {"uuid": identifier, "url": "https://www.chess.com/game/live/123",
           "pgn": '[Event "Live Chess"]\n[Site "Chess.com"]\n[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n[TimeControl "600"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n',
           "end_time": int(datetime.fromisoformat(at).timestamp()), "rules": "chess",
           "time_class": "rapid", "time_control": "600", "rated": True}
    return dict(raw, **changes)


def run(coro):
    return asyncio.run(coro)


def fixture(months, responses):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/archives"):
            return httpx.Response(200, json={"archives": [ROOT + month for month in months]})
        value = responses[request.url.path[-7:]]
        if isinstance(value, list):
            return httpx.Response(200, json={"games": value})
        return value(request) if callable(value) else value

    return httpx.AsyncClient(transport=httpx.MockTransport(handle)), requests


async def consume(client, *, window=WINDOW, **kwargs):
    pages = []
    cursor = None
    for _ in range(100):
        page = await fetch_chesscom_page("Learner", window, client, continuation=cursor, **kwargs)
        assert isinstance(page, GamePage)
        pages.append(page)
        if page.complete:
            assert page.continuation is None and not page.saturated
            return pages
        assert page.continuation is not None
        cursor = page.continuation
    pytest.fail("continuation never exhausted")


def test_only_intersecting_archive_months_requested():
    async def scenario():
        client, requests = fixture(["2026/08", "2026/09", "2026/10", "2026/11"], {"2026/09": [], "2026/10": []})
        async with client:
            pages = await consume(client)
        assert [request.url.path[-7:] for request in requests[1:]] == ["2026/09", "2026/10"]
        assert len(pages) == 2 and not pages[0].complete and pages[-1].complete
    run(scenario())


def test_two_month_range_returns_all_game_ids():
    async def scenario():
        october = game("october", at="2026-10-01T00:00:00+00:00")
        client, requests = fixture(["2026/10", "2026/09", "2026/09"], {
            "2026/09": [game("september"), game("earliest", at="2026-09-01T00:00:00+00:00")],
            "2026/10": [october],
        })
        async with client:
            pages = await consume(client, source_revision=3)
        records = [record for page in pages for record in page.records]
        assert [record.source_game_id for record in records] == ["earliest", "september", "october"]
        assert [record.played_at for record in records] == ["2026-09-01T00:00:00+00:00", "2026-09-20T12:00:00+00:00", "2026-10-01T00:00:00+00:00"]
        assert all(record.time_precision == "second" and record.source_revision == 3 for record in records)
        assert records[-1].pgn == october["pgn"]
        assert records[-1].source_digest == hashlib.sha256(october["pgn"].encode()).hexdigest()
        assert json.loads(dict(records[-1].source_metadata)["archive_url"]) == ROOT + "2026/10"
        assert len(requests) == 3  # Frozen listing, then one request for each month.
    run(scenario())


def test_month_failure_is_incomplete_and_resumable():
    async def scenario():
        responses = {"2026/09": [game("first")], "2026/10": httpx.Response(503)}
        client, requests = fixture(["2026/09", "2026/10"], responses)
        async with client:
            first = await fetch_chesscom_page("learner", WINDOW, client)
            with pytest.raises(ChesscomAcquisitionError) as failed:
                await fetch_chesscom_page("learner", WINDOW, client, continuation=first.continuation)
            assert failed.value.complete is False and failed.value.retryable
            assert failed.value.continuation == first.continuation
            responses["2026/10"] = [game("second", at="2026-10-02T00:00:00+00:00")]
            second = await fetch_chesscom_page("learner", WINDOW, client, continuation=failed.value.continuation)
        assert {record.source_game_id for page in (first, second) for record in page.records} == {"first", "second"}
        assert second.complete and [request.url.path[-7:] for request in requests[1:]] == ["2026/09", "2026/10", "2026/10"]
    run(scenario())


def test_current_month_refetch_keeps_stable_source_ids():
    async def scenario():
        responses = {"2026/10": [game("stable", at="2026-10-02T00:00:00+00:00")]}
        client, _ = fixture(["2026/10"], responses)
        async with client:
            first = await consume(client)
            responses["2026/10"].append(game("new", at="2026-10-25T00:00:00+00:00"))
            second = await consume(client)
        assert first[0].records[0] == second[0].records[0]
        assert {record.source_game_id for record in second[0].records} == {"stable", "new"}
    run(scenario())


def test_numeric_game_url_fallback_and_daily_metadata():
    async def scenario():
        raw = game("unused", uuid=None, url="https://www.chess.com/game/daily/987", time_class="daily", time_control="1/259200")
        client, _ = fixture(["2026/09"], {"2026/09": [raw, dict(raw)]})
        async with client:
            page = (await consume(client, speeds=("classical",)))[0]
        assert [item.source_game_id for item in page.records] == ["987"]
        assert dict(page.records[0].source_metadata)["time_class"] == '"daily"'
        assert [item.reason for item in page.rejections] == ["duplicate_source_id"]
    run(scenario())


def test_changed_pgn_is_not_hidden_as_duplicate():
    async def scenario():
        raw = game("same")
        responses = {"2026/09": [raw]}
        client, _ = fixture(["2026/09"], responses)
        async with client:
            first = await consume(client)
            raw["pgn"] += "\n{source correction}\n"
            second = await consume(client)
        before, after = first[0].records[0], second[0].records[0]
        assert before.source_game_id == after.source_game_id
        assert before.source_digest != after.source_digest
        assert before.source_metadata != after.source_metadata
        assert after.pgn == raw["pgn"]
    run(scenario())


def test_range_player_variant_time_control_filters():
    async def scenario():
        original = game("included")
        raw_games = [original, game("before", at="2026-08-31T23:59:59+00:00"),
                     game("end", at="2026-11-01T00:00:00+00:00"), game("variant", rules="chess960"),
                     game("speed", time_class="bullet"), game("casual", rated=False),
                     game("player", pgn=original["pgn"].replace('"Learner"', '"Stranger"')),
                     game("raw-player", white={"username": "Stranger"}, black={"username": "Opponent"}),
                     game("bad-url", url="https://evil.test/game/live/123"),
                     game("bad-time", end_time=None), game("bad-pgn", pgn="not a game"), None]
        client, _ = fixture(["2026/09"], {"2026/09": raw_games})
        async with client:
            page = (await consume(client, speeds=("rapid",), rated_only=True))[0]
        assert [record.source_game_id for record in page.records] == ["included"]
        reasons = [item.reason for item in page.rejections]
        assert reasons.count("outside_window") == 2
        assert {"unsupported_variant", "time_control_filtered", "rated_filtered", "invalid_pgn_or_player", "invalid_timestamp", "invalid_game_url", "malformed_record"} <= set(reasons)
        assert len(page.records) + len(page.rejections) == len(raw_games)
        assert ("speeds", "rapid") in page.filters
    run(scenario())


@pytest.mark.parametrize("response,code", [
    (httpx.Response(200, json={"archives": [url]}), "invalid_archive_url") for url in (
        "https://evil.test/2026/09", "https://api.chess.com@evil.test/2026/09",
        "http://api.chess.com/pub/player/learner/games/2026/09", ROOT + "2026/13",
        ROOT.replace("learner", "other") + "2026/09", ROOT + "2026/09?token=secret", None)
] + [(httpx.Response(200, text="<html>oops</html>"), "unexpected_content_type"),
     (httpx.Response(200, content=b"{", headers={"Content-Type": "application/json"}), "malformed_json"),
     (httpx.Response(200, json={}), "malformed_archives"),
     (httpx.Response(200, json={"archives": {}}), "malformed_archives"), (None, "timeout")])
def test_bad_archive_url_html_json_and_timeout_are_typed(response, code):
    async def scenario():
        requests = []
        def handle(request):
            requests.append(request)
            assert request.extensions["timeout"]["read"] == 2
            if response is None:
                raise httpx.ReadTimeout("credential=secret", request=request)
            return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(ChesscomAcquisitionError) as failed:
                await fetch_chesscom_page("learner", WINDOW, client, timeout_seconds=2)
        assert len(requests) == 1 and not failed.value.complete
        assert failed.value.code == code and not failed.value.complete
        assert "secret" not in str(failed.value) and failed.value.__cause__ is None
        if code == "invalid_archive_url":
            assert not failed.value.retryable
    run(scenario())


def test_retry_after_and_cancellation():
    async def scenario():
        client, requests = fixture(["2026/09"], {"2026/09": httpx.Response(429, headers={"Retry-After": "120"})})
        async with client:
            with pytest.raises(ChesscomAcquisitionError) as limited:
                await fetch_chesscom_page("learner", WINDOW, client)
            assert limited.value.status_code == 429 and limited.value.retry_after == "120"
            assert limited.value.continuation and not limited.value.complete
            count = len(requests)
            with pytest.raises(ChesscomAcquisitionError) as cancelled:
                await fetch_chesscom_page("learner", WINDOW, client, continuation=limited.value.continuation, cancelled=lambda: True)
            assert cancelled.value.code == "cancelled" and cancelled.value.continuation == limited.value.continuation
            assert len(requests) == count  # No sleep, inline retry or request after cancellation.
    run(scenario())


def test_native_cancellation_closes_inflight_response():
    async def scenario():
        reading = asyncio.Event()
        closed = []
        class BlockingBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                reading.set()
                await asyncio.Event().wait()
                yield b"{}"
            async def aclose(self):
                closed.append(True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, stream=BlockingBody(), headers={"Content-Type": "application/json"}))) as client:
            task = asyncio.create_task(fetch_chesscom_page("learner", WINDOW, client))
            await reading.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed == [True]
    run(scenario())


def test_empty_legitimate_archives_are_distinct_from_failure():
    async def scenario():
        client, _ = fixture([], {})
        async with client:
            page = (await consume(client))[0]
        assert page.complete and page.records == () and page.rejections == ()
        client, _ = fixture(["2026/09"], {"2026/09": httpx.Response(500)})
        async with client:
            with pytest.raises(ChesscomAcquisitionError) as failed:
                await consume(client)
        assert not failed.value.complete
    run(scenario())


def test_cursor_binds_account_window_and_filters_and_rejects_tampering():
    async def scenario():
        client, requests = fixture(["2026/09", "2026/10"], {"2026/09": [], "2026/10": []})
        async with client:
            first = await fetch_chesscom_page("learner", WINDOW, client)
            count = len(requests)
            for kwargs in ({"continuation": "not-base64"}, {"continuation": first.continuation, "rated_only": True}):
                with pytest.raises(ChesscomAcquisitionError) as failed:
                    await fetch_chesscom_page("learner", WINDOW, client, **kwargs)
                assert failed.value.code == "invalid_continuation"
            assert count == len(requests)
    run(scenario())


def test_response_bound_and_conflicting_duplicate_do_not_advance_month():
    async def scenario():
        client, _ = fixture(["2026/09"], {"2026/09": [game("same"), game("same", rated=False)]})
        async with client:
            with pytest.raises(ChesscomAcquisitionError) as conflict:
                await consume(client)
            assert conflict.value.code == "conflicting_source_id" and conflict.value.continuation
            with pytest.raises(ChesscomAcquisitionError) as bound:
                await fetch_chesscom_page("learner", WINDOW, client, max_response_bytes=8)
            assert bound.value.code == "response_too_large" and not bound.value.complete
    run(scenario())


def test_adapter_has_no_learning_or_database_import():
    source = Path(__file__).parents[1] / "app/games/providers/chesscom_acquisition.py"
    tree = ast.parse(source.read_text())
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(any(part in module.split(".") for part in ("database", "learning", "services", "main", "tasks")) for module in imports)
