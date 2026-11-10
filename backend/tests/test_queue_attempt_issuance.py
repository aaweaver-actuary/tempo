"""PR142: admission identity must not authorize an unissued later attempt."""


from tempo_storage import connections as database
from app import main


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_queue_issuance_is_only_queue_get_write_exception():
    import asyncio
    from starlette.requests import Request
    from starlette.responses import Response

    async def probe():
        observed = {}
        for path in (
            "/api/queue/today",
            "/api/queue/prepared",
            "/api/queue/window",
            "/api/queue/entries/1/state",
            "/api/settings",
        ):
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": path,
                    "headers": [],
                    "query_string": b"",
                    "scheme": "http",
                    "server": ("test", 80),
                }
            )

            async def downstream(_request):
                observed[path] = database._query_only_request.get()
                return Response(status_code=204)

            await main.prioritize_foreground_requests(request, downstream)
        return observed

    assert asyncio.run(probe()) == {
        "/api/queue/today": False,
        "/api/queue/prepared": False,
        "/api/queue/window": False,
        "/api/queue/entries/1/state": True,
        "/api/settings": True,
    }
