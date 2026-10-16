"""Offline Explorer boundary regressions, independent of credential/database state."""

import asyncio
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from tempo_intelligence import provider
from tempo_intelligence.provider import ExplorerClient, ProviderFailure, retry_after_deadline


def descriptor(kind="lichess"):
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    return {"kind": kind, "canonical_fen": fen, "position_key": "fixture-position",
            "legal_moves": ["e2e4", "d2d4", "g1f3"],
            "parameters": {"fen": fen, "moves": 3, "topGames": 0}}


def payload():
    return {"white": 4, "draws": 2, "black": 4,
            "moves": [{"uci": "e2e4", "white": 3, "draws": 1, "black": 2},
                      {"uci": "d2d4", "white": 1, "draws": 1, "black": 2}]}


def fetch(response, *, request=None, inspect=None):
    def handler(outgoing):
        if inspect:
            inspect(outgoing)
        return response
    return asyncio.run(ExplorerClient(transport=httpx.MockTransport(handler)).fetch(
        request or descriptor(), "synthetic_secret",
    ))


@pytest.mark.parametrize("kind", ["lichess", "masters"])
def test_explorer_valid_native_counts_include_full_legal_universe_without_probabilities(kind):
    def inspect(request):
        assert request.url.path == "/" + kind
        assert request.headers["Authorization"] == "Bearer synthetic_secret"
        assert request.url.params["moves"] == "3"
        assert request.url.params["topGames"] == "0"
    result = fetch(httpx.Response(200, json=payload()), request=descriptor(kind), inspect=inspect)
    assert result["counts"] == {"white": 4, "draws": 2, "black": 4}
    assert result["total_games"] == result["returned_games"] == 10
    assert result["unallocated_games"] == 0
    assert result["sample_complete"]
    observations = {row["uci"]: row for row in result["legal_observations"]}
    assert observations["g1f3"] == {"uci": "g1f3", "status": "explicit_zero", "games": 0}
    assert "synthetic_secret" not in json.dumps(result)
    assert "probability" not in result
    assert len(result["output_digest"]) == 64
    assert result["provider_data_generation"] is None
    assert "provider_data_generation_not_reported" in result["limitations"]


def test_explorer_truncated_counts_preserve_unallocated_mass_and_unreported_legal_moves():
    sample = payload()
    sample["white"] += 8
    result = fetch(httpx.Response(200, json=sample))
    assert result["total_games"] == 18 and result["returned_games"] == 10
    assert result["unallocated_games"] == 8 and not result["sample_complete"]
    assert result["legal_observations"][-1] == {"uci": "g1f3", "status": "unreported", "games": None}


def test_explorer_empty_sample_remains_unknown_and_explicit_zero_is_distinct():
    result = fetch(httpx.Response(200, json={"white": 0, "draws": 0, "black": 0, "moves": []}))
    assert result["total_games"] == 0 and not result["sample_complete"]
    assert all(row["games"] is None and row["status"] == "unknown" for row in result["legal_observations"])
    zero = {"white": 0, "draws": 0, "black": 0,
            "moves": [{"uci": "e2e4", "white": 0, "draws": 0, "black": 0}]}
    result = fetch(httpx.Response(200, json=zero))
    assert next(row for row in result["legal_observations"] if row["uci"] == "e2e4")["status"] == "explicit_zero"


def test_explorer_terminal_evidence_has_no_move_distribution():
    request = descriptor()
    request["legal_moves"] = []
    request["parameters"]["moves"] = 0
    result = fetch(httpx.Response(200, json={"white": 0, "draws": 0, "black": 0, "moves": []}), request=request)
    assert result["legal_observations"] == [] and result["moves"] == []
    assert "terminal_position_has_no_move_distribution" in result["limitations"]


@pytest.mark.parametrize("mutate", [
    lambda value: value.pop("white"),
    lambda value: value.update(white=-1),
    lambda value: value.update(white=True),
    lambda value: value.update(white=4.0),
    lambda value: value.update(moves=None),
    lambda value: value["moves"][0].update(white=-1),
    lambda value: value["moves"][0].update(black=True),
    lambda value: value["moves"][0].pop("draws"),
    lambda value: value["moves"][0].update(uci="a1a8"),
    lambda value: value["moves"].append(value["moves"][0]),
    lambda value: value.update(white=0),
])
def test_explorer_invalid_counts_missing_fields_duplicates_and_illegal_moves_fail_closed(mutate):
    sample = payload()
    mutate(sample)
    with pytest.raises(ProviderFailure) as caught:
        fetch(httpx.Response(200, json=sample))
    assert caught.value.code == "invalid_response" and not caught.value.retryable


@pytest.mark.parametrize("status,code,retryable,auth_failed", [
    (401, "credential_rejected", False, True), (403, "credential_rejected", False, True),
    (500, "provider_unavailable", True, False), (503, "provider_unavailable", True, False),
    (302, "provider_request_rejected", False, False), (400, "provider_request_rejected", False, False),
])
def test_explorer_failures_are_classified_without_body_or_credential_leakage(status, code, retryable, auth_failed):
    with pytest.raises(ProviderFailure) as caught:
        fetch(httpx.Response(status, text="synthetic_secret"))
    assert caught.value.code == code and caught.value.retryable == retryable
    assert caught.value.auth_failed == auth_failed
    assert "synthetic_secret" not in str(caught.value)


@pytest.mark.parametrize("header,seconds", [("120", 120), ("0", 1), (None, 60), ("invalid", 60), ("-1", 60)])
def test_explorer_retry_after_seconds_and_fallback(header, seconds):
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert retry_after_deadline(header, now=now) == now + timedelta(seconds=seconds)


def test_explorer_retry_after_http_date_and_provider_429_deadline(monkeypatch):
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    monkeypatch.setattr(provider, "_now", lambda: now)
    expected = now + timedelta(seconds=120)
    assert retry_after_deadline("Fri, 16 Oct 2026 00:02:00 GMT", now=now) == expected
    with pytest.raises(ProviderFailure) as caught:
        fetch(httpx.Response(429, headers={"Retry-After": "120"}))
    assert caught.value.code == "rate_limited" and caught.value.retryable
    assert caught.value.retry_at == expected and not caught.value.auth_failed


@pytest.mark.parametrize("body", [b"not-json", b'{"white":0,"white":1,"draws":0,"black":0,"moves":[]}', b'[]'])
def test_explorer_invalid_json_and_duplicate_fields_are_nonretryable(body):
    with pytest.raises(ProviderFailure, match="invalid_response") as caught:
        fetch(httpx.Response(200, content=body))
    assert not caught.value.retryable


def test_explorer_response_size_is_bounded(monkeypatch):
    monkeypatch.setattr(provider, "MAX_RESPONSE_BYTES", 20)
    with pytest.raises(ProviderFailure, match="response_too_large"):
        fetch(httpx.Response(200, json=payload()))


@pytest.mark.parametrize("error", [httpx.ConnectError("synthetic_secret"), httpx.ReadError("synthetic_secret"), httpx.ReadTimeout("synthetic_secret")])
def test_explorer_network_and_timeout_errors_are_safe_retryable_failures(error):
    def handler(request):
        raise error
    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(ExplorerClient(transport=httpx.MockTransport(handler)).fetch(descriptor(), "synthetic_secret"))
    assert caught.value.retryable and not caught.value.auth_failed
    assert "synthetic_secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_explorer_total_timeout_bounds_blocked_response(monkeypatch):
    monkeypatch.setattr(provider, "TOTAL_TIMEOUT_SECONDS", 0.01)
    async def scenario():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        client = ExplorerClient(transport=httpx.MockTransport(handler))
        with pytest.raises(ProviderFailure, match="provider_timeout"):
            await client.fetch(descriptor(), "synthetic_secret")
        assert entered.is_set()
    asyncio.run(scenario())


def test_explorer_cancellation_propagates_without_waiting_for_provider():
    async def scenario():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        client = ExplorerClient(transport=httpx.MockTransport(handler))
        pending = asyncio.create_task(client.fetch(descriptor(), "synthetic_secret"))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    asyncio.run(scenario())


def test_explorer_output_digest_is_stable_for_reordered_move_listing_and_ignores_decorative_metadata():
    first = fetch(httpx.Response(200, json=payload()))
    sample = payload()
    sample["moves"].reverse()
    sample["debug"] = "synthetic_secret"
    second = fetch(httpx.Response(200, json=sample))
    assert first == second


def test_explorer_unsupported_source_and_mismatched_parameters_never_send_request():
    async def handler(request):
        pytest.fail("invalid request reached provider")
    async def scenario():
        client = ExplorerClient(transport=httpx.MockTransport(handler))
        with pytest.raises(ProviderFailure, match="unsupported_source"):
            await client.fetch(descriptor("player"), "synthetic_secret")
        request = descriptor("masters")
        request["parameters"]["ratings"] = "1600"
        with pytest.raises(ProviderFailure, match="invalid_request"):
            await client.fetch(request, "synthetic_secret")
    asyncio.run(scenario())


@pytest.mark.parametrize("parameter,value", [("ratings", "1500"), ("ratings", "400"), ("ratings", "1600,1600"), ("speeds", "blitz:0.5"), ("speeds", "unknown")])
def test_explorer_unsupported_cohorts_fail_before_network(parameter, value):
    request = descriptor()
    request["parameters"][parameter] = value
    with pytest.raises(ProviderFailure, match="unsupported_cohort"):
        fetch(httpx.Response(200, json=payload()), request=request)


def test_explorer_request_has_explicit_connection_timeout_and_never_follows_redirects():
    seen = []
    def inspect(request):
        seen.append(request.url.host)
        assert request.extensions["timeout"]["connect"] == 5
    with pytest.raises(ProviderFailure, match="provider_request_rejected"):
        fetch(httpx.Response(302, headers={"Location": "https://other.invalid/secret"}), inspect=inspect)
    assert seen == ["explorer.lichess.org"]
