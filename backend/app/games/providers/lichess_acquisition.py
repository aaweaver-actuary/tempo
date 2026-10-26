"""Caller-driven Lichess pages. HTTP and validation only; no cursor persistence."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Literal

import httpx

from app.contracts.product_v02.games import GamePage, GameRecord, GameRejection, GameWindow
from ..ingestion.normalizer import RejectedGame, normalize_provider_pgn

MAX_PAGE_SIZE = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_SPEEDS = frozenset({"bullet", "blitz", "rapid", "classical"})
FailureKind = Literal[
    "invalid_request", "invalid_continuation", "not_found", "rate_limited",
    "http_error", "invalid_response", "response_limit", "timeout", "cancelled",
    "transport_error", "ambiguous_boundary", "changed_boundary",
]


class LichessSourceRecord(GameRecord):
    """Provider facts extend shared record identity, rather than copying its DTO."""

    source_metadata: tuple[tuple[str, str], ...] = ()


class LichessGamePage(GamePage):
    records: tuple[LichessSourceRecord, ...]


class LichessAcquisitionError(RuntimeError):
    """Sanitized failure; replay the original continuation, never a partial read."""

    def __init__(self, kind: FailureKind, *, continuation: str | None = None,
                 status_code: int | None = None, retry_after: str | None = None):
        super().__init__(kind)
        self.kind = kind
        self.continuation = continuation
        self.status_code = status_code
        self.retry_after = retry_after


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False).encode()


def _milliseconds(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond % 1000:
        raise LichessAcquisitionError("invalid_request")
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def _timestamp(value: int) -> str:
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)).isoformat(timespec="milliseconds")


def _cursor(key: str, since: int, seen: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(_json({"v": 1, "key": key, "since": since, "seen": seen})).decode()


def _restore(token: str | None, key: str, start: int, end: int) -> tuple[int, dict[str, str]]:
    if token is None:
        return start, {}
    try:
        if not isinstance(token, str) or len(token) > 256_000:
            raise ValueError
        value = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
        if (not isinstance(value, dict) or set(value) != {"v", "key", "since", "seen"}
                or value["v"] != 1 or value["key"] != key
                or type(value["since"]) is not int or not start <= value["since"] < end
                or not isinstance(value["seen"], dict) or not 1 <= len(value["seen"]) <= MAX_PAGE_SIZE
                or any(not _NAME.fullmatch(game_id) or not isinstance(digest, str)
                       or not re.fullmatch(r"[0-9a-f]{64}", digest)
                       for game_id, digest in value["seen"].items())):
            raise ValueError
        return value["since"], value["seen"]
    except (ValueError, TypeError, UnicodeError):
        raise LichessAcquisitionError("invalid_continuation", continuation=token) from None


async def _read(client: httpx.AsyncClient, account: str, params: dict[str, object], *,
                timeout: float, max_bytes: int, check_cancel: Callable[[], None],
                continuation: str | None) -> bytes:
    try:
        async with asyncio.timeout(timeout):
            check_cancel()
            async with client.stream(
                "GET", f"https://lichess.org/api/games/user/{account}", params=params,
                headers={"Accept": "application/x-ndjson", "Accept-Encoding": "identity"},
                timeout=timeout, follow_redirects=False,
            ) as response:
                check_cancel()
                status = response.status_code
                if status != 200:
                    kind = "not_found" if status == 404 else "rate_limited" if status == 429 else "http_error"
                    retry_after = response.headers.get("Retry-After")
                    # Retain only the HTTP Retry-After grammar, never arbitrary server text.
                    if retry_after and not (re.fullmatch(r"[0-9]{1,10}", retry_after)
                            or re.fullmatch(r"[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9:]{8} GMT", retry_after)):
                        retry_after = None
                    raise LichessAcquisitionError(kind, continuation=continuation,
                                                 status_code=status, retry_after=retry_after)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"application/x-ndjson", "application/ndjson"}:
                    raise LichessAcquisitionError("invalid_response", continuation=continuation)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    check_cancel()
                    size += len(chunk)
                    if size > max_bytes:
                        raise LichessAcquisitionError("response_limit", continuation=continuation)
                    chunks.append(chunk)
                return b"".join(chunks)
    except (TimeoutError, httpx.TimeoutException):
        raise LichessAcquisitionError("timeout", continuation=continuation) from None
    except asyncio.CancelledError:
        raise LichessAcquisitionError("cancelled", continuation=continuation) from None
    except httpx.HTTPError:
        raise LichessAcquisitionError("transport_error", continuation=continuation) from None


async def fetch_lichess_page(*, account: str, window: GameWindow,
        client: httpx.AsyncClient, source_revision: int,
        continuation: str | None = None, speeds: tuple[str, ...] = ("bullet", "blitz", "rapid", "classical"),
        rated: bool | None = None, page_size: int = 100, timeout_seconds: float = 10,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        cancelled: Callable[[], bool] | None = None) -> GamePage:
    """One bounded GET, inclusive boundary replay, exact IDs, no automatic retries.

    Windows are [start_at, end_at) at millisecond resolution. A full page always
    returns incomplete. The cursor replays the last timestamp and binds all
    semantic request inputs. The next bounded request grows to include overlap.
    At MAX_PAGE_SIZE coincident IDs, stop explicitly rather than skip a timestamp.
    """
    if (not isinstance(account, str) or not _NAME.fullmatch(account)
            or not isinstance(window, GameWindow)
            or type(source_revision) is not int or not 1 <= source_revision <= 2**53 - 1
            or type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE
            or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
            or type(max_response_bytes) is not int or not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES
            or not isinstance(speeds, tuple) or not speeds
            or any(not isinstance(speed, str) or speed not in _SPEEDS for speed in speeds)
            or (cancelled is not None and not callable(cancelled))
            or (rated is not None and type(rated) is not bool)):
        raise LichessAcquisitionError("invalid_request", continuation=continuation)
    account = account.casefold()
    speeds = tuple(sorted(set(speeds)))
    start, end = _milliseconds(window.start_at), _milliseconds(window.end_at)
    if not 0 <= start < end <= 253402300799999:
        raise LichessAcquisitionError("invalid_request", continuation=continuation)
    filters = (("perfType", ",".join(speeds)),) + (() if rated is None else (("rated", str(rated).lower()),))
    key = _digest(_json([account, start, end, window.precision, filters, source_revision, page_size]))
    since, seen = _restore(continuation, key, start, end)
    if len(seen) >= MAX_PAGE_SIZE:
        raise LichessAcquisitionError("ambiguous_boundary", continuation=continuation)
    limit = min(MAX_PAGE_SIZE, page_size + len(seen))

    def check_cancel():
        if cancelled is not None and cancelled():
            raise LichessAcquisitionError("cancelled", continuation=continuation)

    params = dict(filters)
    params.update({"since": since, "until": end, "max": limit, "sort": "dateAsc",
                   "pgnInJson": "true", "moves": "true", "opening": "true",
                   "ongoing": "false", "finished": "true", "evals": "false"})
    body = await _read(client, account, params, timeout=timeout_seconds,
                       max_bytes=max_response_bytes, check_cancel=check_cancel,
                       continuation=continuation)
    try:
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    except (ValueError, UnicodeError):
        raise LichessAcquisitionError("invalid_response", continuation=continuation) from None
    if len(rows) > limit:
        raise LichessAcquisitionError("response_limit", continuation=continuation)
    records, rejections = [], []
    previous, page_ids = since, {}
    boundary = dict(seen)
    for row in rows:
        check_cancel()
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not _NAME.fullmatch(row["id"]) or type(row.get("createdAt")) is not int
                or not previous <= row["createdAt"] < end):
            raise LichessAcquisitionError("invalid_response", continuation=continuation)
        game_id, timestamp = row["id"], row["createdAt"]
        try:
            row_digest = _digest(_json(row))
        except (ValueError, TypeError):
            raise LichessAcquisitionError("invalid_response", continuation=continuation) from None
        if game_id in page_ids:
            if page_ids[game_id] != row_digest:
                raise LichessAcquisitionError("changed_boundary", continuation=continuation)
            rejections.append(GameRejection(source_game_id=game_id,
                source_revision=source_revision, reason="duplicate_source_id"))
            continue
        page_ids[game_id] = row_digest
        if timestamp != previous:
            boundary = {}
        previous = timestamp
        boundary[game_id] = row_digest
        if timestamp == since and game_id in seen:
            if seen[game_id] != row_digest:
                raise LichessAcquisitionError("changed_boundary", continuation=continuation)
            continue
        reason = None
        if type(row.get("rated")) is not bool or not isinstance(row.get("speed"), str):
            reason = "invalid_filter_metadata"
        elif row["speed"] not in speeds:
            reason = "filtered_speed"
        elif rated is not None and row["rated"] != rated:
            reason = "filtered_rated"
        elif row.get("variant") not in (None, "standard", "fromPosition"):
            reason = "unsupported_variant"
        elif not isinstance(row.get("pgn"), str) or not row["pgn"].strip():
            reason = "invalid_pgn"
        else:
            try:
                # Reuse the provider donor's legal-game/account/result validation.
                normalized = normalize_provider_pgn("lichess", account, row["pgn"],
                    provider_game_id=game_id, played_at=_timestamp(timestamp),
                    speed=row["speed"], rated=row["rated"],
                    game_url=f"https://lichess.org/{game_id}")
            except RejectedGame as error:
                reason = {
                    "configured account did not play this game": "account_mismatch",
                    "only standard chess games are supported": "unsupported_variant",
                    "game is unfinished or has an unsupported result": "unfinished_game",
                }.get(str(error), "invalid_pgn")
            except ValueError:
                reason = "invalid_pgn"
        if reason:
            rejections.append(GameRejection(source_game_id=game_id,
                source_revision=source_revision, reason=reason))
        else:
            metadata = {"payload_digest": row_digest, "game_url": f"https://lichess.org/{game_id}",
                "created_at_ms": timestamp, "last_move_at_ms": row.get("lastMoveAt"),
                "speed": row["speed"], "rated": row["rated"], "variant": row.get("variant"),
                "learner_color": normalized.color}
            records.append(LichessSourceRecord(source_game_id=game_id, source_revision=source_revision,
                source_digest=_digest(row["pgn"].encode()), pgn=row["pgn"],
                played_at=_timestamp(timestamp), time_precision="millisecond",
                source_metadata=tuple((name, _json(value).decode()) for name, value in sorted(metadata.items()))))
    saturated = len(rows) == limit
    if saturated and len(boundary) > MAX_PAGE_SIZE:
        raise LichessAcquisitionError("ambiguous_boundary", continuation=continuation)
    next_token = _cursor(key, previous, boundary) if saturated else None
    if saturated and next_token == continuation:
        raise LichessAcquisitionError("ambiguous_boundary", continuation=continuation)
    return LichessGamePage(provider="lichess", account=account, window=window,
        records=tuple(records), rejections=tuple(rejections), filters=filters,
        continuation=next_token,
        complete=not saturated, saturated=saturated)


__all__ = ["fetch_lichess_page", "LichessAcquisitionError", "LichessGamePage", "LichessSourceRecord",
           "MAX_PAGE_SIZE", "MAX_RESPONSE_BYTES"]
