# Bounded Lichess acquisition (#484)

`fetch_lichess_page` in [lichess_acquisition.py](lichess_acquisition.py) performs one
bounded async export. It returns #482's `GamePage`/`GameRecord`/`GameRejection`
values. Per #482's provider-wrapper direction, `LichessSourceRecord(GameRecord)`
adds only immutable `source_metadata`; `LichessGamePage(GamePage)` specializes its
records so JSON round trips retain those provider facts. These are shared-value
subtypes, not competing canonical game/effect DTOs. Provider wire readers use
`LichessGamePage.model_validate_json`; common in-process consumers use the inherited
GamePage/GameRecord fields. Existing donor entry points in
`lichess.py` remain unchanged. The new operation reuses the same
`normalize_provider_pgn` legal-game/account/result validation, retaining the
original PGN rather than re-exporting it.

```python
import httpx
from app.contracts.product_v02.games import GameWindow
from app.games.providers.lichess_acquisition import fetch_lichess_page

async def one_page():
    window = GameWindow(start_at="2026-10-01T00:00:00Z",
        end_at="2026-10-02T00:00:00Z", precision="millisecond")
    async with httpx.AsyncClient() as client:
        return await fetch_lichess_page(account="EXPLICIT_ACCOUNT", window=window,
            client=client, source_revision=1, speeds=("rapid",), rated=None,
            page_size=100, timeout_seconds=10, continuation=None)
```

The account is normalized case-insensitively. Windows are half-open UTC
`[start_at, end_at)` and must lie exactly on millisecond boundaries; unsupported
submillisecond windows fail before HTTP rather than round away records. `rated`
is `True` for rated-only, `False` for casual-only, or `None` for both. Requested
filters are retained as immutable facts. Only bullet/blitz/rapid/classical are
supported. Identity is `(provider, account, source_game_id)`, never a timestamp.

Each record retains exact original PGN, its SHA-256 digest, explicit caller-owned
positive `source_revision`, UTC played time and millisecond precision. The
adapter does not invent a provider revision or allocate durable revision numbers.
The coordinator must compare digests, assign revisions and quarantine changed
evidence; the revision integer alone is not a duplicate key. The PGN retains
source player/color/move/result metadata. Responses outside account/result/legal
game constraints are rejected with sanitized reasons.

`source_metadata` contains JSON-encoded immutable values for source URL, complete
canonical source-JSON SHA-256 `payload_digest`, original created/last-move time,
speed, rated flag, variant (null when unsupplied), and validated learner color.
The payload fingerprint preserves metadata changes separately from exact PGN
content; the coordinator must inspect both when reconciling changed evidence.

## Checkpoint and completeness

One call makes at most one HTTP request. Request `page_size` is 1–1000 (default 100);
the independent output budgets below may end a page earlier.
The next request includes boundary overlap, so its explicit `max` may grow up to
1000 even with `page_size=100`. A response containing `max` source rows always
returns `saturated=True`, `complete=False`, and an opaque continuation.

The continuation binds account, exact window, normalized filters, source revision
and page size. It stores the last millisecond and IDs/digests already emitted at
that millisecond. The next request starts inclusively at that boundary, deduplicates
by source ID, and grows its bounded request enough to see beyond overlap. Equal
timestamps with different IDs remain distinct. A changed overlapping ID produces
`changed_boundary`; it is never silently discarded. No cursor is persisted here.

A normally ended short response proves exhaustion of the remaining requested
window and returns `complete=True`, with no continuation. Rejections with reliable
ID/time are accounted in `rejections`, including local filter mismatches and
invalid PGNs; they count toward saturation. Identical duplicate source rows are
accounted as `duplicate_source_id`; conflicting rows with one ID fail the page.
Unreliable identity/timestamp/order,
invalid NDJSON, byte overflow, interrupted streams, HTTP errors and timeouts fail
the entire operation, preserve the input checkpoint and return no partial page.
Persist output and its continuation atomically in the coordinator before moving on.
Retrying the same semantic request/checkpoint produces the same page for unchanged
provider evidence; previously delivered pages must still be idempotent downstream.

Explicit unsupported case: 1000 distinct games at one exact millisecond cannot be
proved exhausted within this adapter's limit, even if exactly 1000 exist. It
returns a saturated page, then `ambiguous_boundary` with that same continuation
before further HTTP. No timestamp is skipped. Safe fallback: leave acquisition
incomplete/visible, retain its checkpoint, and use explicit source PGN ingestion
for needed games. #484 remains the follow-up scope for an explicitly larger
bounded probe or provider-supported stable cursor; acceptance must prove exact-ID
equality with more than 1000 coincident records before claiming support.

As with the provider export itself, completeness is for the observed finished-game
window, not a snapshot guarantee for future games, ongoing games that finish later,
or subsequently changed provider evidence. The coordinator owns overlapping later
sync windows and revision reconciliation.

## Errors and lifecycle

`LichessAcquisitionError` exposes `kind`, `status_code`, `retry_after` and the
**input** `continuation`. Kinds: `invalid_request`, `invalid_continuation`,
`not_found`, `rate_limited`, `http_error`, `invalid_response`, `response_limit`,
`timeout`, `cancelled`, `transport_error`, `ambiguous_boundary`, `changed_boundary`.
Valid numeric/HTTP-date Retry-After is retained. Error messages contain a fixed
reason, without response bodies, request headers, credentials or underlying
transport messages. Fixtures contain only synthetic users and games.

The caller injects and owns `httpx.AsyncClient`; this operation does not load
credentials or configuration, construct a global client, retry, sleep or persist.
Redirects are disabled per request. Streaming decoding is limited to 8 MiB, read
in 64 KiB chunks. `max_response_bytes` may lower that bound. HTTP and total async
I/O timeout default to 10 seconds and cannot exceed 30 seconds. CPU validation is
bounded by that byte/page limit and checks cooperative cancellation per record.
`cancelled: Callable[[], bool]` is checked before/after I/O and per chunk/record;
blocked I/O may wait until timeout. Task cancellation interrupts blocked I/O and
returns typed `cancelled`. No child task is created, and the response context
closes before errors escape. No Study or Tactics resource is acquired.

## Owning verification and handoff

```sh
make python-file FILE=backend/tests/test_product_lichess.py
```

The regular backend test file uses injected `httpx.MockTransport`/synthetic
streams. Named acceptance checks cover 205-game exact source-ID equality,
saturation, equal timestamps, deterministic retry, interrupted checkpoint
preservation, filtered/rejected accounting, 429 Retry-After, typed failures,
timeout/cancellation stream/task cleanup, and storage/learning independence.
Additional changed-boundary checks cover cursor input binding, changed overlap,
unverifiable progress and explicit response limits. No live HTTP, database,
engine, browser, Docker or full suite is needed for this boundary. The legacy
donor executable is unchanged, so its existing test evidence is retained.

T08/#489 owns persistence/retries; T10/#491 owns product/client/export wiring.
Required integration change: import this function directly in composition, inject
a bounded client and cancellation capability, await one page, atomically persist
its result/checkpoint, and interpret `complete` rather than record count. No shared
`__init__`, dependency, runtime registration or root application edit is included.
The synchronous `GameAcquisitionPort` can be adapted by the integration owner at
its async scheduling boundary; the public returned values are the shared types.
The canonical implemented dependency drawing is in [README.md](README.md).

Primary semantics checked against the [Lichess export API](https://lichess.org/api#tag/Games/operation/apiGamesUser),
its [OpenAPI source](https://github.com/lichess-org/api/blob/master/doc/specs/tags/games/api-games-user-username.yaml),
and the half-open creation-time query in [Query.scala](https://github.com/lichess-org/lila/blob/master/modules/game/src/main/Query.scala)
and [dateBetween](https://github.com/lichess-org/lila/blob/master/modules/db/src/main/dsl.scala).
The new request sends `until=end_at` without subtracting a millisecond.

## Task D emitted-page budgets and delayed completion

Output bounds additionally default to `max_page_records=100`,
`max_page_positions=10000`, `max_page_bytes=8388608`, independently of the
streamed response limit and request `page_size`. A record that would overflow a
page is not consumed: the original millisecond/ID boundary resumes it on the
next page. Existing version 1 continuation binding and coincident-timestamp
safeguards remain. A single oversized record fails as
`record_exceeds_page_budget` with the original continuation. Serialized page
provenance reserves room for the existing bounded boundary token and remains
below the coordinator's 16 MiB limit.

The request still excludes ongoing games and queries **creation time**. A game
that finishes after its creation time leaves the short forward overlap is found
by `ContinuousSync`'s explicitly configured full-history reconciliation, starting
at the saved coverage start. The adapter itself creates no timer, storage or
retry loop. See the [continuous owner](../feedback/README.md#continuous-acquisition-and-historical-reconciliation--task-d).
Current regular acquisition proof is in `test_product_continuous_sync.py` plus
the selected adapter cases in `test_product_game_sync_integration.py`; the older
standalone suite named above is absent at the Task D baseline.
