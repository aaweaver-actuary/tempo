"""T12 offline acquisition proofs; synthetic population, HTTP, cache and clock."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import chess
import httpx
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
for relative in ("packages/evidence-math/src", "contracts/analysis-jobs/src"):
    sys.path.insert(0, str(ROOT / relative))

from app.analysis.probability.interfaces import ExplorerClient, ExplorerRequest, ExplorerResult
from app.contracts.product_v02.analysis import PopulationSelection, ProbabilityEvidence
from tempo_evidence_math import move_universe

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
SECRET = "synthetic_credential_never_returned"


def request(**changes):
    values = dict(position=move_universe(chess.STARTING_FEN), starting_fen=chess.STARTING_FEN,
        history_uci=(), variant="standard", population=PopulationSelection(
            rating_min=1600, rating_max=1999, time_controls=("rapid",)),
        since="2026-01", until="2026-10", move_limit=20)
    return ExplorerRequest(**(values | changes))


def payload(total=100, counts=(60, 20)):
    return {"white": total, "draws": 0, "black": 0,
        "moves": [{"uci": move, "white": count, "draws": 0, "black": 0}
                  for move, count in zip(("e2e4", "d2d4"), counts)]}


def client(handler, **kwargs):
    return ExplorerClient(transport_factory=lambda: httpx.MockTransport(handler),
                          clock=lambda: NOW, **kwargs)


def fetch(body=None, *, req=None, status=200, headers=None, **kwargs):
    adapter = client(lambda _: httpx.Response(status, json=body or payload(), headers=headers), **kwargs)
    return asyncio.run(adapter.fetch(req or request(), credential=SECRET))


def probabilities(result):
    return {move.move_uci: move.probability for move in result.evidence.distribution.moves}


def test_partial_moves_preserve_unknown_mass():
    result = fetch()
    assert result.diagnostic is None
    assert result.evidence.counts.total_count == 100
    assert probabilities(result)["e2e4"] == .6
    assert probabilities(result)["d2d4"] == .2
    assert probabilities(result)["g1f3"] is None
    assert result.evidence.distribution.unknown_mass == .2
    assert result.evidence.population == request().population
    assert result.evidence.observed_at == NOW.isoformat()


def test_zero_games_is_unknown():
    result = fetch(payload(0, (0, 0)))
    assert result.evidence.counts.total_count == 0
    assert result.evidence.distribution.unknown_mass == 1
    assert set(probabilities(result).values()) == {None}


def test_missing_counts_not_invented():
    missing_total = payload()
    del missing_total["white"]
    result = fetch(missing_total)
    assert result.evidence.counts is None
    assert {move.move_uci: move.count for move in result.reported_counts} == {"e2e4":60,"d2d4":20}
    assert result.evidence.distribution.unknown_mass == 1
    assert set(probabilities(result).values()) == {None}
    missing_move = payload()
    del missing_move["moves"][0]["white"]
    result = fetch(missing_move)
    assert result.evidence.counts.total_count == 100
    assert [item.move_uci for item in result.evidence.counts.moves] == ["d2d4"]
    assert probabilities(result)["e2e4"] is None
    assert result.evidence.distribution.unknown_mass == .8


def test_population_missing_is_configuration_required():
    adapter = client(lambda _: pytest.fail("unconfigured request reached HTTP"))
    result = asyncio.run(adapter.fetch(request(population=None), credential=SECRET))
    assert result.evidence is None
    assert result.diagnostic.code == "configuration_required"
    assert not result.diagnostic.retryable


def test_cache_key_separates_rating_speed_variant():
    adapter = client(lambda _: pytest.fail("key computation reached HTTP"))
    base = request()
    alternatives = [base, request(population=PopulationSelection(
        rating_min=1400, rating_max=1599, time_controls=("rapid",))),
        request(population=PopulationSelection(rating_min=1600, rating_max=1999,
            time_controls=("blitz",))), request(variant="chess960"),
        request(since="2026-02"), request(until="2026-09"), request(move_limit=2),
        request(history_uci=("e2e4",)), request(starting_fen=chess.STARTING_FEN.replace("0 1", "1 2")),
        request(max_response_bytes=1024), request(cache_max_age_seconds=60)]
    assert len({adapter.cache_key(value) for value in alternatives}) == len(alternatives)
    assert client(lambda _: None, base_url="https://other.invalid").cache_key(base) != adapter.cache_key(base)


@pytest.mark.parametrize("mutate", [
    lambda p: p["moves"][0].update(uci="a1a8"),
    lambda p: p["moves"].append(p["moves"][0]),
    lambda p: p.update(white=-1), lambda p: p.update(white=True),
    lambda p: p.update(white=100.0), lambda p: p.update(white=2**53),
    lambda p: p["moves"][0].update(draws=-1),
    lambda p: p["moves"][0].update(white=101),
    lambda p: p.update(white=0, draws=100),
    lambda p: p.update(source="masters"),
    lambda p: p.update(fen=chess.STARTING_FEN.replace(" w ", " b ")),
    lambda p: p.update(moves=None),
])
def test_illegal_move_and_invalid_counts_rejected(mutate):
    body = payload()
    mutate(body)
    result = fetch(body)
    assert result.diagnostic.code == "invalid_response"
    assert not result.diagnostic.retryable
    assert result.evidence.distribution is None


@pytest.mark.parametrize("status,code,retryable", [(401,"credential_rejected",False),
    (403,"credential_rejected",False), (429,"rate_limited",True),
    (503,"provider_unavailable",True), (400,"provider_request_rejected",False),
    (302,"provider_request_rejected",False)])
def test_429_auth_timeout_and_cancel_are_honest(status, code, retryable):
    result = fetch(status=status, headers={"Retry-After": "120", "Location": "https://other.invalid"})
    assert result.diagnostic.code == code
    assert result.diagnostic.retryable is retryable
    assert result.evidence.distribution is None
    assert result.evidence.unavailable_reason == code
    if status == 429:
        assert result.diagnostic.retry_at == (NOW + timedelta(seconds=120)).isoformat()
    if status in {401,403}:
        assert result.diagnostic.auth_failed


def test_timeout_and_injected_or_task_cancellation_close_http_before_return():
    async def scenario(mode):
        entered, closed, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def handler(_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        adapter = client(handler)
        req = request(timeout_seconds=.03, connect_timeout_seconds=.01)
        pending = asyncio.create_task(adapter.fetch(req, credential=SECRET, cancellation=cancel))
        await entered.wait()
        if mode == "injected": cancel.set()
        if mode == "task": pending.cancel()
        result = await pending
        assert closed.is_set()
        assert result.diagnostic.code == ("provider_timeout" if mode == "timeout" else "cancelled")
        assert result.diagnostic.retryable and result.evidence.distribution is None
    for mode in ("timeout", "injected", "task"):
        asyncio.run(scenario(mode))


def test_no_credentials_in_output():
    for status in (200,401,429,503):
        body = payload() | {"debug": SECRET}
        result = fetch(body, status=status)
        assert SECRET not in result.model_dump_json() + repr(result)
    for error in (httpx.ConnectError(SECRET), httpx.ReadTimeout(SECRET)):
        def handler(_): raise error
        result = asyncio.run(client(handler).fetch(request(), credential=SECRET))
        assert result.diagnostic.retryable
        assert SECRET not in result.model_dump_json() + repr(result)


def test_provider_offline_never_calls_study():
    # Extend T01's actual scoped guard with this module, without editing it or
    # repeating its owning tests. Reuse relative import resolution/cycle proof.
    from test_product_contracts import boundary_errors, has_import_cycle, import_targets
    folder = ROOT / "backend/app/analysis/probability"
    modules = {"app.analysis.probability." + ".".join(path.relative_to(folder).with_suffix("").parts): path.read_text()
               for path in folder.rglob("*.py")}
    assert not boundary_errors([("analysis", source, name) for name, source in modules.items()])
    assert not has_import_cycle(modules)
    for name, source in modules.items():
        targets = tuple(import_targets(source, name))
        assert not any(target.startswith(("tempo_intelligence", "psycopg", "sqlite3", "app.services"))
                       for target in targets)
    result = fetch(status=503)
    assert result.diagnostic.code == "provider_unavailable"


def test_public_interface_preserves_types_and_cold_import_starts_no_work():
    from app.analysis.probability import interfaces
    from app.analysis.probability.explorer import ExplorerClient as ExistingClient
    from app.analysis.probability.ports import ExplorerRequest as ExistingRequest, ExplorerResult as ExistingResult
    assert interfaces.ExplorerClient is ExistingClient
    assert interfaces.ExplorerRequest is ExistingRequest
    assert interfaces.ExplorerResult is ExistingResult
    assert set(interfaces.__all__) == {
        "Cancellation", "DiagnosticCode", "ExplorerCache", "ExplorerClient", "ExplorerDiagnostic",
        "ExplorerProbabilityProvider", "ExplorerRequest", "ExplorerResult",
    }
    script = '''
import builtins, socket
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.startswith(("app.study", "app.games", "app.repertoire", "app.main", "app.tasks", "tempo_intelligence")):
        raise AssertionError("optional boundary imported private runtime: " + name)
    return original(name, *args, **kwargs)
def denied(*args, **kwargs):
    raise AssertionError("public interface initiated network work")
socket.socket.connect = denied
socket.create_connection = denied
builtins.__import__ = checked
from app.analysis.probability.interfaces import ExplorerClient
client = ExplorerClient(transport_factory=denied)
'''
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (
        ROOT / "backend", ROOT / "packages/evidence-math/src", ROOT / "contracts/analysis-jobs/src"))))
    result = subprocess.run([sys.executable,"-c",script],cwd=ROOT,env=environment,
                            capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr


def test_complete_distribution_round_trip():
    result = fetch(payload(80))
    assert result.evidence.distribution.unknown_mass == 0
    assert probabilities(result)["e2e4"] == .75
    assert probabilities(result)["d2d4"] == .25
    assert probabilities(result)["g1f3"] == 0
    assert ExplorerResult.model_validate_json(result.model_dump_json()) == result
    assert ProbabilityEvidence.model_validate_json(result.evidence.model_dump_json()) == result.evidence


def test_reordered_moves_and_decorative_metadata_preserve_evidence_identity():
    original = payload()
    reordered = payload()
    reordered["moves"].reverse()
    reordered["decorative"] = SECRET
    assert fetch(original) == fetch(reordered)


def test_exact_history_and_all_explicit_filters_reach_http_once():
    calls = []
    board = chess.Board()
    board.push_uci("e2e4")
    req = request(position=move_universe(board.fen()), history_uci=("e2e4",), move_limit=2)
    def handler(outgoing):
        calls.append(outgoing)
        assert outgoing.url.path == "/lichess"
        assert outgoing.url.params["fen"] == chess.STARTING_FEN
        assert outgoing.url.params["play"] == "e2e4"
        assert outgoing.url.params["ratings"] == "1600,1800"
        assert outgoing.url.params["speeds"] == "rapid"
        assert outgoing.url.params["since"] == "2026-01"
        assert outgoing.url.params["until"] == "2026-10"
        assert outgoing.url.params["moves"] == "2"
        assert outgoing.url.params["topGames"] == outgoing.url.params["recentGames"] == "0"
        assert outgoing.url.params["history"] == "false"
        assert outgoing.headers["Authorization"] == f"Bearer {SECRET}"
        assert outgoing.extensions["timeout"]["connect"] == 5
        return httpx.Response(200, json={"white":1,"draws":0,"black":0,
            "moves":[{"uci":"e7e5","white":1,"draws":0,"black":0}]})
    result = asyncio.run(client(handler).fetch(req, credential=SECRET))
    assert result.diagnostic is None and len(calls) == 1
    assert result.evidence.position == req.position


@pytest.mark.parametrize("changes,code", [({"history_uci":("e2e4",)},"invalid_request"),
    ({"history_uci":("e2e5",)},"invalid_request"), ({"variant":"chess960"},"unsupported_variant"),
    ({"population":PopulationSelection(rating_min=1500,rating_max=1700,
        time_controls=("rapid",))},"unsupported_population"),
    ({"population":PopulationSelection(rating_min=2500,rating_max=3000,
        time_controls=("rapid",))},"unsupported_population")])
def test_unrepresentable_context_is_never_silently_widened(changes,code):
    adapter = client(lambda _: pytest.fail("unsupported context reached HTTP"))
    result = asyncio.run(adapter.fetch(request(**changes), credential=SECRET))
    assert result.diagnostic.code == code


@pytest.mark.parametrize("credential", [None,"","has whitespace","newline\nsecret"])
def test_missing_or_malformed_credential_is_visible_without_http(credential):
    adapter = client(lambda _: pytest.fail("missing credential reached HTTP"))
    result = asyncio.run(adapter.fetch(request(), credential=credential))
    assert result.diagnostic.code == "credential_missing" and result.diagnostic.auth_failed


class Cache:
    def __init__(self):
        self.values, self.events = {}, []
        self.in_operation = False
    async def get(self,key):
        self.in_operation = True
        try:
            self.events.append("get")
            return self.values.get(key)
        finally:
            self.in_operation = False
    async def put(self,key,value):
        self.in_operation = True
        try:
            self.events.append("put")
            assert SECRET not in value.model_dump_json() + key
            self.values[key] = value
        finally:
            self.in_operation = False


def test_cache_releases_capability_before_http_and_retains_observation_time():
    cache, calls = Cache(), []
    def handler(_):
        assert not cache.in_operation
        calls.append("HTTP")
        return httpx.Response(200,json=payload())
    adapter = client(handler,cache=cache)
    req = request(cache_max_age_seconds=60)
    first = asyncio.run(adapter.fetch(req,credential=SECRET))
    adapter.clock = lambda: NOW + timedelta(seconds=30)
    second = asyncio.run(adapter.fetch(req,credential=SECRET))
    assert not first.from_cache and second.from_cache
    assert first.evidence == second.evidence
    assert calls == ["HTTP"] and cache.events == ["get","put","get"]
    adapter.clock = lambda: NOW + timedelta(seconds=61)
    third = asyncio.run(adapter.fetch(req,credential=SECRET))
    assert not third.from_cache and len(calls) == 2
    assert third.evidence.observed_at != first.evidence.observed_at


def test_cache_wrong_context_is_rejected_and_failure_is_never_cached():
    cache = Cache()
    req = request(cache_max_age_seconds=60)
    wrong = fetch(req=request(population=PopulationSelection(rating_min=1400,rating_max=1599,
        time_controls=("rapid",)),cache_max_age_seconds=60))
    adapter = client(lambda _: pytest.fail("corrupt cache reached HTTP"),cache=cache)
    cache.values[adapter.cache_key(req)] = wrong
    result = asyncio.run(adapter.fetch(req,credential=SECRET))
    assert result.diagnostic.code == "invalid_cache_entry" and result.evidence.distribution is None
    cache.values.clear()
    result = fetch(req=req,status=503,cache=cache)
    assert not cache.values and result.diagnostic.code == "provider_unavailable"


def test_cache_failure_and_cache_cancellation_are_visible_and_release_work():
    class FailingCache(Cache):
        async def get(self,key): raise RuntimeError(SECRET)
    result = fetch(req=request(cache_max_age_seconds=60),cache=FailingCache())
    assert result.diagnostic.code == "cache_unavailable" and result.diagnostic.retryable
    assert SECRET not in result.model_dump_json()

    async def scenario():
        entered, released, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class BlockedCache(Cache):
            async def get(self,key):
                entered.set()
                try: await asyncio.Event().wait()
                finally: released.set()
        adapter = client(lambda _: pytest.fail("cache cancellation reached HTTP"),cache=BlockedCache())
        pending = asyncio.create_task(adapter.fetch(request(cache_max_age_seconds=60),
            credential=SECRET,cancellation=cancel))
        await entered.wait()
        cancel.set()
        result = await pending
        assert released.is_set() and result.diagnostic.code == "cancelled"
    asyncio.run(scenario())


@pytest.mark.parametrize("body", [b"not-json", b'{"white":0,"white":1,"moves":[]}',
    b'{"white":NaN,"moves":[]}',b'[]'])
def test_malformed_payload_and_duplicate_fields_are_honest(body):
    adapter = client(lambda _: httpx.Response(200,content=body))
    result = asyncio.run(adapter.fetch(request(),credential=SECRET))
    assert result.diagnostic.code == "invalid_response"


def test_size_result_and_request_bounds():
    assert fetch(req=request(max_response_bytes=20)).diagnostic.code == "response_too_large"
    assert fetch(req=request(move_limit=1)).diagnostic.code == "result_limit_exceeded"
    for changes in ({"move_limit":0},{"max_requests":2},{"timeout_seconds":float("inf")},
                    {"max_response_bytes":2**30},{"history_uci":("e2e4",)*513}):
        with pytest.raises(ValidationError): request(**changes)


def test_pre_cancel_does_not_touch_http_or_cache():
    async def scenario():
        cancel = asyncio.Event()
        cancel.set()
        cache = Cache()
        result = await client(lambda _: pytest.fail("cancelled reached HTTP"),cache=cache).fetch(
            request(cache_max_age_seconds=60),credential=SECRET,cancellation=cancel)
        assert result.diagnostic.code == "cancelled" and not cache.events
    asyncio.run(scenario())
