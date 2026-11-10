from datetime import datetime, timezone
import asyncio
import json

import httpx
import pytest

from app.contracts.transport.game_sync import GameSyncJob
from app.games.ingestion.serialization import serialize_job


def test_game_sync_public_projection_preserves_saved_result():
    from app.games.ingestion.serialization import serialize_job

    assert serialize_job(None) is None
    saved_job = {
        "id": "job",
        "status": "complete",
        "created_at": "created",
        "started_at": "started",
        "completed_at": "completed",
        "updated_at": "updated",
        "error": None,
        "result_json": json.dumps({"imported": 3}),
    }
    unchanged_saved_job = saved_job.copy()
    assert serialize_job(saved_job)["result"] == {"imported": 3}
    assert saved_job == unchanged_saved_job


@pytest.mark.parametrize("state", ["queued", "running", "paused", "retrying", "failed"])
def test_incomplete_game_sync_never_exposes_internal_counters_as_completed_result(
    state,
):
    row = {
        "id": "job",
        "status": state,
        "created_at": "2026-09-29T10:00:00Z",
        "started_at": None,
        "completed_at": None,
        "updated_at": "2026-09-29T10:00:00Z",
        "error": "provider failed" if state == "failed" else None,
        "result_json": json.dumps(
            {
                "imported": 1,
                "providers": {
                    "lichess": {"inserted": 1, "updated": 0, "duplicates": 0}
                },
            }
        ),
    }
    public_job = serialize_job(row)
    assert public_job["result"] is None
    assert public_job["error"] == row["error"]
    assert GameSyncJob.model_validate(public_job).result is None
    assert json.loads(row["result_json"])["providers"]["lichess"]["inserted"] == 1


def test_completed_game_sync_rejects_partial_result_in_public_model():
    row = {
        "id": "job",
        "status": "complete",
        "created_at": "2026-09-29T10:00:00Z",
        "started_at": None,
        "completed_at": "2026-09-29T10:01:00Z",
        "updated_at": "2026-09-29T10:01:00Z",
        "error": None,
        "result_json": json.dumps(
            {
                "imported": 1,
                "providers": {
                    "lichess": {"inserted": 1, "updated": 0, "duplicates": 0}
                },
            }
        ),
    }
    with pytest.raises(ValueError):
        GameSyncJob.model_validate(serialize_job(row))


from app.games.providers.lichess import fetch_lichess_games_window
from app.games.providers.chesscom import (
    fetch_chesscom_archive_urls,
    fetch_chesscom_archive_month,
)


def test_lichess_sync_window_bounds_provider_request_before_persistence():
    requested_urls = []

    def handler(request: httpx.Request):
        requested_urls.append(request.url)
        return httpx.Response(
            200, text="", headers={"content-type": "application/x-chess-pgn"}
        )

    async def fetch_window():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as provider:
            return await fetch_lichess_games_window(
                "TempoPlayer",
                1000,
                2000,
                ["rapid"],
                True,
                provider,
                max_games=100,
            )

    records, counts = asyncio.run(fetch_window())
    assert records == []
    assert counts == {"fetched": 0, "filtered": 0, "rejected": 0}
    assert len(requested_urls) == 1
    assert requested_urls[0].params["since"] == "1000"
    assert requested_urls[0].params["until"] == "2000"
    assert requested_urls[0].params["max"] == "100"


def test_lichess_sync_window_rejects_unbounded_page_size():
    async def fetch_window():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: pytest.fail("Invalid window reached the provider")
            )
        ) as provider:
            await fetch_lichess_games_window(
                "TempoPlayer",
                1000,
                2000,
                ["rapid"],
                True,
                provider,
                max_games=1001,
            )

    with pytest.raises(ValueError, match="page size"):
        asyncio.run(fetch_window())


def test_chesscom_sync_fetches_one_archive_per_restartable_window():
    requested_paths = []

    def handler(request: httpx.Request):
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/archives"):
            return httpx.Response(
                200,
                json={
                    "archives": [
                        "https://api.chess.com/pub/player/tempoplayer/games/2026/08",
                        "https://api.chess.com/pub/player/tempoplayer/games/2026/09",
                    ]
                },
            )
        return httpx.Response(200, json={"games": [chesscom_game("one-month")]})

    async def fetch_month():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as provider:
            archive_urls, invalid_urls = await fetch_chesscom_archive_urls(
                "TempoPlayer",
                datetime(2026, 9, 1, tzinfo=timezone.utc),
                provider,
            )
            assert invalid_urls == 0
            assert len(archive_urls) == 1
            return await fetch_chesscom_archive_month(
                "TempoPlayer",
                archive_urls[0],
                datetime(2026, 9, 1, tzinfo=timezone.utc),
                ["rapid"],
                True,
                provider,
            )

    records, counts = asyncio.run(fetch_month())
    assert len(records) == 1
    assert counts["fetched"] == 1
    assert requested_paths == [
        "/pub/player/tempoplayer/games/archives",
        "/pub/player/tempoplayer/games/2026/09",
    ]


def test_chesscom_split_month_counts_only_games_inside_its_time_window():
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "games": [
                    chesscom_game(
                        "early",
                        end_time=int(
                            datetime(2026, 9, 5, tzinfo=timezone.utc).timestamp()
                        ),
                    ),
                    chesscom_game(
                        "late",
                        end_time=int(
                            datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp()
                        ),
                    ),
                ]
            },
        )

    async def fetch_half():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as provider:
            return await fetch_chesscom_archive_month(
                "TempoPlayer",
                "https://api.chess.com/pub/player/tempoplayer/games/2026/09",
                datetime(2026, 9, 1, tzinfo=timezone.utc),
                ["rapid"],
                True,
                provider,
                until=datetime(2026, 9, 10, tzinfo=timezone.utc),
            )

    records, counts = asyncio.run(fetch_half())
    assert [record.provider_game_id for record in records] == ["early"]
    assert counts == {"fetched": 1, "filtered": 0, "rejected": 0}


def pgn(game_url: str, white: str = "TempoPlayer", result: str = "1-0") -> str:
    return f'''[Event "Live Chess - chess"]
[Site "Chess.com"]
[Link "{game_url}"]
[Date "2026.09.18"]
[UTCDate "2026.09.18"]
[UTCTime "12:00:00"]
[White "{white}"]
[Black "Opponent"]
[Result "{result}"]

1. e4 e5 2. Nf3 Nc6 {result}
'''


def chesscom_game(identifier: str, **overrides) -> dict:
    game = {
        "uuid": identifier,
        "url": f"https://www.chess.com/game/live/{identifier}",
        "pgn": pgn(f"https://www.chess.com/game/live/{identifier}"),
        "end_time": int(datetime(2026, 9, 18, 12, tzinfo=timezone.utc).timestamp()),
        "time_class": "rapid",
        "rated": True,
        "rules": "chess",
    }
    game.update(overrides)
    return game


def install_transport(monkeypatch, handler):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.game_sync.httpx.AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
