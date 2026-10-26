"""T03 fake-transport regressions; no account, storage, Study or network."""

import asyncio
import ast
import hashlib
import json
from pathlib import Path
import sys

import httpx
import pytest

# Source-only shared values, matching #482's focused setup; no installation.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/game-review-contracts/src"))
sys.path.insert(0, str(ROOT / "packages/chess-core/src"))

from app.contracts.product_v02.games import GamePage, GameWindow
from app.games.providers.lichess_acquisition import (
    LichessAcquisitionError, fetch_lichess_page,
)


START = 1790812800000
WINDOW = GameWindow(start_at="2026-10-01T00:00:00Z", end_at="2026-10-02T00:00:00Z", precision="millisecond")


def source(number, *, timestamp=None, **changes):
    game_id = f"game{number:04d}"
    value = {"id": game_id, "createdAt": START + number if timestamp is None else timestamp,
        "speed": "rapid", "rated": True,
        "pgn": f'[Event "Rated rapid game"]\n[Site "https://lichess.org/{game_id}"]\n'
               '[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
               '1. e4 {original comment} e5 2. Nf3 Nc6 1-0\n'}
    value.update(changes)
    return value


def response(rows):
    return httpx.Response(200, content=b"\n".join(json.dumps(row).encode() for row in rows),
        headers={"Content-Type": "application/x-ndjson"})


class Export:
    def __init__(self, rows):
        self.rows = rows
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        params = request.url.params
        rows = sorted((row for row in self.rows if int(params["since"]) <= row["createdAt"] < int(params["until"])),
            key=lambda row: row["createdAt"])
        # Keep filters deliberately local too: ignored provider filters must be accounted.
        return response(rows[:int(params["max"])])


async def acquire(handler, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await fetch_lichess_page(account="Learner", window=WINDOW, client=client,
            source_revision=1, **kwargs)


async def consume(export, **kwargs):
    pages, token = [], None
    async with httpx.AsyncClient(transport=httpx.MockTransport(export)) as client:
        for _ in range(20):
            page = await fetch_lichess_page(account="Learner", window=WINDOW, client=client,
                source_revision=1, continuation=token, **kwargs)
            assert isinstance(page, GamePage)
            pages.append(page)
            if page.complete:
                return pages
            assert page.continuation is not None
            token = page.continuation
    pytest.fail("bounded consumer did not reach completion")


def test_more_than_one_page_is_complete_without_loss():
    rows = [source(number) for number in range(205)]
    export = Export(rows)
    pages = asyncio.run(consume(export))
    ids = [record.source_game_id for page in pages for record in page.records]
    assert len(pages) == 3
    assert len(ids) == len(set(ids)) == 205
    assert set(ids) == {row["id"] for row in rows}
    assert all(not page.complete and page.saturated for page in pages[:-1])
    assert pages[-1].complete and pages[-1].continuation is None
    first = pages[0].records[0]
    assert first.pgn == rows[0]["pgn"]
    assert first.source_digest == hashlib.sha256(first.pgn.encode()).hexdigest()
    assert first.time_precision == "millisecond" and first.source_revision == 1
    assert first.played_at == "2026-10-01T00:00:00.000+00:00"
    assert pages[0].account == "learner" and pages[0].provider == "lichess"
    assert dict(pages[0].filters)["perfType"] == "blitz,bullet,classical,rapid"
    assert "rated" not in export.requests[0].url.params
    assert export.requests[0].url.params["until"] == str(START + 86400000)


def test_saturated_page_not_complete():
    page = asyncio.run(acquire(Export([source(number) for number in range(100)])))
    assert not page.complete and page.saturated and page.continuation
    # A following short overlap page proves exhaustion even at exactly one page.
    tail = asyncio.run(acquire(Export([source(number) for number in range(100)]), continuation=page.continuation))
    assert tail.complete and not tail.records


def test_equal_timestamp_boundary_keeps_all_ids():
    rows = [source(number, timestamp=START + (0 if number < 155 else 1)) for number in range(205)]
    pages = asyncio.run(consume(Export(rows)))
    ids = [record.source_game_id for page in pages for record in page.records]
    assert len(ids) == len(set(ids)) == 205
    assert set(ids) == {row["id"] for row in rows}
    assert pages[-1].complete


def test_retry_replays_same_page():
    export = Export([source(number) for number in range(205)])
    first = asyncio.run(acquire(export))
    replay = asyncio.run(acquire(export))
    assert replay == first
    tail = asyncio.run(acquire(export, continuation=first.continuation))
    repeated_tail = asyncio.run(acquire(export, continuation=first.continuation))
    assert repeated_tail == tail
    assert tail.records[0].source_game_id == "game0100"


class BrokenStream(httpx.AsyncByteStream):
    def __init__(self, mode):
        self.mode = mode
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        yield json.dumps(source(0)).encode() + b"\n"
        if self.mode == "disconnect":
            raise httpx.ReadError("private upstream diagnostic must not escape")
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def test_interrupted_stream_preserves_checkpoint():
    checkpoint = asyncio.run(acquire(Export([source(number) for number in range(205)]))).continuation

    async def run():
        stream = BrokenStream("disconnect")
        before = asyncio.all_tasks()
        with pytest.raises(LichessAcquisitionError) as failure:
            await acquire(lambda request: httpx.Response(200, stream=stream,
                headers={"Content-Type": "application/x-ndjson"}), continuation=checkpoint)
        assert failure.value.kind == "transport_error"
        assert failure.value.continuation == checkpoint
        assert str(failure.value) == "transport_error" and failure.value.__suppress_context__
        assert stream.closed and asyncio.all_tasks() == before
    asyncio.run(run())


def test_filtered_or_rejected_records_are_accounted():
    rows = [source(0), source(1, speed="bullet"), source(2, pgn="bad PGN"), source(3, rated=False)]
    page = asyncio.run(acquire(Export(rows), speeds=("rapid",), rated=True))
    assert page.complete and len(page.records) == 1
    assert {item.source_game_id: item.reason for item in page.rejections} == {
        "game0001": "filtered_speed", "game0002": "invalid_pgn", "game0003": "filtered_rated"}
    assert len(page.records) + len(page.rejections) == len(rows)
    assert dict(page.filters) == {"perfType": "rapid", "rated": "true"}


def test_429_preserves_retry_after():
    checkpoint = asyncio.run(acquire(Export([source(0)]), page_size=1)).continuation
    for retry_after in ("60", "Wed, 21 Oct 2026 07:28:00 GMT"):
        with pytest.raises(LichessAcquisitionError) as failure:
            asyncio.run(acquire(lambda request: httpx.Response(429, headers={"Retry-After": retry_after}),
                page_size=1, continuation=checkpoint))
        assert failure.value.kind == "rate_limited" and failure.value.status_code == 429
        assert failure.value.retry_after == retry_after and failure.value.continuation == checkpoint


def test_not_found_html_timeout_and_cancel_are_typed():
    for status, headers, body, kind in ((404, {}, b"private details", "not_found"),
            (200, {"Content-Type": "text/html"}, b"<html>private</html>", "invalid_response"),
            (200, {"Content-Type": "application/x-ndjson"}, b"{broken", "invalid_response"),
            (302, {"Location": "https://example.invalid/private"}, b"", "http_error")):
        with pytest.raises(LichessAcquisitionError) as failure:
            asyncio.run(acquire(lambda request: httpx.Response(status, headers=headers, content=body)))
        assert failure.value.kind == kind
    with pytest.raises(LichessAcquisitionError, match="cancelled"):
        asyncio.run(acquire(lambda request: pytest.fail("cancelled before I/O"), cancelled=lambda: True))

    async def lifecycle():
        for mode in ("timeout", "cancel"):
            stream = BrokenStream(mode)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                    httpx.Response(200, stream=stream, headers={"Content-Type": "application/x-ndjson"}))) as client:
                before = asyncio.all_tasks()
                task = asyncio.create_task(fetch_lichess_page(account="Learner", window=WINDOW,
                    source_revision=1, client=client, timeout_seconds=0.02 if mode == "timeout" else 10))
                await stream.started.wait()
                if mode == "cancel":
                    task.cancel()
                with pytest.raises(LichessAcquisitionError, match="timeout" if mode == "timeout" else "cancelled"):
                    await task
                assert stream.closed and task.done() and asyncio.all_tasks() == before
    asyncio.run(lifecycle())


def test_adapter_has_no_storage_or_learning_dependency():
    path = ROOT / "backend/app/games/providers/lichess_acquisition.py"
    tree = ast.parse(path.read_text())
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(any(part in name.split(".") for part in (
        "database", "services", "learning", "study", "storage", "main", "product_composition")) for name in imports)
    assert "app.contracts.product_v02.games" in imports
    assert "ingestion.normalizer" in imports


@pytest.mark.parametrize("changes", [{"createdAt": None}, {"id": None}, {"createdAt": START - 1}])
def test_unverifiable_progress_never_returns_a_checkpoint(changes):
    with pytest.raises(LichessAcquisitionError, match="invalid_response") as failure:
        asyncio.run(acquire(lambda request: response([source(0, **changes)])))
    assert failure.value.continuation is None


def test_ambiguous_boundary_is_explicit_and_does_not_skip_any_timestamp():
    rows = [source(number, timestamp=START) for number in range(1001)]
    first = asyncio.run(acquire(Export(rows), page_size=1000))
    assert first.saturated and not first.complete and len(first.records) == 1000
    with pytest.raises(LichessAcquisitionError, match="ambiguous_boundary") as failure:
        asyncio.run(acquire(lambda request: pytest.fail("must not guess past saturated timestamp"),
            page_size=1000, continuation=first.continuation))
    assert failure.value.continuation == first.continuation


def test_continuation_is_bound_to_inputs_and_changed_overlap_is_quarantined():
    export = Export([source(number) for number in range(205)])
    first = asyncio.run(acquire(export))
    with pytest.raises(LichessAcquisitionError, match="invalid_continuation"):
        asyncio.run(acquire(lambda request: pytest.fail("invalid cursor reached HTTP"),
            continuation=first.continuation, rated=False))
    export.rows[99] = source(99, pgn=source(99)["pgn"] + "\n")
    with pytest.raises(LichessAcquisitionError, match="changed_boundary"):
        asyncio.run(acquire(export, continuation=first.continuation))


def test_response_and_record_bounds_are_enforced():
    with pytest.raises(LichessAcquisitionError, match="response_limit"):
        asyncio.run(acquire(lambda request: response([source(0)]), max_response_bytes=10))
    with pytest.raises(LichessAcquisitionError, match="response_limit"):
        asyncio.run(acquire(lambda request: response([source(0), source(1)]), page_size=1))
    with pytest.raises(LichessAcquisitionError, match="invalid_request"):
        asyncio.run(acquire(lambda request: pytest.fail("invalid bound reached HTTP"), page_size=1001))
