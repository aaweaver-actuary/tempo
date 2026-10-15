"""Separate offline cache and online-provider protocol proofs."""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import hashlib, json, uuid
import chess
import pytest
from urllib.parse import parse_qs, urlparse
from fastapi.testclient import TestClient
from tempo_learning_runtime import create_app
from tempo_learning_runtime.endgames import generate_position
from test_runtime import activate, bundle, head, review_payload, submit


def endgame_bundle():
    payload = bundle(
        [
            (
                "endgame",
                {
                    "type": "move_line",
                    "accepted_lines": [["e2e4"]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
        ]
    )
    payload["facts"][0].update(
        source_ref="template-a",
        endgame={
            "name": "Rook ending",
            "white_material": "KR",
            "black_material": "K",
            "goal_mix": "both",
        },
    )
    return payload


def test_offline_endgame_cache_attempt_replay_and_review(case):
    activate(case, endgame_bundle())
    card = head(case)
    operation = uuid.uuid4().hex
    seed = int.from_bytes(hashlib.sha256(f"{operation}:0".encode()).digest()[:8], "big")
    fen = generate_position("KR", "K", "white", seed=seed)
    key = "tb-fen-v2:" + chess.Board(fen).fen()
    data = {"category": "win", "moves": []}
    case.query(
        "INSERT INTO learning.tablebase_cache VALUES(%s,%s,NOW()::text)",
        (key, json.dumps(data)),
    )
    probe = case.client.post("/api/endgames/probe", json={"fen": fen})
    assert probe.status_code == 200 and probe.json() == data
    endpoint = "/api/endgames/templates/template-a/attempt"
    saved = case.client.post(endpoint, headers={"Idempotency-Key": operation})
    assert saved.status_code == 200, saved.text
    assert saved.json()["fen"] == fen
    case.query("DELETE FROM learning.tablebase_cache")
    assert (
        case.client.post(endpoint, headers={"Idempotency-Key": operation}).json()
        == saved.json()
    )
    assert (
        case.client.post(
            "/api/endgames/templates/other/attempt",
            headers={"Idempotency-Key": operation},
        ).status_code
        == 409
    )
    assert case.query("SELECT COUNT(*) FROM learning.endgame_attempts")[0][0] == 1
    assert case.client.post("/api/endgames/probe", json={"fen": fen}).status_code == 503
    reviewed = submit(case, card, review_payload(card))
    assert reviewed.status_code == 200, reviewed.text
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


def test_configured_tablebase_provider_success_cache_and_explicit_failure(case):
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if len(requests) == 1:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"category":"draw","moves":[]}')
            else:
                self.send_response(503)
                self.end_headers()

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = Thread(target=provider.serve_forever, daemon=True)
    worker.start()
    configuration = replace(
        case.configuration,
        tablebase_url=f"http://127.0.0.1:{provider.server_port}/standard",
    )
    try:
        with TestClient(
            create_app(configuration), headers=dict(case.client.headers)
        ) as client:
            fen = "8/8/8/8/8/2k5/8/K7 w - - 0 1"
            assert (
                client.post("/api/endgames/probe", json={"fen": fen}).status_code == 200
            )
            assert (
                client.post("/api/endgames/probe", json={"fen": fen}).status_code == 200
            )
            assert len(requests) == 1
            failure = client.post(
                "/api/endgames/probe", json={"fen": "8/8/8/8/8/2k5/8/1K6 w - - 0 1"}
            )
            assert (
                failure.status_code == 503
                and "Optional tablebase unavailable" in failure.text
            )
    finally:
        provider.shutdown()
        provider.server_close()
        worker.join(timeout=3)


@pytest.mark.parametrize("clocks", [(0, 99), (99, 0)])
def test_tablebase_cache_keeps_halfmove_clocks_separate_and_ignores_old_keys(
    case, clocks
):
    requests = []
    fens = {clock: f"8/8/8/8/8/2k5/7R/K7 w - - {clock} 1" for clock in clocks}
    responses = {
        0: {"category": "win", "moves": []},
        99: {"category": "draw", "moves": []},
    }

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            fen = parse_qs(urlparse(self.path).query)["fen"][0]
            requests.append(fen)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(responses[int(fen.split()[4])]).encode())

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = Thread(target=provider.serve_forever, daemon=True)
    worker.start()
    old_key = " ".join(chess.Board(fens[clocks[0]]).fen().split()[:4])
    case.query(
        "INSERT INTO learning.tablebase_cache VALUES(%s,%s,NOW()::text)",
        (old_key, json.dumps({"category": "loss", "moves": []})),
    )
    configuration = replace(
        case.configuration,
        tablebase_url=f"http://127.0.0.1:{provider.server_port}/standard",
    )
    try:
        with TestClient(
            create_app(configuration), headers=dict(case.client.headers)
        ) as client:
            for clock in clocks:
                response = client.post("/api/endgames/probe", json={"fen": fens[clock]})
                assert (
                    response.status_code == 200 and response.json() == responses[clock]
                )
            assert requests == [fens[clock] for clock in clocks]
            for clock in reversed(clocks):
                assert (
                    client.post("/api/endgames/probe", json={"fen": fens[clock]}).json()
                    == responses[clock]
                )
            assert len(requests) == 2
        for clock in clocks:
            assert (
                case.client.post(
                    "/api/endgames/probe", json={"fen": fens[clock]}
                ).json()
                == responses[clock]
            )
        keys = {
            row[0] for row in case.query("SELECT fen_key FROM learning.tablebase_cache")
        }
        assert keys == {
            old_key,
            *("tb-fen-v2:" + chess.Board(fen).fen() for fen in fens.values()),
        }
        uncached = fens[clocks[0]].replace(f" {clocks[0]} 1", " 50 1")
        assert (
            case.client.post("/api/endgames/probe", json={"fen": uncached}).status_code
            == 503
        )
        assert len(requests) == 2
    finally:
        provider.shutdown()
        provider.server_close()
        worker.join(timeout=3)
