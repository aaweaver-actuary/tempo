"""Standalone acquisition adapted from tempo_intelligence/provider.py.

Reuses its bounded streaming, native outcome validation and safe error scheme;
imports no Intelligence lifecycle, store, registry or publication machinery.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import re

import chess
import httpx
from pydantic import ValidationError
from tempo_evidence_math import (CountEvidence, MoveCount, MoveProbability,
                                 MoveProbabilityDistribution, empirical_frequencies,
                                 move_universe, unknown_distribution)

from app.contracts.product_v02.analysis import ProbabilityEvidence
from .ports import (Cancellation, ExplorerCache, ExplorerDiagnostic,
                    ExplorerRequest, ExplorerResult)

ADAPTER_VERSION = "explorer-v1"
# Exact finite bucket ranges only. Do not silently widen arbitrary user bands.
# Lichess groups average player ratings, not the next opponent's exact rating.
RATING_BOUNDS = (0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500, 2800)
SPEEDS = frozenset(("ultraBullet", "bullet", "blitz", "rapid", "classical", "correspondence"))
MAX_COUNT = 2**53 - 1


class _Failure(Exception):
    def __init__(self, code, *, retryable=False, retry_at=None, auth_failed=False):
        self.diagnostic = ExplorerDiagnostic(code=code, retryable=retryable,
                                             retry_at=retry_at, auth_failed=auth_failed)
        super().__init__(code)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _outcomes(value) -> tuple[int | None, int | None, int | None]:
    if not isinstance(value, dict):
        raise _Failure("invalid_response")
    values = tuple(value.get(field) for field in ("white", "draws", "black"))
    for count in values:
        if count is not None and (type(count) is not int or not 0 <= count <= MAX_COUNT):
            raise _Failure("invalid_response")
    if sum(count for count in values if count is not None) > MAX_COUNT:
        raise _Failure("invalid_response")
    return values


def _distribution(position, counts):
    if counts is None or not counts.total_count:
        return unknown_distribution(position)
    frequencies = empirical_frequencies(counts)
    unknown_mass = (counts.total_count - sum(move.count for move in counts.moves)) / counts.total_count
    return MoveProbabilityDistribution(position=position, unknown_mass=unknown_mass,
        moves=tuple(MoveProbability(move_uci=move,
                    probability=frequencies.get(move, None if unknown_mass else 0.0))
                    for move in position.legal_moves))


def _validate_payload(payload, request):
    if not isinstance(payload, dict) or not isinstance(payload.get("moves"), list):
        raise _Failure("invalid_response")
    if len(payload["moves"]) > request.move_limit:
        raise _Failure("result_limit_exceeded")
    if "source" in payload and payload["source"] != "lichess":
        raise _Failure("invalid_response")
    if "fen" in payload:
        try:
            if move_universe(payload["fen"]) != request.position:
                raise ValueError("position mismatch")
        except (ValueError, TypeError, IndexError, AttributeError):
            raise _Failure("invalid_response") from None
    root = _outcomes(payload)
    outcomes = []
    counts = []
    seen = set()
    for item in payload["moves"]:
        native_counts = _outcomes(item)
        move = item.get("uci")
        if not isinstance(move, str) or move not in request.position.legal_moves or move in seen:
            raise _Failure("invalid_response")
        seen.add(move)
        outcomes.append(native_counts)
        if all(count is not None for count in native_counts):
            counts.append(MoveCount(move_uci=move, count=sum(native_counts)))
    if any(root[index] is not None and sum(row[index] for row in outcomes
            if row[index] is not None) > root[index] for index in range(3)):
        raise _Failure("invalid_response")
    if any(count is None for count in root):
        # Do not replace a missing denominator with the returned subtotal.
        return None, unknown_distribution(request.position), tuple(counts)
    evidence_counts = CountEvidence(kind="counts", total_count=sum(root), moves=tuple(counts))
    return evidence_counts, _distribution(request.position, evidence_counts), tuple(counts)


class ExplorerClient:
    """One request per fetch; no automatic retry, queue, DB or default transport."""
    def __init__(self, *, transport_factory: Callable[[], httpx.AsyncBaseTransport],
                 cache: ExplorerCache | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 base_url: str = "https://explorer.lichess.org"):
        parsed = httpx.URL(base_url)
        if (parsed.scheme != "https" or not parsed.host or parsed.userinfo or parsed.query
                or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("invalid_provider_endpoint")
        self.source = str(parsed).rstrip("/")
        self.transport_factory = transport_factory
        self.cache = cache
        self.clock = clock

    def cache_key(self, request: ExplorerRequest) -> str:
        return _digest({"source": self.source, "adapter_version": ADAPTER_VERSION,
                        "request": request.model_dump(mode="json")})

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock_requires_timezone")
        return now.astimezone(timezone.utc)

    def _result(self, request, *, evidence=None, diagnostic=None, reported_counts=()):
        key = self.cache_key(request)
        if diagnostic is not None and request.population is not None:
            evidence = ProbabilityEvidence(evidence_id=key, position=request.position,
                provider="lichess-explorer", model=None, basis="population", population=request.population,
                personal_account=None, observed_at=None, distribution=None, counts=None,
                unavailable_reason=diagnostic.code)
        return ExplorerResult(request_key=key, request=request, source=self.source,
                              evidence=evidence, diagnostic=diagnostic, reported_counts=reported_counts)

    def _parameters(self, request):
        if request.population is None:
            raise _Failure("configuration_required")
        if request.variant != "standard":
            raise _Failure("unsupported_variant")
        population = request.population
        if (population.rating_min not in RATING_BOUNDS[:-1]
                or population.rating_max + 1 not in RATING_BOUNDS
                or not set(population.time_controls).issubset(SPEEDS)):
            raise _Failure("unsupported_population")
        try:
            if len(request.starting_fen.split()) != 6:
                raise ValueError("full FEN required")
            move_universe(request.starting_fen)
            board = chess.Board(request.starting_fen)
            for uci in request.history_uci:
                board.push_uci(uci)
            if move_universe(board.fen()) != request.position:
                raise ValueError("history does not reach target")
        except (ValueError, TypeError, IndexError):
            raise _Failure("invalid_request") from None
        return {"fen": request.starting_fen, "play": ",".join(request.history_uci),
                "variant": request.variant, "speeds": ",".join(sorted(population.time_controls)),
                "ratings": ",".join(str(rating) for rating in RATING_BOUNDS[:-1]
                                     if population.rating_min <= rating <= population.rating_max),
                "since": request.since, "until": request.until, "moves": request.move_limit,
                "topGames": 0, "recentGames": 0, "history": "false"}

    def _retry_at(self, header):
        now = self._now()
        if header:
            try:
                if re.fullmatch(r"[0-9]+", header.strip()):
                    return (now + timedelta(seconds=max(1, int(header)))).isoformat()
                deadline = parsedate_to_datetime(header)
                if deadline.tzinfo is not None and deadline.utcoffset() is not None:
                    return max(now + timedelta(seconds=1), deadline.astimezone(timezone.utc)).isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
        return (now + timedelta(seconds=60)).isoformat()

    async def _http(self, request, parameters, credential):
        # Adapted donor acquisition; neither cache nor storage scope is open here.
        async with httpx.AsyncClient(transport=self.transport_factory(),
                timeout=httpx.Timeout(request.timeout_seconds, connect=request.connect_timeout_seconds),
                follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", f"{self.source}/lichess", params=parameters,
                    headers={"Authorization": f"Bearer {credential}", "Accept": "application/json",
                             "Accept-Encoding": "identity",
                             "User-Agent": "Tempo Explorer/1"}) as response:
                if response.status_code in {401, 403}:
                    raise _Failure("credential_rejected", auth_failed=True)
                if response.status_code == 429:
                    raise _Failure("rate_limited", retryable=True,
                                   retry_at=self._retry_at(response.headers.get("Retry-After")))
                if 500 <= response.status_code <= 599:
                    raise _Failure("provider_unavailable", retryable=True)
                if response.status_code != 200:
                    raise _Failure("provider_request_rejected")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise _Failure("invalid_response")
                contents = bytearray()
                async for part in response.aiter_bytes():
                    if len(contents) + len(part) > request.max_response_bytes:
                        raise _Failure("response_too_large")
                    contents.extend(part)
                try:
                    return json.loads(contents, object_pairs_hook=_unique_object,
                                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
                except (ValueError, TypeError, UnicodeError):
                    raise _Failure("invalid_response") from None

    def _cached(self, cached, request):
        try:
            cached = ExplorerResult.model_validate_json(cached.model_dump_json())
            evidence = cached.evidence
            if (cached.request != request or cached.request_key != self.cache_key(request)
                    or cached.source != self.source or cached.diagnostic is not None
                    or evidence is None or evidence.provider != "lichess-explorer"
                    or evidence.population != request.population or evidence.position != request.position
                    or evidence.distribution != _distribution(request.position, evidence.counts)
                    or evidence.observed_at is None):
                raise ValueError("cache context mismatch")
            age = (self._now() - datetime.fromisoformat(evidence.observed_at)).total_seconds()
            if age < 0 or age > request.cache_max_age_seconds:
                return None
            return cached.model_copy(update={"from_cache": True})
        except (ValueError, TypeError, AttributeError):
            raise _Failure("invalid_cache_entry") from None

    async def _fetch(self, request, credential):
        parameters = self._parameters(request)
        if (not isinstance(credential, str) or not credential or len(credential) > 4096
                or any(not 33 <= ord(character) <= 126 for character in credential)):
            raise _Failure("credential_missing", auth_failed=True)
        if self.cache is not None and request.cache_max_age_seconds:
            try:
                cached = await self.cache.get(self.cache_key(request))
            except Exception:
                raise _Failure("cache_unavailable", retryable=True) from None
            if cached is not None:
                result = self._cached(cached, request)
                if result is not None:
                    return result
        payload = await self._http(request, parameters, credential)
        counts, distribution, reported_counts = _validate_payload(payload, request)
        observed_at = self._now().isoformat()
        evidence = ProbabilityEvidence(evidence_id=_digest({"key": self.cache_key(request),
            "observed_at": observed_at, "counts": counts.model_dump(mode="json") if counts else None,
            "reported_counts": [item.model_dump(mode="json") for item in reported_counts]}),
            position=request.position, provider="lichess-explorer", model=None, basis="population",
            population=request.population, personal_account=None, observed_at=observed_at,
            distribution=distribution, counts=counts, unavailable_reason=None)
        result = self._result(request, evidence=evidence, reported_counts=reported_counts)
        if self.cache is not None and request.cache_max_age_seconds:
            try:
                await self.cache.put(result.request_key, result)
            except Exception:
                raise _Failure("cache_unavailable", retryable=True) from None
        return result

    async def fetch(self, request: ExplorerRequest, *, credential: str | None,
                    cancellation: Cancellation | None = None) -> ExplorerResult:
        """Cancellation is an explicit unavailable result after child cleanup."""
        work = None
        cancelled = None
        try:
            if cancellation is not None and cancellation.is_set():
                raise _Failure("cancelled", retryable=True)
            async with asyncio.timeout(request.timeout_seconds):
                work = asyncio.create_task(self._fetch(request, credential))
                if cancellation is not None:
                    cancelled = asyncio.create_task(cancellation.wait())
                    await asyncio.wait((work, cancelled), return_when=asyncio.FIRST_COMPLETED)
                    if cancellation.is_set():
                        raise _Failure("cancelled", retryable=True)
                return await work
        except _Failure as failure:
            return self._result(request, diagnostic=failure.diagnostic)
        except (TimeoutError, httpx.TimeoutException):
            return self._result(request, diagnostic=ExplorerDiagnostic(code="provider_timeout", retryable=True))
        except asyncio.CancelledError:
            return self._result(request, diagnostic=ExplorerDiagnostic(code="cancelled", retryable=True))
        except httpx.DecodingError:
            return self._result(request, diagnostic=ExplorerDiagnostic(code="invalid_response"))
        except httpx.RequestError:
            return self._result(request, diagnostic=ExplorerDiagnostic(code="provider_unavailable", retryable=True))
        except (ValidationError, RecursionError):
            return self._result(request, diagnostic=ExplorerDiagnostic(code="invalid_response"))
        finally:
            children = [task for task in (work, cancelled) if task is not None]
            for task in children:
                if not task.done():
                    task.cancel()
            if children:
                await asyncio.gather(*children, return_exceptions=True)
