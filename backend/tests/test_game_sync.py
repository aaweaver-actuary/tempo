from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app


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
        response = client.post(
            "/api/games/sync", json={"chesscom_username": "TempoPlayer"}
        )
        assert response.status_code == 200
        assert response.json()["providers"]["chess.com"]["inserted"] == 2
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
        result = client.post(
            "/api/games/sync",
            json={"lichess_username": "TempoPlayer", "chesscom_username": "TempoPlayer"},
        ).json()
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
        first = client.post("/api/games/sync", json={"lichess_username": "TempoPlayer"}).json()
        second = client.post("/api/games/sync", json={"lichess_username": "TempoPlayer"}).json()
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
        client.post("/api/games/sync", json={"chesscom_username": "TempoPlayer"})
        counts = client.post(
            "/api/games/sync", json={"chesscom_username": "TempoPlayer"}
        ).json()["providers"]["chess.com"]
        assert counts["filtered"] == 1
        assert counts["rejected"] == 1
        assert counts["duplicates"] == 1
        assert counts["failed"] == 0
