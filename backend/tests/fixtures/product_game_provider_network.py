"""External-network substitution for the packaged optional-worker proof only.

Mounted as sitecustomize.py outside /app. All application, durable store,
command-worker and Study code remains the unmodified package implementation.
"""
import os

if os.environ.get("TEMPO_FIXTURE_PROVIDER_PORT"):
    from urllib.parse import urlparse
    import httpx

    assert os.environ.get("TEMPO_TEST_INSTANCE") == "disposable"
    dsn = urlparse(os.environ["TEMPO_DATABASE_WRITE_URL"])
    assert (dsn.hostname, dsn.path, dsn.username) == ("postgres", "/tempo", "tempo_writer")
    port = int(os.environ["TEMPO_FIXTURE_PROVIDER_PORT"])
    assert 1024 <= port <= 65535
    original = httpx.AsyncHTTPTransport.handle_async_request

    async def local_provider(self, request):
        provider = request.url.host
        if provider not in {"lichess.org", "api.chess.com"}:
            raise RuntimeError("Packaged fixture refuses non-provider external traffic")
        request.headers["X-Tempo-Fixture-Provider"] = provider
        request.url = request.url.copy_with(scheme="http", host="host.docker.internal", port=port)
        return await original(self, request)

    httpx.AsyncHTTPTransport.handle_async_request = local_provider
