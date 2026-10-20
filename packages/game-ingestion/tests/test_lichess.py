from datetime import datetime, timedelta, timezone
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest
from tempo_game_ingestion.lichess import PublicImportError, PublicResponse, UrlPublicTransport, fetch_lichess_games
from tempo_game_ingestion.public_cli import main

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
SCOPE = dict(workspace_id="w", username="Learner", since=START, until=START + timedelta(days=1),
             limit=2, game_revision=1, source_revision=1)
PGN = '[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n'


def record(identity="abcd1234", milliseconds=0, pgn=PGN):
    return dict(id=identity, createdAt=int(START.timestamp() * 1000) + milliseconds, pgn=pgn)


class FakeTransport:
    def __init__(self, records=(), *, response=None):
        self.response = response or PublicResponse(200, b"\n".join(json.dumps(value).encode() for value in records))
        self.calls = []
    def get(self, url, **options):
        self.calls.append((url, options))
        return self.response


def test_public_endpoint_scope_and_canonical_outputs_use_one_explicit_request():
    transport = FakeTransport([record()])
    batch = fetch_lichess_games(transport, **SCOPE)
    assert len(transport.calls) == 1
    url, options = transport.calls[0]
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "lichess.org", "/api/games/user/learner")
    assert parse_qs(parsed.query) == {"since": [str(int(START.timestamp() * 1000))],
        "until": [str(int((START + timedelta(days=1)).timestamp() * 1000) - 1)], "max": ["2"],
        "sort": ["dateAsc"], "pgnInJson": ["true"], "moves": ["true"], "ongoing": ["false"],
        "finished": ["true"], "evals": ["false"]}
    assert options == {"timeout": 10, "max_bytes": 8 * 1024 * 1024}
    game, = batch.games
    assert game.moves == ["e2e4", "e7e5"]
    assert game.source_id == "lichess:abcd1234"
    assert game.played_at == START.isoformat()
    assert game == fetch_lichess_games(FakeTransport([record()]), **SCOPE).games[0]
    assert game.game_id != fetch_lichess_games(FakeTransport([record()]), **(SCOPE | {"workspace_id": "other"})).games[0].game_id
    assert not batch.limit_reached


def test_full_page_is_explicitly_limited_and_never_implies_window_completion_or_fetches_another_page():
    transport = FakeTransport([record()])
    result = fetch_lichess_games(transport, **(SCOPE | {"limit": 1}))
    assert result.limit_reached
    assert len(transport.calls) == 1


@pytest.mark.parametrize("changes", [
    {"username": "../private"}, {"username": "user?token=secret"}, {"username": ""},
    {"since": START.replace(tzinfo=None)}, {"until": START},
    {"until": START + timedelta(days=367)}, {"until": START + timedelta(microseconds=1)}, {"limit": 0}, {"limit": 101}, {"limit": True},
    {"timeout": 0}, {"timeout": 16}, {"game_revision": 0}, {"source_revision": True}, {"workspace_id": ""},
])
def test_invalid_public_scope_never_calls_transport(changes):
    transport = FakeTransport()
    with pytest.raises(PublicImportError):
        fetch_lichess_games(transport, **(SCOPE | changes))
    assert transport.calls == []


@pytest.mark.parametrize("records,reason", [
    ([record(), record()], "duplicate"),
    ([record(milliseconds=2), record("other", milliseconds=1)], "order"),
    ([record(milliseconds=-1)], "window"),
    ([record(milliseconds=86400000)], "window"),
    ([{"id": "a", "createdAt": True, "pgn": PGN}], "timestamp"),
    ([{"id": "a", "createdAt": int(START.timestamp() * 1000)}], "pgn"),
    ([record(pgn='[White "Learner"]\n[Black "Opponent"]\n[Result "*"]\n\n1. e4 *')], "invalid_provider_game"),
    ([record(), record("second", 1), record("third", 2)], "page_bound"),
])
def test_invalid_provider_response_fails_whole_batch_without_partial_success(records, reason):
    with pytest.raises(PublicImportError, match=reason):
        fetch_lichess_games(FakeTransport(records), **SCOPE)


@pytest.mark.parametrize("status,reason", [(429, "rate_limited"), (401, "http_401"), (302, "http_302"), (503, "http_503")])
def test_http_and_rate_limit_failures_are_reported_without_retry(status, reason):
    transport = FakeTransport(response=PublicResponse(status, b"", "60" if status == 429 else None))
    with pytest.raises(PublicImportError, match=reason) as failure:
        fetch_lichess_games(transport, **SCOPE)
    assert failure.value.retry_after == ("60" if status == 429 else None)
    assert len(transport.calls) == 1


def test_injected_transport_timeout_is_reported_without_retry():
    class TimeoutTransport:
        def get(self, *args, **kwargs):
            raise TimeoutError("synthetic timeout")
    with pytest.raises(PublicImportError, match="provider_timeout"):
        fetch_lichess_games(TimeoutTransport(), **SCOPE)


@pytest.mark.parametrize("body,reason", [(b"not json", "ndjson"), (b" " * (8 * 1024 * 1024 + 1), "byte_bound")])
def test_malformed_or_oversized_provider_body_is_rejected(body, reason):
    with pytest.raises(PublicImportError, match=reason):
        fetch_lichess_games(FakeTransport(response=PublicResponse(200, body)), **SCOPE)


def test_default_transport_sends_only_public_headers_and_enforces_read_bound(monkeypatch):
    from tempo_game_ingestion import lichess
    observations = {}
    class Response:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): observations["size"] = size; return b"12345"
    class Opener:
        def open(self, request, timeout):
            observations.update(headers=request.headers, timeout=timeout)
            return Response()
    monkeypatch.setattr(lichess, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PublicImportError, match="byte_bound"):
        UrlPublicTransport().get("https://lichess.org/api/games/user/learner", timeout=3, max_bytes=4)
    assert observations["size"] == 5
    assert observations["timeout"] == 3
    assert set(name.lower() for name in observations["headers"]) == {"accept", "user-agent"}
    assert lichess._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://elsewhere") is None


def test_public_cli_uses_injected_transport_and_reports_limit_honestly(capsys):
    arguments = ["--workspace-id", "w", "--username", "Learner", "--since", START.isoformat(),
        "--until", (START + timedelta(days=1)).isoformat(), "--limit", "1", "--game-revision", "1", "--source-revision", "1"]
    transport = FakeTransport([record()])
    assert main(arguments, transport=transport) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["limit_reached"] is True
    assert len(json.loads(output.out)["games"]) == 1
    assert main(arguments, transport=FakeTransport(response=PublicResponse(429, b"", "60"))) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Retry-After: 60" in output.err


def test_installed_public_cli_rejects_invalid_scope_before_network(tmp_path):
    from pathlib import Path
    import subprocess
    import sys
    command = Path(sys.executable).parent / "tempo-lichess-ingest"
    completed = subprocess.run([str(command), "--workspace-id", "w", "--username", "../invalid",
        "--since", START.isoformat(), "--until", (START + timedelta(days=1)).isoformat(),
        "--limit", "1", "--game-revision", "1", "--source-revision", "1"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "explicit_public_username_required" in completed.stderr


@pytest.mark.parametrize("url", ["http://lichess.org/api/games/user/learner", "https://elsewhere/api/games/user/learner",
    "https://user:password@lichess.org/api/games/user/learner", "https://lichess.org:443/api/games/user/learner",
    "https://lichess.org/other", "https://lichess.org/api/games/user/../private", "https://lichess.org/api/games/user/learner#fragment",
    "https://lichess.org/api/games/user/lear\nner"])
def test_default_transport_rejects_nonpublic_authority_or_path_before_io(monkeypatch, url):
    from tempo_game_ingestion import lichess
    monkeypatch.setattr(lichess, "build_opener", lambda *args: pytest.fail("Invalid destination reached I/O"))
    with pytest.raises(PublicImportError, match="exact_public_lichess_endpoint_required"):
        UrlPublicTransport().get(url, timeout=1, max_bytes=100)


def test_default_transport_disables_ambient_proxy_configuration(monkeypatch):
    from tempo_game_ingestion import lichess
    from urllib.request import ProxyHandler
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic-proxy.invalid:9999")
    monkeypatch.setenv("http_proxy", "http://synthetic-proxy.invalid:9999")
    observations = []
    class Response:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b""
    class Opener:
        def open(self, *args, **kwargs): return Response()
    def opener(*handlers):
        observations.extend(handlers)
        return Opener()
    monkeypatch.setattr(lichess, "build_opener", opener)
    response = UrlPublicTransport().get("https://lichess.org/api/games/user/learner", timeout=1, max_bytes=100)
    proxies = [handler for handler in observations if isinstance(handler, ProxyHandler)]
    assert len(proxies) == 1 and proxies[0].proxies == {}
    assert response.body == b""


def test_default_transport_stops_slow_chunk_stream_on_elapsed_budget_and_closes_response(monkeypatch):
    from tempo_game_ingestion import lichess
    moments = iter([0, 0, 11])
    monkeypatch.setattr(lichess, "monotonic", lambda: next(moments))
    observations = {"reads": 0, "closed": False}
    class Response:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): observations["closed"] = True
        def read(self, size):
            observations["reads"] += 1
            return b"slow chunk"
    class Opener:
        def open(self, *args, **kwargs): return Response()
    monkeypatch.setattr(lichess, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PublicImportError, match="provider_elapsed_budget_exceeded"):
        UrlPublicTransport().get("https://lichess.org/api/games/user/learner", timeout=10, max_bytes=100)
    assert observations == {"reads": 1, "closed": True}


def test_compatibility_normalizer_and_source_bound_are_public_exports():
    import tempo_game_ingestion
    assert {"normalize_pgn", "MAX_SOURCE_BYTES"} <= set(tempo_game_ingestion.__all__)
    assert tempo_game_ingestion.normalize_pgn(PGN.encode(), "Learner")["moves"] == ["e2e4", "e7e5"]
