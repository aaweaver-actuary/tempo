from datetime import datetime, timezone
import time
from datetime import date
import json

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app
import app.services.game_sync_coordinator as game_sync_coordinator


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
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at)
                   VALUES('review-card','rep','prefix',? ,?,'learning',?,?)""",
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
        assert public_game["moves"] == ["e2e4", "e7e5", "g1f3", "b8c6"]
        assert public_game["timeline"] == []
        assert {
            "provider_game_id",
            "content_hash",
            "adaptive_excluded",
            "moves_json",
            "timeline_json",
            "expected_json",
        }.isdisjoint(public_game)
        detail = client.get(f"/api/games/{public_game['id']}").json()
        assert detail == public_game


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
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at)
                   VALUES('foreground-review','load-rep','prefix',?,?,'learning',?,?)""",
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
