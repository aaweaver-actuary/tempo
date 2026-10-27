"""Execute the actual root admission function without importing the live app.

Only its admission capabilities are supplied. No host settings, DB, Redis,
worker, lifespan or provider is reached by this focused routing boundary test.
"""
import ast
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
import asyncio
import logging
import time

from fastapi.responses import JSONResponse
import pytest
from redis.exceptions import RedisError
from starlette.requests import Request


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/product/game-review/games"),
    ("GET", "/api/product/game-review/sync/preview/lichess"),
    ("POST", "/api/product/game-review/sync"),
    ("POST", "/api/product/game-review/configuration"),
    ("POST", "/api/product/game-review/feedback/retry"),
])
def test_game_review_root_commands_and_reads_use_background_admission(method, path):
    events = []
    @contextmanager
    def background():
        events.append("background")
        yield
    @asynccontextmanager
    async def foreground():
        pytest.fail("optional Game Review took foreground admission")
        yield
    @contextmanager
    def query_only():
        events.append("read_only")
        yield
    root = Path(__file__).resolve().parents[1] / "app/main.py"
    module = ast.parse(root.read_text())
    handler = next(node for node in module.body if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == "prioritize_foreground_requests")
    handler.decorator_list = []
    namespace = dict(Request=Request, time=time, logging=logging, RedisError=RedisError,
        JSONResponse=JSONResponse, postgres_store=SimpleNamespace(configured=lambda: True),
        activity_gate=SimpleNamespace(background_request=background, async_foreground=foreground),
        query_only_request=query_only)
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(root), "exec"), namespace)
    async def downstream(request):
        events.append("dispatch")
        return JSONResponse({"passed": True})
    request = Request(dict(type="http", method=method, path=path, headers=[], query_string=b""))
    result = asyncio.run(namespace[handler.name](request, downstream))
    assert result.status_code == 200
    assert events == (["read_only"] if method == "GET" else []) + ["background", "dispatch"]
