"""Controlled HTTP/clock/admission inputs for the real packaged CLI.

Mount only this fixture and synthetic provider inputs. Production modules are
loaded from the image; no source overlay, successful finding or receipt exists.
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
import time

import httpx

inputs = json.loads(Path('/qualification/provider.json').read_text())
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        from urllib.parse import urlsplit, parse_qs
        url = urlsplit(self.path)
        if url.path.startswith('/api/games/user/'):
            query = parse_qs(url.query)
            rows = [row for row in inputs['lichess'] if int(query['since'][0]) <= row['createdAt'] < int(query['until'][0])]
            body = '\n'.join(json.dumps(row) for row in rows[:int(query['max'][0])]) + '\n'
            kind = 'application/x-ndjson'
        elif url.path.endswith('/archives'):
            body = json.dumps({'archives': ['https://api.chess.com/pub/player/learner/games/2026/09',
                                           'https://api.chess.com/pub/player/learner/games/2026/10']})
            kind = 'application/json'
        else:
            body = json.dumps({'games': inputs['chesscom'].get(url.path[-2:], [])})
            kind = 'application/json'
        self.send_response(200); self.send_header('Content-Type', kind); self.end_headers(); self.wfile.write(body.encode())

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
Thread(target=server.serve_forever, daemon=True).start()
class LocalHTTP(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        request.url = request.url.copy_with(scheme='http', host='127.0.0.1', port=server.server_port)
        return await super().handle_async_request(request)
class Clock:
    def now(self): return inputs['now']
    def utc_now(self): return datetime.fromtimestamp(self.now(), timezone.utc).isoformat()
@contextmanager
def admitted():
    yield  # Only synthetic quiet admission; deployed resource isolation unproved.

try:
    if sys.argv[1] in {'worker', 'feedback-worker'}:
        feedback = sys.argv[1] == 'feedback-worker'
        if feedback:
            # The host verifies the three actual boot identities and records the
            # public mode intent before releasing this real collector process.
            deadline = time.monotonic() + 45
            while not Path('/qualification/feedback-start').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('feedback_fixture_start_not_released')
                time.sleep(.05)
            inputs = json.loads(Path('/qualification/provider.json').read_text())
        from app.product_composition import game_sync_current as current
        original = current.current_provider_worker
        def build(**kwargs):
            return original(**kwargs, clock=Clock(), transport_factory=LocalHTTP)
        current.current_provider_worker = build
        current.current_sync_admission = admitted
        sys.argv = ['worker', *(['--feedback'] if feedback else ['--collect-only']), '--follow', '--overlap-seconds', '0',
            '--reconcile-interval-seconds', '600', '--once', '--max-units', '50']
        current.main()
    elif sys.argv[1] == 'feedback-configure':
        from app.games.feedback.review import SyncConfiguration
        from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore
        connections = DedicatedGamesConnections(os.environ['TEMPO_DATABASE_WRITE_URL'])
        configuration = SyncConfiguration.model_validate_json(json.dumps(inputs['configuration']))
        PostgresReviewStore(connections).configure(configuration, 0)
        print(json.dumps({'configured': True, 'revision': configuration.revision}))
    elif sys.argv[1] == 'feedback-capabilities':
        from app.product_composition.game_collection_status import feedback_release_capabilities
        print(json.dumps(feedback_release_capabilities()))
    elif sys.argv[1] == 'feedback-state':
        from app.product_composition.game_review import CurrentStudyReceiptReads, SystemClock
        from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore
        connections = DedicatedGamesConnections(os.environ['TEMPO_DATABASE_READ_URL'], read_only=True)
        delivery = PostgresReviewStore(connections).delivery(sys.argv[2])
        if delivery is None:
            raise RuntimeError('feedback_fixture_delivery_unavailable')
        receipt = CurrentStudyReceiptReads(connections, SystemClock()).encounter_status(delivery.command.key)
        print(json.dumps({'operation_id': delivery.operation_id, 'command': delivery.command.model_dump(mode='json'),
            'qualification': delivery.qualification.model_dump(mode='json') if delivery.qualification else None,
            'delivery_state': delivery.state, 'receipt': receipt.model_dump(mode='json') if receipt else None}))
    else:
        from app.product_composition.game_collection_status import main
        raise SystemExit(main(['--workspace', 'legacy', '--limit', '20', '--private']))
finally:
    server.shutdown(); server.server_close()
