"""The retired feature has no serving, command, configuration or provider path."""
from contextlib import nullcontext
from types import SimpleNamespace
import asyncio
import httpx
from tempo_learning_runtime import api
from tempo_learning_runtime.config import Configuration


def test_retired_endgames_routes_do_not_open_database_or_provider(monkeypatch):
    database = SimpleNamespace(scope=lambda: nullcontext(), transaction=lambda **_: (_ for _ in ()).throw(AssertionError("Retired route opened a database")))
    monkeypatch.setattr(api, "Database", lambda _: database)
    monkeypatch.setattr(api, "HostActivity", lambda *args, **kwargs: SimpleNamespace(request=lambda: nullcontext()))
    app = api.create_app(Configuration("retirement", "unused", "unused", "x" * 40, maintenance=False))
    assert not any(route.path.startswith("/api/endgames/") for route in app.routes)
    assert not any(name.startswith("endgames.") for name in api.gateway._handlers)
    assert "tablebase_url" not in Configuration.__dataclass_fields__
    async def probe():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver", headers={"Authorization": "Bearer " + "x" * 40}) as client:
            for method, path in [("GET", "/api/endgames/templates"), ("POST", "/api/endgames/probe"), ("POST", "/api/endgames/templates/old/attempt")]:
                response = await client.request(method, path, json={})
                assert response.status_code == 404, response.text
    asyncio.run(probe())
