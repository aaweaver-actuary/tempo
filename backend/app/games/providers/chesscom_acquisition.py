"""Bounded, caller-driven Chess.com acquisition; no storage or retry loop."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlsplit

import httpx

from app.contracts.product_v02.games import GamePage, GameRecord, GameRejection, GameWindow

from ..contracts import ProviderRequestError
from ..ingestion.interfaces import RejectedGame, normalize_provider_pgn

_ORIGIN = "https://api.chess.com"
_SPEEDS = frozenset({"bullet", "blitz", "rapid", "classical"})
_MAX_MONTHS = 1200
_MAX_CURSOR_BYTES = 32768


class ChesscomSourceRecord(GameRecord):
    """Shared acquisition record plus immutable provider-owned raw provenance."""

    source_metadata: tuple[tuple[str, str], ...] = ()


class ChesscomGamePage(GamePage):
    """GamePage extension that retains provider metadata during serialization."""

    records: tuple[ChesscomSourceRecord, ...]


class ChesscomAcquisitionError(ProviderRequestError):
    """Sanitized incomplete result. Retry the unchanged continuation, if retryable."""

    complete = False

    def __init__(self, code: str, continuation: str | None, *, status_code: int = 502,
                 retry_after: str | None = None, retryable: bool = True):
        super().__init__(f"Chess.com acquisition: {code}", status_code, retry_after)
        self.code = code
        self.continuation = continuation
        self.retryable = retryable


def _retry_after(value: str | None) -> str | None:
    if value and len(value) <= 128:
        if value.isascii() and value.isdecimal():
            return value
        try:
            if parsedate_to_datetime(value).tzinfo is not None:
                return value
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def _account(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise ValueError("account must be a public Chess.com username")
    return value.casefold()


def _month(url: object, account: str) -> str:
    if not isinstance(url, str):
        raise ValueError("invalid archive URL")
    parsed = urlsplit(url)
    match = re.fullmatch(rf"/pub/player/{re.escape(account)}/games/([0-9]{{4}})/([0-9]{{2}})", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "api.chess.com"
            or parsed.query or parsed.fragment or not match):
        raise ValueError("invalid archive URL")
    year, month = map(int, match.groups())
    datetime(year, month, 1, tzinfo=timezone.utc)
    return f"{year:04d}/{month:02d}"


def _intersects(month: str, start: datetime, end: datetime) -> bool:
    year, number = map(int, month.split("/"))
    left = datetime(year, number, 1, tzinfo=timezone.utc)
    right = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=timezone.utc)
    return left < end and start < right


def _encode_cursor(scope: str, months: list[str], index: int) -> str:
    data = json.dumps({"v": 1, "scope": scope, "months": months, "index": index},
                      separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).decode()


def _decode_cursor(value: str, scope: str, account: str, start: datetime, end: datetime):
    try:
        if len(value) > _MAX_CURSOR_BYTES:
            raise ValueError
        data = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if (not isinstance(data, dict) or set(data) != {"v", "scope", "months", "index"}
                or data["v"] != 1 or data["scope"] != scope):
            raise ValueError
        months, index = data["months"], data["index"]
        if (not isinstance(months, list) or not 0 < len(months) <= _MAX_MONTHS
                or type(index) is not int or not 0 <= index < len(months)):
            raise ValueError
        for month in months:
            if _month(f"{_ORIGIN}/pub/player/{account}/games/{month}", account) != month or not _intersects(month, start, end):
                raise ValueError
        if months != sorted(set(months)):
            raise ValueError
        return months, index
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ChesscomAcquisitionError("invalid_continuation", value, retryable=False) from None


async def fetch_chesscom_page(
    account: str, window: GameWindow, client: httpx.AsyncClient, *,
    continuation: str | None = None, speeds: tuple[str, ...] = ("bullet", "blitz", "rapid", "classical"),
    rated_only: bool = False, source_revision: int = 1, timeout_seconds: float = 10,
    cancelled: Callable[[], bool] | None = None, max_response_bytes: int = 32 * 1024 * 1024,
) -> ChesscomGamePage:
    """Read the listing plus at most one complete month, or resume that month.

    UTC range is [start_at, end_at), using the provider's actual end_time.
    Client lifecycle and durable checkpoints/revision sequencing belong to caller.
    No sleep, retry, credential loading, database or Study import occurs here.
    """
    account = _account(account)
    if not isinstance(window, GameWindow):
        raise TypeError("window must be GameWindow")
    start = datetime.fromisoformat(window.start_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(window.end_at.replace("Z", "+00:00"))
    if (not speeds or not set(speeds) <= _SPEEDS or type(rated_only) is not bool
            or type(source_revision) is not int or not 1 <= source_revision <= 2**53 - 1
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 15
            or type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 32 * 1024 * 1024):
        raise ValueError("invalid acquisition policy or resource bound")
    filters = (("speeds", ",".join(sorted(set(speeds)))), ("rated_only", str(rated_only).lower()),
               ("variant", "standard"))
    scope = hashlib.sha256(json.dumps([account, start.isoformat(), end.isoformat(), filters,
                                      source_revision], separators=(",", ":")).encode()).hexdigest()

    def check_cancelled():
        if cancelled is not None and cancelled():
            raise ChesscomAcquisitionError("cancelled", continuation)

    async def request(url: str) -> dict:
        check_cancelled()
        try:
            async with client.stream("GET", url, timeout=timeout_seconds, follow_redirects=False) as response:
                if not response.is_success:
                    raise ChesscomAcquisitionError(
                        "rate_limited" if response.status_code == 429 else "http_error", continuation,
                        status_code=response.status_code,
                        retry_after=_retry_after(response.headers.get("Retry-After")),
                        retryable=response.status_code in {408, 429} or response.status_code >= 500,
                    )
                if "json" not in response.headers.get("content-type", "").lower():
                    raise ChesscomAcquisitionError("unexpected_content_type", continuation)
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    check_cancelled()
                    if len(body) + len(chunk) > max_response_bytes:
                        raise ChesscomAcquisitionError("response_too_large", continuation, retryable=False)
                    body.extend(chunk)
                check_cancelled()
                def reject_nonfinite(_):
                    raise ValueError
                payload = json.loads(body, parse_constant=reject_nonfinite)
                if not isinstance(payload, dict):
                    raise ValueError
                return payload
        except httpx.TimeoutException:
            raise ChesscomAcquisitionError("timeout", continuation) from None
        except httpx.TransportError:
            raise ChesscomAcquisitionError("transport_error", continuation) from None
        except (ValueError, UnicodeError):
            raise ChesscomAcquisitionError("malformed_json", continuation) from None

    if continuation is None:
        payload = await request(f"{_ORIGIN}/pub/player/{account}/games/archives")
        urls = payload.get("archives")
        if not isinstance(urls, list) or len(urls) > _MAX_MONTHS:
            raise ChesscomAcquisitionError("malformed_archives", None, retryable=False)
        try:
            months = sorted({_month(url, account) for url in urls})
            months = [month for month in months if _intersects(month, start, end)]
        except (ValueError, OverflowError):
            raise ChesscomAcquisitionError("invalid_archive_url", None, retryable=False) from None
        index = 0
    else:
        months, index = _decode_cursor(continuation, scope, account, start, end)

    records: list[ChesscomSourceRecord] = []
    rejections: list[GameRejection] = []
    if months:
        # Once the listing is frozen, a failure can retry without redoing earlier months.
        continuation = _encode_cursor(scope, months, index)
        archive_url = f"{_ORIGIN}/pub/player/{account}/games/{months[index]}"
        payload = await request(archive_url)
        games = payload.get("games")
        if not isinstance(games, list):
            raise ChesscomAcquisitionError("malformed_games", continuation)
        seen: dict[str, str] = {}
        for row, raw in enumerate(games):
            if row % 16 == 0:
                await asyncio.sleep(0)  # Yield CPU and permit native task cancellation.
            check_cancelled()
            source_id = None
            try:
                if not isinstance(raw, dict):
                    raise RejectedGame("malformed_record")
                source_id = _source_id(raw)
                stamp = raw.get("end_time")
                if type(stamp) is not int:
                    raise RejectedGame("invalid_timestamp")
                played_at = datetime.fromtimestamp(stamp, timezone.utc)
                speed = "classical" if raw.get("time_class") == "daily" else raw.get("time_class")
                reason = ("outside_window" if not start <= played_at < end else
                          "unsupported_variant" if raw.get("rules") != "chess" else
                          "time_control_filtered" if speed not in speeds else
                          "rated_filtered" if rated_only and raw.get("rated") is not True else None)
                if reason:
                    raise RejectedGame(reason)
                if type(raw.get("rated")) is not bool or not isinstance(raw.get("pgn"), str):
                    raise RejectedGame("malformed_record")
                players = [raw.get(color) for color in ("white", "black")]
                if any(player is not None for player in players):
                    if not any(isinstance(player, dict) and isinstance(player.get("username"), str)
                               and player["username"].casefold() == account for player in players):
                        raise RejectedGame("invalid_pgn_or_player")
                pgn = raw["pgn"]
                try:
                    normalize_provider_pgn("chess.com", account, pgn, provider_game_id=source_id,
                                           played_at=played_at.isoformat(), speed=speed,
                                           rated=raw["rated"], game_url=raw.get("url", ""))
                except RejectedGame:
                    raise RejectedGame("invalid_pgn_or_player") from None
                digest = hashlib.sha256(pgn.encode()).hexdigest()
                payload_digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"),
                                                          ensure_ascii=False).encode()).hexdigest()
                if source_id in seen:
                    if seen[source_id] != payload_digest:
                        raise ChesscomAcquisitionError("conflicting_source_id", continuation, retryable=False)
                    raise RejectedGame("duplicate_source_id")
                seen[source_id] = payload_digest
                metadata = {key: raw[key] for key in ("url", "uuid", "time_class", "time_control", "rated", "rules") if key in raw}
                metadata.update(archive_url=archive_url, payload_digest=payload_digest)
            except RejectedGame as error:
                rejections.append(GameRejection(source_game_id=source_id, source_revision=source_revision,
                                                reason=str(error)))
                continue
            except (ValueError, TypeError, OverflowError, OSError):
                rejections.append(GameRejection(source_game_id=source_id, source_revision=source_revision,
                                                reason="malformed_record"))
                continue
            # DTO/programming failures are not misreported as rejected provider records.
            records.append(ChesscomSourceRecord(source_game_id=source_id, source_revision=source_revision,
                                                source_digest=digest, pgn=pgn, played_at=played_at.isoformat(),
                                                time_precision="second", source_metadata=tuple(
                                                    (key, json.dumps(value, ensure_ascii=False))
                                                    for key, value in sorted(metadata.items()))))
        records.sort(key=lambda record: (record.played_at, record.source_game_id))
        index += 1
    check_cancelled()
    next_cursor = _encode_cursor(scope, months, index) if index < len(months) else None
    return ChesscomGamePage(provider="chesscom", account=account, window=window, records=tuple(records),
                            rejections=tuple(rejections), continuation=next_cursor, complete=next_cursor is None,
                            saturated=False, filters=filters)


def _source_id(raw: dict) -> str:
    url = raw.get("url")
    match = None
    if url is not None:
        if not isinstance(url, str):
            raise RejectedGame("invalid_game_url")
        try:
            parsed = urlsplit(url)
            match = re.fullmatch(r"/(?:game/(?:live|daily)|(?:live|daily)/game)/([0-9]+)", parsed.path)
        except ValueError:
            raise RejectedGame("invalid_game_url") from None
        if (parsed.scheme != "https" or parsed.netloc not in {"www.chess.com", "chess.com"}
                or parsed.query or parsed.fragment or not match):
            raise RejectedGame("invalid_game_url")
    uuid = raw.get("uuid")
    if isinstance(uuid, str) and 0 < len(uuid) <= 200 and not any(c.isspace() for c in uuid):
        return uuid
    if match:
        return match[1]
    raise RejectedGame("missing_source_id")
