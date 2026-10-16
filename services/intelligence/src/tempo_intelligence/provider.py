"""Bounded asynchronous Explorer HTTP and source-native count validation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import re

import httpx

from .models import LICHESS_RATINGS, LICHESS_SPEEDS


MAX_RESPONSE_BYTES = 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 5
TOTAL_TIMEOUT_SECONDS = 20
_UCI = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ProviderFailure(RuntimeError):
    """Safe failure metadata; never includes provider body, URL, or credential."""

    def __init__(
        self, code: str, *, retryable: bool = False,
        retry_at: datetime | None = None, auth_failed: bool = False,
    ):
        self.code = code
        self.retryable = retryable
        self.retry_at = retry_at
        self.auth_failed = auth_failed
        super().__init__(code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def retry_after_deadline(header: str | None, *, now: datetime | None = None) -> datetime:
    """Honor a valid HTTP Retry-After; otherwise use the documented 60s guidance."""
    current = now or _now()
    if header:
        try:
            if re.fullmatch(r"[0-9]+", header.strip()):
                return current + timedelta(seconds=max(1, int(header.strip())))
            deadline = parsedate_to_datetime(header)
            if deadline.tzinfo is not None and deadline.utcoffset() is not None:
                return max(current + timedelta(seconds=1), deadline.astimezone(timezone.utc))
        except (ValueError, TypeError, OverflowError):
            pass
    return current + timedelta(seconds=60)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _counts(payload: dict) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise ProviderFailure("invalid_response")
    counts = {}
    for outcome in ("white", "draws", "black"):
        count = payload.get(outcome)
        if type(count) is not int or count < 0:
            raise ProviderFailure("invalid_response")
        counts[outcome] = count
    return counts


def validate_evidence(payload: object, descriptor: dict) -> dict:
    """Retain counts and legal observations without estimating probabilities."""
    if not isinstance(payload, dict) or not isinstance(payload.get("moves"), list):
        raise ProviderFailure("invalid_response")
    root_counts = _counts(payload)
    legal_moves = descriptor["legal_moves"]
    legal_set = set(legal_moves)
    moves_by_uci = {}
    for move in payload["moves"]:
        move_counts = _counts(move)
        uci = move.get("uci")
        if (not isinstance(uci, str) or not _UCI.fullmatch(uci)
                or uci not in legal_set or uci in moves_by_uci):
            raise ProviderFailure("invalid_response")
        moves_by_uci[uci] = {"uci": uci, **move_counts, "games": sum(move_counts.values())}
    # Outcome-wise validation is stronger than checking only the combined sum.
    if any(sum(move[outcome] for move in moves_by_uci.values()) > root_counts[outcome]
           for outcome in root_counts):
        raise ProviderFailure("invalid_response")
    total_games = sum(root_counts.values())
    returned_games = sum(move["games"] for move in moves_by_uci.values())
    unallocated_games = total_games - returned_games
    missing_moves = sorted(legal_set - moves_by_uci.keys())
    observations = []
    for uci in sorted(legal_set):
        if uci in moves_by_uci:
            games = moves_by_uci[uci]["games"]
            status = "observed" if games else "explicit_zero"
        elif total_games and not unallocated_games:
            games, status = 0, "explicit_zero"
        else:
            games = None
            status = "unreported" if unallocated_games else "unknown"
        observations.append({"uci": uci, "status": status, "games": games})
    limitations = ["historical_sample_counts_are_not_calibrated_probabilities", "provider_data_generation_not_reported"]
    if unallocated_games:
        limitations.append("provider_sample_contains_unreported_moves")
    if not total_games:
        limitations.append("no_games_in_requested_sample")
    if not legal_set:
        limitations.append("terminal_position_has_no_move_distribution")
    evidence = {
        "schema_version": 1,
        "source": descriptor["kind"],
        "position_key": descriptor["position_key"],
        "provider_data_generation": None,
        "counts": root_counts,
        "total_games": total_games,
        "moves": [moves_by_uci[uci] for uci in sorted(moves_by_uci)],
        "returned_games": returned_games,
        "unallocated_games": unallocated_games,
        "legal_observations": observations,
        "missing_moves": missing_moves,
        "sample_complete": bool(total_games and not unallocated_games),
        "limitations": limitations,
    }
    evidence["output_digest"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return evidence


class ExplorerClient:
    def __init__(
        self, base_url: str = "https://explorer.lichess.org",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        parsed = httpx.URL(base_url)
        if (parsed.scheme not in {"http", "https"} or parsed.userinfo
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("invalid_provider_endpoint")
        self.base_url = str(parsed).rstrip("/")
        self.transport = transport

    async def fetch(self, descriptor: dict, token: str) -> dict:
        kind = descriptor.get("kind")
        if kind not in {"lichess", "masters"}:
            raise ProviderFailure("unsupported_source")
        parameters = descriptor.get("parameters")
        legal_moves = descriptor.get("legal_moves")
        if (not isinstance(parameters, dict) or not isinstance(legal_moves, list)
                or any(not isinstance(move, str) or not _UCI.fullmatch(move) for move in legal_moves)
                or len(set(legal_moves)) != len(legal_moves)
                or not isinstance(descriptor.get("position_key"), str)):
            raise ProviderFailure("invalid_request")
        allowed = {"fen", "play", "since", "until", "moves", "topGames"}
        if kind == "lichess":
            allowed |= {"variant", "speeds", "ratings", "recentGames", "history"}
        if (set(parameters) - allowed
                or parameters.get("fen") != descriptor.get("canonical_fen")
                or parameters.get("moves") != len(legal_moves)
                or any(type(value) not in {str, int, bool} for value in parameters.values())):
            raise ProviderFailure("invalid_request")
        if kind == "lichess":
            if "ratings" in parameters:
                ratings = str(parameters["ratings"]).split(",")
                if (not ratings or len(set(ratings)) != len(ratings)
                        or any(rating not in {str(group) for group in LICHESS_RATINGS} for rating in ratings)):
                    raise ProviderFailure("unsupported_cohort")
            if "speeds" in parameters:
                speeds = str(parameters["speeds"]).split(",")
                if (not speeds or len(set(speeds)) != len(speeds)
                        or any(speed not in LICHESS_SPEEDS for speed in speeds)):
                    raise ProviderFailure("unsupported_cohort")
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(character.isspace() or not 33 <= ord(character) <= 126 for character in token)):
            raise ProviderFailure("credential_missing")
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=httpx.Timeout(TOTAL_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
                    follow_redirects=False, trust_env=False,
                ) as client:
                    async with client.stream(
                        "GET", f"{self.base_url}/{kind}", params=parameters,
                        headers={"Authorization": f"Bearer {token}",
                                 "Accept": "application/json", "User-Agent": "Tempo Intelligence/1"},
                    ) as response:
                        if response.status_code in {401, 403}:
                            raise ProviderFailure("credential_rejected", auth_failed=True)
                        if response.status_code == 429:
                            raise ProviderFailure(
                                "rate_limited", retryable=True,
                                retry_at=retry_after_deadline(response.headers.get("Retry-After")),
                            )
                        if 500 <= response.status_code <= 599:
                            raise ProviderFailure("provider_unavailable", retryable=True)
                        if response.status_code != 200:
                            raise ProviderFailure("provider_request_rejected")
                        contents = bytearray()
                        async for part in response.aiter_bytes():
                            if len(contents) + len(part) > MAX_RESPONSE_BYTES:
                                raise ProviderFailure("response_too_large")
                            contents.extend(part)
                        try:
                            payload = json.loads(contents, object_pairs_hook=_unique_object)
                        except (ValueError, TypeError, UnicodeError):
                            raise ProviderFailure("invalid_response") from None
                        return validate_evidence(payload, descriptor)
        except ProviderFailure:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderFailure("provider_timeout", retryable=True) from None
        except httpx.DecodingError:
            raise ProviderFailure("invalid_response") from None
        except httpx.RequestError:
            raise ProviderFailure("provider_unavailable", retryable=True) from None
