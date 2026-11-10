"""Synthetic authoring, provenance, quota, and compatibility regressions."""

from contextlib import nullcontext
import uuid

import chess
from fastapi.testclient import TestClient

from app.contracts.transport.tactics import TacticCaptureRequest
from app.services.tactic_capture import validate_tactic_line


def authored_capture(move="e2e4", **overrides):
    return TacticCaptureRequest(
        capture_id=uuid.uuid4(),
        starting_fen=chess.STARTING_FEN,
        moves=[move],
        **overrides,
    )


def test_capture_accepts_black_castling_en_passant_and_underpromotion():
    cases = [
        ("4k3/8/8/8/8/8/8/4K3 b - - 0 1", ["e8d7"], "black"),
        ("4k3/8/8/8/8/8/8/4K2R w K - 0 1", ["e1g1"], "white"),
        ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2", ["e5d6"], "white"),
        ("7k/P7/8/8/8/8/8/4K3 w - - 0 1", ["a7a8n"], "white"),
    ]
    for fen, moves, color in cases:
        normalized_fen, normalized_moves, trained_color = validate_tactic_line(
            fen, moves
        )
        assert (
            normalized_fen == fen
            and normalized_moves == moves
            and trained_color == color
        )


def test_capture_api_uses_foreground_receipt_and_rejects_mismatched_key(monkeypatch):
    from app import main, command_dispatch
    from fastapi.responses import JSONResponse

    observed = []

    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(
        command_dispatch,
        "dispatch_command",
        lambda name, payload, *, idempotency_key: (
            observed.append((name, payload, idempotency_key))
            or JSONResponse({"operation_id": idempotency_key}, status_code=202)
        ),
    )
    body = authored_capture().model_dump(mode="json")
    client = TestClient(main.app)
    assert client.post("/api/tactics/captures", json=body).status_code == 202
    assert observed == [
        ("tactics.capture.create", {"request": body}, body["capture_id"])
    ]
    assert (
        client.post(
            "/api/tactics/captures", json=body, headers={"Idempotency-Key": "different"}
        ).status_code
        == 422
    )
    assert len(observed) == 1
