"""Golden hashes recorded from the gateway before B1 identity extraction."""

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.contracts.command_identity import request_digest

CASES = [
    (
        "opening_evidence.checkpoint",
        {"checkpoint": {"attempt_id": "a"}, "prepared_manifest": {"x": 1}},
        "7190a9e1fb1c8f43c75743d68c7d49aafa285cb28067f6f0a4a55351ed66b603",
    ),
    (
        "cards.review",
        {"card_id": "a", "review": {"rating": 2}, "prepared_manifest": None},
        "a9336300d58ecac00326b0fb679413c3d727f721b27936d21ce7c9b84b5c4d43",
    ),
    (
        "cards.review.reconcile",
        {"review": {"revision": "2"}, "prepared_manifest": []},
        "ab8cf97d766eaca6f55f0e59a85f1afaece6415eb37f5b39832fc3db9f3f2e31",
    ),
    (
        "analysis.paste.commit",
        {"request": {"pgn": "é4", "force": False}, "preview": {"x": 1}},
        "cdcdd4cd487c5755532fb27fe2a29caf6c40e037e4f5b636502e640e4ad29ff1",
    ),
    (
        "discovery.accept",
        {
            "opportunity_id": "o",
            "selected_move_uci": "e2e4",
            "evidence_fingerprint": None,
            "extra": 1,
        },
        "b21b12880d09ec541c5c9ed06c8b445d8d311f8acf793c20bbac8a030f669bd0",
    ),
    (
        "imports.pgn.explicit",
        {"n": 1, "null": None, "s": "1"},
        "d31c414a9b903b459d939d43e779db5152a9a33e75a6373680e37cec85f857e4",
    ),
]


@pytest.mark.parametrize("name,payload,expected", CASES)
def test_legacy_command_digest_bytes_are_unchanged(name, payload, expected):
    from app.command_gateway import request_digest as gateway_digest

    original = copy.deepcopy(payload)
    assert request_digest(name, payload) == gateway_digest(name, payload) == expected
    assert payload == original


@pytest.mark.parametrize(
    "name", ["opening_evidence.checkpoint", "cards.review", "cards.review.reconcile"]
)
def test_prepared_data_cannot_change_operation_identity(name):
    payload = {"request": {"revision": "1"}}
    assert request_digest(name, payload) == request_digest(
        name, {**payload, "prepared_manifest": {"changed": True}}
    )
    assert request_digest(name, payload) != request_digest(
        name, {"request": {"revision": 1}}
    )
    assert request_digest(name, payload) != request_digest(
        name, {**payload, "extra": None}
    )


def test_projection_errors_preserve_missing_keys_and_null():
    with pytest.raises(KeyError, match="request"):
        request_digest("analysis.paste.commit", {})
    with pytest.raises(KeyError, match="selected_move_uci"):
        request_digest("discovery.accept", {"opportunity_id": "o"})
    assert request_digest("analysis.paste.commit", {"request": None}) != request_digest(
        "analysis.paste.commit", {"request": {}}
    )


def test_gateway_adapter_delegates_to_pure_identity(monkeypatch):
    from app import command_gateway

    monkeypatch.setattr(
        command_gateway, "_identity_digest", lambda name, payload: "delegated"
    )
    assert command_gateway.request_digest("example", {}) == "delegated"


def test_pure_codec_import_excludes_runtime_providers_and_storage():
    root = Path(__file__).resolve().parents[2]
    code = """\nimport builtins\noriginal = builtins.__import__\ndef guarded(name, *args, **kwargs):\n    if name == "app.main" or name == "app.tasks" or name.startswith(("tempo_storage", "psycopg", "celery", "redis")):\n        raise AssertionError("forbidden import: " + name)\n    return original(name, *args, **kwargs)\nbuiltins.__import__ = guarded\nfrom app.contracts.command_identity import request_digest\nassert len(request_digest("cards.review", {"card_id": "a"})) == 64\n"""
    env = {**os.environ, "PYTHONPATH": str(root / "backend")}
    subprocess.run([sys.executable, "-c", code], cwd=root, env=env, check=True)


def test_api_dispatch_uses_the_same_pure_codec():
    from app import command_dispatch

    assert command_dispatch.request_digest is request_digest
    assert command_dispatch.request_digest(*CASES[1][:2]) == CASES[1][2]


def test_prepared_paste_and_discovery_changes_do_not_change_identity():
    name, paste, _ = CASES[3]
    assert request_digest(name, paste) == request_digest(
        name, {**paste, "preview": {"new": 1}}
    )
    name, choice, _ = CASES[4]
    assert request_digest(name, choice) == request_digest(
        name, {**choice, "extra": {"new": 1}}
    )
    assert request_digest(name, choice) != request_digest(
        name, {**choice, "selected_move_uci": "d2d4"}
    )
