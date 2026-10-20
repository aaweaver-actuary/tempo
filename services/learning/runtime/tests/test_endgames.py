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


def test_saved_endgame_attempt_replay_and_review_survive_calculation_removal(case):
    from tempo_learning_runtime import gateway
    activate(case, endgame_bundle())
    card = head(case)
    operation = uuid.uuid4().hex
    payload = {"template_id": "template-a", "fen": "8/8/8/8/8/2k5/R7/1K6 w - - 0 1",
               "target": "win", "moves": []}
    # Represents an already-recorded immutable pre-removal position. Recovery
    # keeps the canonical gateway and never needs a provider or generator.
    with case.database.scope():
        saved = gateway.dispatch("endgames.attempt.create", payload, operation)
    endpoint = "/api/endgames/templates/template-a/attempt"
    replay = case.client.post(endpoint, headers={"Idempotency-Key": operation})
    assert replay.status_code == 200 and replay.json() == saved
    assert case.client.post(endpoint).status_code == 410
    assert case.client.post("/api/endgames/probe", json={"fen": payload["fen"]}).status_code == 410
    assert case.client.post("/api/endgames/templates/other/attempt",
                            headers={"Idempotency-Key": operation}).status_code == 409
    assert case.query("SELECT COUNT(*) FROM learning.endgame_attempts")[0][0] == 1
    reviewed = submit(case, card, review_payload(card))
    assert reviewed.status_code == 200, reviewed.text
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
