"""One explicit bounded public Lichess request; no durable jobs or downstream work.

Request/window/page validation adapts Game Evidence providers.py's Lichess path.
Storage checkpoints, provider leases, retries and comparison are deliberately not
part of this independent caller-driven adapter.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from time import monotonic

from tempo_game_review_contracts import CanonicalGame
from . import BytesInput, PgnGameIngester
from .pgn import MAX_SOURCE_BYTES


class PublicImportError(ValueError):
    def __init__(self, reason: str, *, retry_after: str | None = None):
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(reason + (f"; Retry-After: {retry_after}" if retry_after else ""))


@dataclass(frozen=True)
class PublicResponse:
    status: int
    body: bytes
    retry_after: str | None = None


class PublicHttpTransport(Protocol):
    def get(self, url: str, *, timeout: float, max_bytes: int) -> PublicResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class UrlPublicTransport:
    """Exact public endpoint, no ambient proxies, bounded reads/socket timeout.

    Elapsed budget is checked around reads; a blocked socket read may outlast
    that budget. This is not a hard real-time total request deadline.
    """
    def get(self, url: str, *, timeout: float, max_bytes: int) -> PublicResponse:
        try:
            parsed = urlsplit(url)
        except (TypeError, ValueError) as error:
            raise PublicImportError("exact_public_lichess_endpoint_required") from error
        if (not isinstance(url, str) or any(character.isspace() for character in url)
                or parsed.scheme != "https" or parsed.netloc != "lichess.org"
                or not re.fullmatch(r"/api/games/user/[A-Za-z0-9_-]{1,100}", parsed.path)
                or parsed.fragment):
            raise PublicImportError("exact_public_lichess_endpoint_required")
        if type(timeout) not in (int, float) or not 0 < timeout <= 15 or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_SOURCE_BYTES:
            raise PublicImportError("bounded_public_transport_limits_required")
        request = Request(url, headers={"Accept": "application/x-ndjson",
            "User-Agent": "tempo-game-ingestion/0.1.0 (+https://github.com/aaweaver-actuary/tempo)"})
        expires_at = monotonic() + timeout
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
                chunks, size = [], 0
                while True:
                    if monotonic() >= expires_at:
                        raise PublicImportError("provider_elapsed_budget_exceeded")
                    chunk = response.read(min(64 * 1024, max_bytes - size + 1))
                    if monotonic() >= expires_at:
                        raise PublicImportError("provider_elapsed_budget_exceeded")
                    size += len(chunk)
                    if size > max_bytes:
                        raise PublicImportError("provider_response_byte_bound_exceeded")
                    if not chunk:
                        break
                    chunks.append(chunk)
                return PublicResponse(response.status, b"".join(chunks), response.headers.get("Retry-After"))
        except HTTPError as error:
            try:
                return PublicResponse(error.code, b"", error.headers.get("Retry-After"))
            finally:
                error.close()
        except (TimeoutError, URLError, OSError) as error:
            reason = "provider_timeout" if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError) else "provider_transport_unavailable"
            raise PublicImportError(reason) from error


@dataclass(frozen=True)
class PublicGameBatch:
    games: tuple[CanonicalGame, ...]
    username: str
    since: str
    until: str
    limit: int
    limit_reached: bool


def fetch_lichess_games(transport: PublicHttpTransport, *, workspace_id: str,
        username: str, since: datetime, until: datetime, limit: int,
        game_revision: int, source_revision: int, timeout: float = 10) -> PublicGameBatch:
    """Read at most one ascending public page; a full limit is not window completion."""
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", username):
        raise PublicImportError("explicit_public_username_required")
    if not isinstance(since, datetime) or not isinstance(until, datetime) or since.utcoffset() is None or until.utcoffset() is None:
        raise PublicImportError("explicit_timezone_aware_window_required")
    since, until = since.astimezone(timezone.utc), until.astimezone(timezone.utc)
    if not since < until or until - since > timedelta(days=366):
        raise PublicImportError("ordered_window_at_most_366_days_required")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise PublicImportError("explicit_limit_between_1_and_100_required")
    if type(timeout) not in (int, float) or not 0 < timeout <= 15:
        raise PublicImportError("timeout_between_0_and_15_seconds_required")
    if not isinstance(workspace_id, str) or not workspace_id.strip() or len(workspace_id) > 200 or any(type(value) is not int or value < 1 for value in (game_revision, source_revision)):
        raise PublicImportError("explicit_workspace_and_positive_revisions_required")
    start, end = int(since.timestamp() * 1000), int(until.timestamp() * 1000)
    if start >= end:
        raise PublicImportError("window_requires_at_least_one_millisecond")
    params = {"since": start, "until": end - 1, "max": limit, "sort": "dateAsc",
        "pgnInJson": "true", "moves": "true", "ongoing": "false", "finished": "true", "evals": "false"}
    url = f"https://lichess.org/api/games/user/{username.casefold()}?{urlencode(params)}"
    try:
        response = transport.get(url, timeout=timeout, max_bytes=MAX_SOURCE_BYTES)
    except PublicImportError:
        raise
    except (OSError, TimeoutError) as error:
        raise PublicImportError("provider_timeout" if isinstance(error, TimeoutError) else "provider_transport_unavailable") from error
    if response.status != 200:
        raise PublicImportError("provider_rate_limited" if response.status == 429 else f"provider_http_{response.status}", retry_after=response.retry_after)
    if not isinstance(response.body, bytes) or len(response.body) > MAX_SOURCE_BYTES:
        raise PublicImportError("provider_response_byte_bound_exceeded")
    try:
        records = [json.loads(line) for line in response.body.splitlines() if line.strip()]
    except (ValueError, UnicodeError) as error:
        raise PublicImportError("invalid_provider_ndjson") from error
    if len(records) > limit:
        raise PublicImportError("provider_page_bound_exceeded")
    seen, previous, games = set(), start, []
    for record in records:
        if not isinstance(record, dict) or type(record.get("createdAt")) is not int or not isinstance(record.get("pgn"), str) or not isinstance(record.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", record["id"]):
            raise PublicImportError("provider_identity_timestamp_and_pgn_required")
        game_id, timestamp = record["id"], record["createdAt"]
        if not previous <= timestamp < end or game_id in seen:
            raise PublicImportError("unverified_provider_order_window_or_duplicate")
        previous = timestamp
        seen.add(game_id)
        identity = hashlib.sha256(json.dumps([workspace_id, "lichess", game_id, username.casefold()], separators=(",", ":")).encode()).hexdigest()
        try:
            games.append(PgnGameIngester().ingest(BytesInput(record["pgn"].encode()),
                workspace_id=workspace_id, game_id=identity, game_revision=game_revision,
                source_id=f"lichess:{game_id}", source_revision=source_revision,
                player=username, played_at=datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()))
        except ValueError as error:
            raise PublicImportError(f"invalid_provider_game:{game_id}:{error}") from error
    return PublicGameBatch(tuple(games), username.casefold(), since.isoformat(), until.isoformat(), limit, len(games) == limit)


__all__ = ["get_games", "fetch_lichess_games", "PublicHttpTransport", "UrlPublicTransport",
           "PublicResponse", "PublicGameBatch", "PublicImportError"]


def get_games(username: str, since: datetime, until: datetime, limit: int = 100) -> list[CanonicalGame]:
    """Simple public account/date-window entry; at most limit games, no persistence."""
    return list(fetch_lichess_games(UrlPublicTransport(), workspace_id="public",
        username=username, since=since, until=until, limit=limit,
        game_revision=1, source_revision=1).games)
