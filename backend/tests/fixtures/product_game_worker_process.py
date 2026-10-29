"""Task-only worker process: actual runner/provider loop, local HTTP, no live DB."""
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx


class LocalTransport(httpx.AsyncBaseTransport):
    def __init__(self, port):
        self.port = port
        self.http = httpx.AsyncHTTPTransport(retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))

    async def handle_async_request(self, request):
        local = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
        return await self.http.handle_async_request(httpx.Request(request.method, local, headers=request.headers))

    async def aclose(self):
        await self.http.aclose()


def main():
    ROOT = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(ROOT / "backend/tests"))
    from test_product_feedback_jobs import FakeClock, MemoryStore
    from app.contracts.product_v02.games import GameWindow
    from app.product_composition.game_sync import AcquisitionPolicy, create_game_sync, sync_job
    from app.product_composition.game_worker import run_optional_worker
    port = int(sys.argv[1])
    policy = AcquisitionPolicy(provider="lichess", speeds=("rapid",), rated=True,
                               source_revision=1, timeout_seconds=15)
    other = AcquisitionPolicy(provider="chesscom", speeds=("rapid",), rated=True, source_revision=1)
    store, clock = MemoryStore(), FakeClock()
    window = GameWindow(start_at="2026-10-01T00:00:00Z", end_at="2026-11-01T00:00:00Z", precision="millisecond")
    store.enqueue(sync_job(job_id="task-worker", workspace_id="fixture", account="Learner", window=window,
                           repertoire_ids=("white",), policy=policy))
    runtime = create_game_sync(policies=(policy, other), store=store,
        snapshots=SimpleNamespace(), learning=SimpleNamespace(), clock=clock,
        transport_factory=lambda: LocalTransport(port))
    run_optional_worker(runtime, enqueue_due=lambda: None, idle_seconds=0.05)


def provider_http_fixture(directory, port):
    """Only external HTTP is fake; the packaged worker uses its actual CLI."""
    from datetime import datetime
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import select
    import socket
    from threading import Lock
    from urllib.parse import parse_qs, urlparse
    import time
    root = Path(directory)
    state = dict(lichess_requests=0, rate_limited=0, waiting=0, pages=[], months=[])
    lock = Lock()
    def save():
        temporary = root / "http-state.tmp"
        temporary.write_text(json.dumps(state))
        temporary.replace(root / "http-state.json")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            data = json.loads((root / "providers.json").read_text())
            url = urlparse(self.path)
            provider = self.headers["X-Tempo-Fixture-Provider"]
            if provider == "lichess.org":
                with lock:
                    state["lichess_requests"] += 1
                    count = state["lichess_requests"]
                    recovery = not data.get("intake_probe", False)
                    if count == 1 and recovery: state["rate_limited"] += 1
                    if count in (2, 3) and recovery: state["waiting"] = count
                    save()
                if count == 1 and recovery:
                    self.send_response(429); self.send_header("Retry-After", "1"); self.end_headers(); return
                if count in (2, 3) and recovery:
                    # The test terminates the real worker while its socket is
                    # open. Never keep a thread indefinitely after its client.
                    end = time.monotonic() + 30
                    while time.monotonic() < end:
                        readable, _, _ = select.select([self.connection], [], [], .05)
                        if readable and not self.connection.recv(1, socket.MSG_PEEK): break
                    return
                params = parse_qs(url.query)
                rows = [{"id": item["source_id"], "createdAt": int(datetime.fromisoformat(item["played_at"]).timestamp()*1000),
                         "pgn": item["pgn"], "speed": "rapid", "rated": True} for item in data["lichess"]]
                rows = sorted((row for row in rows if int(params["since"][0]) <= row["createdAt"] < int(params["until"][0])), key=lambda row: row["createdAt"])
                rows = rows[:int(params["max"][0])]
                with lock: state["pages"].append(len(rows)); save()
                body = "\n".join(json.dumps(row) for row in rows).encode()
            elif provider == "api.chess.com":
                if url.path.endswith("archives"):
                    body = json.dumps(dict(archives=["https://api.chess.com/pub/player/learner/games/2026/09", "https://api.chess.com/pub/player/learner/games/2026/10"])).encode()
                else:
                    month = url.path[-2:]
                    with lock: state["months"].append(month); save()
                    body = json.dumps(dict(games=[dict(uuid=item["source_id"], pgn=item["pgn"],
                        end_time=int(datetime.fromisoformat(item["played_at"]).timestamp()), rules="chess", time_class="rapid", rated=True,
                        url="https://www.chess.com/game/live/123456") for item in data["chesscom"] if item["played_at"][5:7] == month])).encode()
            else:
                self.send_error(400); return
            self.send_response(200); self.send_header("Content-Type", "application/x-ndjson" if provider == "lichess.org" else "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
    save()
    ThreadingHTTPServer(("0.0.0.0", int(port)), Handler).serve_forever()


if __name__ == "__main__":
    if sys.argv[1] == "--provider-http": provider_http_fixture(sys.argv[2], sys.argv[3])
    else: main()
