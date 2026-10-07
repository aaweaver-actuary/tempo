from datetime import datetime, timezone
import time
from datetime import date
import asyncio
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app import database
from app.models import GameSyncRequest, GameSyncJob
from app.main import app
import app.main as main_module
import app.services.game_sync_coordinator as game_sync_coordinator


<<<<<<< HEAD
=======
def test_game_sync_public_projection_preserves_coordinator_import_contract():
    from app.services.game_sync_serialization import serialize_job

    assert game_sync_coordinator.serialize_job is serialize_job
    assert serialize_job(None) is None
    saved_job = {"id": "job", "status": "complete", "created_at": "created",
                 "started_at": "started", "completed_at": "completed",
                 "updated_at": "updated", "error": None,
                 "result_json": json.dumps({"imported": 3})}
    unchanged_saved_job = saved_job.copy()
    assert serialize_job(saved_job)["result"] == {"imported": 3}
    assert saved_job == unchanged_saved_job


>>>>>>> main
@pytest.mark.parametrize("state", ["queued", "running", "paused", "retrying", "failed"])
def test_incomplete_game_sync_never_exposes_internal_counters_as_completed_result(state):
    row = {"id": "job", "status": state, "created_at": "2026-09-29T10:00:00Z",
           "started_at": None, "completed_at": None, "updated_at": "2026-09-29T10:00:00Z",
           "error": "provider failed" if state == "failed" else None,
           "result_json": json.dumps({"imported": 1, "providers": {
               "lichess": {"inserted": 1, "updated": 0, "duplicates": 0}}})}
    public_job = game_sync_coordinator.serialize_job(row)
    assert public_job["result"] is None
    assert public_job["error"] == row["error"]
    assert GameSyncJob.model_validate(public_job).result is None
    assert json.loads(row["result_json"])["providers"]["lichess"]["inserted"] == 1


def test_completed_game_sync_rejects_partial_result_in_public_model():
    row = {"id": "job", "status": "complete", "created_at": "2026-09-29T10:00:00Z",
           "started_at": None, "completed_at": "2026-09-29T10:01:00Z",
           "updated_at": "2026-09-29T10:01:00Z", "error": None,
           "result_json": json.dumps({"imported": 1, "providers": {
               "lichess": {"inserted": 1, "updated": 0, "duplicates": 0}}})}
    with pytest.raises(ValueError):
        GameSyncJob.model_validate(game_sync_coordinator.serialize_job(row))
from app.services.lichess_client import fetch_lichess_games_window
from app.services.chesscom_client import (
    fetch_chesscom_archive_urls, fetch_chesscom_archive_month,
)


def test_lichess_sync_window_bounds_provider_request_before_persistence():
    requested_urls = []

    def handler(request: httpx.Request):
        requested_urls.append(request.url)
        return httpx.Response(200, text="", headers={"content-type": "application/x-chess-pgn"})

    async def fetch_window():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as provider:
            return await fetch_lichess_games_window(
                "TempoPlayer", 1000, 2000, ["rapid"], True, provider, max_games=100,
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
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: pytest.fail("Invalid window reached the provider")
        )) as provider:
            await fetch_lichess_games_window(
                "TempoPlayer", 1000, 2000, ["rapid"], True, provider, max_games=1001,
            )

    with pytest.raises(ValueError, match="page size"):
        asyncio.run(fetch_window())


def test_chesscom_sync_fetches_one_archive_per_restartable_window():
    requested_paths = []

    def handler(request: httpx.Request):
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/archives"):
            return httpx.Response(200, json={"archives": [
                "https://api.chess.com/pub/player/tempoplayer/games/2026/08",
                "https://api.chess.com/pub/player/tempoplayer/games/2026/09",
            ]})
        return httpx.Response(200, json={"games": [chesscom_game("one-month")]})

    async def fetch_month():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as provider:
            archive_urls, invalid_urls = await fetch_chesscom_archive_urls(
                "TempoPlayer", datetime(2026, 9, 1, tzinfo=timezone.utc), provider,
            )
            assert invalid_urls == 0
            assert len(archive_urls) == 1
            return await fetch_chesscom_archive_month(
                "TempoPlayer", archive_urls[0],
                datetime(2026, 9, 1, tzinfo=timezone.utc), ["rapid"], True, provider,
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
        return httpx.Response(200, json={"games": [
            chesscom_game("early", end_time=int(datetime(2026, 9, 5, tzinfo=timezone.utc).timestamp())),
            chesscom_game("late", end_time=int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp())),
        ]})

    async def fetch_half():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as provider:
            return await fetch_chesscom_archive_month(
                "TempoPlayer", "https://api.chess.com/pub/player/tempoplayer/games/2026/09",
                datetime(2026, 9, 1, tzinfo=timezone.utc), ["rapid"], True, provider,
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
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def complete_sync(client: TestClient, payload: dict) -> dict:
    enqueue = client.post("/api/games/sync", json=payload)
    assert enqueue.status_code == 202
    job_id = enqueue.json()["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get("/api/games/sync/status").json().get("active_job")
        if job and job["id"] == job_id and job["status"] == "complete":
            return job["result"]
        if job and job["id"] == job_id and job["status"] == "failed":
            raise AssertionError(job["error"])
        time.sleep(0.01)
    raise AssertionError("Game sync did not complete")


def test_chesscom_mixed_case_username_and_shared_site_header_import_every_distinct_game(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    requested_paths = []

    def handler(request: httpx.Request):
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/archives"):
            return httpx.Response(
                200,
                json={"archives": ["https://api.chess.com/pub/player/tempoplayer/games/2026/09"]},
            )
        return httpx.Response(
            200, json={"games": [chesscom_game("first"), chesscom_game("second")]}
        )

    install_transport(monkeypatch, handler)
    with TestClient(app) as client:
        result = complete_sync(client, {"chesscom_username": "TempoPlayer"})
        assert result["providers"]["chess.com"]["inserted"] == 2
        assert client.get("/api/games/summary").json()["total"] == 2
    assert requested_paths[0].endswith("/player/tempoplayer/games/archives")


def test_one_provider_failure_does_not_discard_other_provider_success(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")

    def handler(request: httpx.Request):
        if request.url.host == "lichess.org":
            return httpx.Response(503, text="unavailable")
        if request.url.path.endswith("/archives"):
            return httpx.Response(
                200,
                json={"archives": ["https://api.chess.com/pub/player/tempoplayer/games/2026/09"]},
            )
        return httpx.Response(200, json={"games": [chesscom_game("survivor")]})

    install_transport(monkeypatch, handler)
    with TestClient(app) as client:
        result = complete_sync(
            client,
            {"lichess_username": "TempoPlayer", "chesscom_username": "TempoPlayer"},
        )
        assert result["providers"]["lichess"]["failed"] == 1
        assert result["providers"]["chess.com"]["inserted"] == 1
        assert client.get("/api/games/summary").json()["total"] == 1


def test_incremental_overlap_catches_late_games_without_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    calls = 0
    since_values = []

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        since_values.append(int(request.url.params["since"]))
        exported = pgn("https://lichess.org/original", "TempoPlayer")
        if calls > 1:
            exported += "\n" + pgn("https://lichess.org/late", "TempoPlayer")
        return httpx.Response(200, text=exported, headers={"content-type": "application/x-chess-pgn"})

    install_transport(monkeypatch, handler)
    with TestClient(app) as client:
        first = complete_sync(client, {"lichess_username": "TempoPlayer"})
        second = complete_sync(client, {"lichess_username": "TempoPlayer"})
        assert first["imported"] == 1
        assert second["providers"]["lichess"]["inserted"] == 1
        assert second["providers"]["lichess"]["duplicates"] == 1
        assert client.get("/api/games/summary").json()["total"] == 2
    assert since_values[1] <= int(datetime(2026, 9, 18, 12, tzinfo=timezone.utc).timestamp() * 1000)


def test_sync_reports_filtered_rejected_and_duplicate_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")

    def handler(request: httpx.Request):
        if request.url.path.endswith("/archives"):
            return httpx.Response(
                200,
                json={"archives": ["https://api.chess.com/pub/player/tempoplayer/games/2026/09"]},
            )
        return httpx.Response(
            200,
            json={
                "games": [
                    chesscom_game("valid"),
                    chesscom_game("bullet", time_class="bullet"),
                    chesscom_game("bad", pgn="not a pgn"),
                ]
            },
        )

    install_transport(monkeypatch, handler)
    with TestClient(app) as client:
        complete_sync(client, {"chesscom_username": "TempoPlayer"})
        counts = complete_sync(client, {"chesscom_username": "TempoPlayer"})[
            "providers"
        ]["chess.com"]
        assert counts["filtered"] == 1
        assert counts["rejected"] == 1
        assert counts["duplicates"] == 1
        assert counts["failed"] == 0


def test_sync_status_never_exposes_persistence_only_result_json(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO game_sync_state(provider,username,status,last_result_json)
                   VALUES('lichess','TempoPlayer','idle',NULL)"""
            )
        response = client.get("/api/games/sync/status")
        assert response.status_code == 200
        provider = response.json()["providers"][0]
        assert "last_result_json" not in provider
        assert provider["last_result"] is None


def test_provider_status_never_inherits_another_provider_error(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")

    def handler(request: httpx.Request):
        if request.url.host == "lichess.org":
            return httpx.Response(404, text="not found")
        if request.url.path.endswith("/archives"):
            return httpx.Response(200, json={"archives": []})
        raise AssertionError(f"Unexpected request: {request.url}")

    install_transport(monkeypatch, handler)
    with TestClient(app) as client:
        complete_sync(
            client,
            {"lichess_username": "Missing", "chesscom_username": "TempoPlayer"},
        )
        providers = {
            provider["provider"]: provider
            for provider in client.get("/api/games/sync/status").json()["providers"]
        }
        assert providers["lichess"]["last_error"] == "Lichess username not found"
        assert providers["chess.com"]["last_error"] is None


@pytest.mark.parametrize("locked_step", ["derivation", "finalization"])
def test_sync_finalization_retries_transient_database_lock_without_refetching_providers(
    tmp_path, monkeypatch, locked_step
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    provider_calls = []

    async def successful_providers(_request):
        provider_calls.append(1)
        return {"imported": 0, "providers": {
            "lichess": {"provider": "lichess", "status": "idle", "error": None},
            "chess.com": {"provider": "chess.com", "status": "idle", "error": None},
        }, "_changed_game_ids": ["lichess:one"]}

    monkeypatch.setattr(game_sync_coordinator, "sync_providers", successful_providers)
    original_finish = game_sync_coordinator._finish_job
    operation_attempts = []

    def transiently_locked_finish(job_id, result):
        if locked_step == "finalization":
            operation_attempts.append(1)
        if locked_step == "finalization" and len(operation_attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        original_finish(job_id, result)

    def transiently_locked_derivation(_game_id, *, background):
        assert background is True
        if locked_step == "derivation":
            operation_attempts.append(1)
        if locked_step == "derivation" and len(operation_attempts) == 1:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(game_sync_coordinator, "_finish_job", transiently_locked_finish)
    monkeypatch.setattr(game_sync_coordinator, "enqueue_game_derivation", transiently_locked_derivation)
    database.initialize()
    job_id = game_sync_coordinator.enqueue_sync(GameSyncRequest(lichess_username="TempoPlayer"))
    with database.read_connection() as db:
        job = dict(db.execute("SELECT * FROM game_sync_jobs WHERE id=?", (job_id,)).fetchone())
    game_sync_coordinator._execute_job(job)
    with database.read_connection() as db:
        completed = dict(db.execute("SELECT status,result_json FROM game_sync_jobs WHERE id=?", (job_id,)).fetchone())
    assert completed["status"] == "complete"
    provider_results = json.loads(completed["result_json"])["providers"]
    assert provider_results["lichess"]["error"] is None
    assert provider_results["chess.com"]["error"] is None
    assert len(operation_attempts) == 2
    assert len(provider_calls) == 1


def test_slow_game_sync_does_not_delay_settings_read(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")

    def handler(_request: httpx.Request):
        time.sleep(0.35)
        return httpx.Response(404, text="not found")

    install_transport(monkeypatch, handler)
    with TestClient(app) as client:
        enqueue = client.post("/api/games/sync", json={"lichess_username": "Slow"})
        assert enqueue.status_code == 202
        started = time.monotonic()
        settings = client.get("/api/settings")
        elapsed = time.monotonic() - started
        assert settings.status_code == 200
        assert elapsed < 0.2
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get("/api/games/sync/status").json()["active_job"]
            if job["status"] == "complete":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Slow sync did not finish")


def test_background_game_sync_routes_use_background_database_sections(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    monkeypatch.setattr(main_module.coordinator, "wake", lambda: None)
    background_headers = {"X-Tempo-Work-Class": "background"}
    with TestClient(app) as client:
        assert client.get("/api/settings", headers=background_headers).status_code == 200
        assert (
            client.get("/api/games/sync/status", headers=background_headers).status_code
            == 200
        )
        enqueue = client.post(
            "/api/games/sync",
            headers=background_headers,
            json={"lichess_username": "BackgroundPlayer"},
        )
        assert enqueue.status_code == 202
        assert enqueue.json()["status"] == "queued"


def test_sync_enqueue_does_not_rewrite_settings_or_schedule_coverage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    scheduled_repertoires = []
    monkeypatch.setattr(
        main_module,
        "enqueue_coverage_refresh",
        lambda repertoire_id, **_kwargs: scheduled_repertoires.append(repertoire_id),
    )
    monkeypatch.setattr(main_module.coordinator, "wake", lambda: None)
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        response = client.post(
            "/api/games/sync",
            json={"lichess_username": "DifferentPlayer"},
        )
        assert response.status_code == 202
        assert client.get("/api/settings").json() == before
        assert scheduled_repertoires == []


def test_only_coverage_setting_changes_enqueue_one_refresh_per_repertoire(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    scheduled_repertoires = []
    monkeypatch.setattr(
        main_module,
        "enqueue_coverage_refresh",
        lambda repertoire_id, **_kwargs: scheduled_repertoires.append(repertoire_id),
    )
    monkeypatch.setattr(main_module.coordinator, "wake", lambda: None)
    with TestClient(app) as client:
        with database.connection() as connection:
            connection.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('settings-rep','Settings','settings.pgn',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.execute(
                "INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at) VALUES('settings-rep','clean',?)",
                (date.today().isoformat(),),
            )
        settings = client.get("/api/settings").json()
        unchanged_coverage = {**settings, "new_cards_per_day": 12}
        assert client.put("/api/settings", json=unchanged_coverage).status_code == 200
        assert scheduled_repertoires == []
        changed_coverage = {
            **unchanged_coverage,
            "coverage_horizon_fullmoves": unchanged_coverage[
                "coverage_horizon_fullmoves"
            ]
            + 1,
        }
        assert client.put("/api/settings", json=changed_coverage).status_code == 200
        assert scheduled_repertoires == ["settings-rep"]


def test_correct_card_review_advances_while_game_sync_is_active(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")

    def handler(_request: httpx.Request):
        time.sleep(0.35)
        return httpx.Response(404, text="not found")

    install_transport(monkeypatch, handler)
    with TestClient(app) as client:
        today = date.today().isoformat()
        with database.connection() as db:
            db.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Rep','rep.pgn',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            db.execute(
                "INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at) VALUES('rep','clean',?)",
                (today,),
            )
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,trained_color)
                   VALUES('review-card','rep','prefix',? ,?,'learning',?,?, 'white')""",
                (
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    json.dumps(["e2e4"]),
                    today,
                    today,
                ),
            )
            queue_entry_id = db.execute(
                "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'review-card',0)",
                (today,),
            ).lastrowid
        assert client.post(
            "/api/games/sync", json={"lichess_username": "Slow"}
        ).status_code == 202
        started = time.monotonic()
        review = client.post(
            "/api/cards/review-card/review",
            json={"outcome": "correct", "queue_entry_id": queue_entry_id},
        )
        assert review.status_code == 200
        assert time.monotonic() - started < 0.2
        with database.connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id='review-card'"
            ).fetchone()[0] == 1
            assert db.execute(
                "SELECT status FROM daily_queue WHERE id=?", (queue_entry_id,)
            ).fetchone()[0] == "complete"


def test_game_summaries_never_expose_persistence_only_sync_fields(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")

    def handler(request: httpx.Request):
        if request.url.path.endswith("/archives"):
            return httpx.Response(
                200,
                json={
                    "archives": [
                        "https://api.chess.com/pub/player/tempoplayer/games/2026/09"
                    ]
                },
            )
        return httpx.Response(200, json={"games": [chesscom_game("private-fields")]})

    install_transport(monkeypatch, handler)
    with TestClient(app) as client:
        complete_sync(client, {"chesscom_username": "TempoPlayer"})
        summary_response = client.get("/api/games/summary")
        assert summary_response.status_code == 200
        public_game = summary_response.json()["games"][0]
        assert {
            "provider_game_id",
            "content_hash",
            "adaptive_excluded",
            "moves_json",
            "timeline_json",
            "expected_json",
            "moves",
            "timeline",
        }.isdisjoint(public_game)
        detail = client.get(f"/api/games/{public_game['id']}").json()
        assert detail["moves"] == ["e2e4", "e7e5", "g1f3", "b8c6"]
        assert detail["timeline"] == []


def test_games_summary_pagination_omits_heavy_fields_and_caps_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        now = datetime.now(timezone.utc).isoformat()
        with database.connection() as db:
            db.executemany(
                """INSERT INTO imported_games(
                       id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json
                   ) VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,'[\"e2e4\"]')""",
                [(f"page-{index:02d}", now, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1") for index in range(75)],
            )
        first = client.get("/api/games/summary", params={"limit": 50}).json()
        assert first["total"] == 75
        assert len(first["games"]) == 50
        assert first["next_cursor"]
        assert all("moves" not in game and "timeline" not in game for game in first["games"])
        second = client.get("/api/games/summary", params={"limit": 50, "cursor": first["next_cursor"]}).json()
        assert len(second["games"]) == 25
        assert not {game["id"] for game in first["games"]} & {game["id"] for game in second["games"]}


def test_correct_review_succeeds_while_one_thousand_derivation_jobs_are_queued(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('load-rep','Load','load.pgn',?)",
                (now,),
            )
            db.execute(
                "INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at) VALUES('load-rep','clean',?)",
                (now,),
            )
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,trained_color)
                   VALUES('foreground-review','load-rep','prefix',?,?,'learning',?,?, 'white')""",
                (start_fen, json.dumps(["e2e4"]), today, today),
            )
            queue_entry_id = db.execute(
                "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'foreground-review',0)",
                (today,),
            ).lastrowid
            games = [
                (
                    f"queued-{index}",
                    "lichess",
                    "TempoPlayer",
                    now,
                    "rapid",
                    1,
                    "white",
                    "1-0",
                    start_fen,
                    "[]",
                )
                for index in range(1000)
            ]
            db.executemany(
                """INSERT INTO imported_games(
                       id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                games,
            )
            db.executemany(
                "INSERT INTO game_derivation_jobs(game_id,status,updated_at) VALUES(?,'queued',?)",
                [(game[0], now) for game in games],
            )
        started = time.monotonic()
        response = client.post(
            "/api/cards/foreground-review/review",
            json={"outcome": "correct", "queue_entry_id": queue_entry_id},
        )
        assert response.status_code == 200
        assert response.json()["persisted"] is True
        assert time.monotonic() - started < 1
        with database.connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id='foreground-review'"
            ).fetchone()[0] == 1


def test_sync_jobs_are_not_starved_behind_derivation_work(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")

    def handler(_request: httpx.Request):
        return httpx.Response(404, text="not found")

    install_transport(monkeypatch, handler)
    original_execute_derivation = game_sync_coordinator._execute_derivation

    def deliberately_slow_derivation(game_id: str):
        time.sleep(0.05)
        original_execute_derivation(game_id)

    monkeypatch.setattr(
        game_sync_coordinator, "_execute_derivation", deliberately_slow_derivation
    )
    now = datetime.now(timezone.utc).isoformat()
    start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    with TestClient(app) as client:
        with database.connection() as db:
            games = [
                (
                    f"starvation-{index}",
                    "lichess",
                    "TempoPlayer",
                    now,
                    "rapid",
                    1,
                    "white",
                    "1-0",
                    start_fen,
                    "[]",
                )
                for index in range(100)
            ]
            db.executemany(
                """INSERT INTO imported_games(
                       id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                games,
            )
            db.executemany(
                "INSERT INTO game_derivation_jobs(game_id,status,updated_at) VALUES(?,'queued',?)",
                [(game[0], now) for game in games],
            )
        started = time.monotonic()
        complete_sync(client, {"lichess_username": "Missing"})
        assert time.monotonic() - started < 1
