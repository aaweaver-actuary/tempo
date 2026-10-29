# Restartable Chess.com acquisition

`await fetch_chesscom_page(account, window, client, *, continuation=None,
speeds=("bullet", "blitz", "rapid", "classical"), rated_only=False,
source_revision=1, timeout_seconds=10, cancelled=None,
max_response_bytes=33554432, max_page_records=100, max_page_positions=10000,
max_page_bytes=8388608) -> ChesscomGamePage` returns #482's immutable v0.2
`GamePage` through a provider-local subtype. `ChesscomSourceRecord` extends only
the shared `GameRecord` with `source_metadata`; `ChesscomGamePage` specializes
only its records field so `model_dump_json()` preserves provider provenance.
No canonical game/effect facts or shared DTO fields are duplicated. Consumers
can use the base GamePage interface; persist the provider page serialization
before projecting it into another model that may drop provider extension fields.
The client is an injected `httpx.AsyncClient`, including `MockTransport` for tests.
The caller owns its lifetime and must supply a public, credential-free transport.
No client, credential, connection, queue or worker is created at import time.

Archive paths, month-by-game-end semantics and epoch-second timestamps were
checked against the primary [Chess.com Published Data API documentation](https://www.chess.com/news/view/published-data-api).
No live player endpoint was queried for qualification.

The existing `chesscom.py` donor APIs are unchanged. Their useful parser is reused
through `normalize_provider_pgn`, preserving player/result/variant/legal-mainline
validation without another PGN parser. The capability missing from the donor is
an honest bounded page with original source bytes and a safe continuation.

## Scope, source and rejection facts

Windows are UTC **[start_at, end_at)**, consistent with the Lichess adapter. Actual
provider `end_time` is retained as second precision; no current time or PGN date
is substituted. Only listed months intersecting the window are requested. A
December/January boundary is handled as a calendar boundary. `daily` maps to the
retained `classical` speed name; original time class/control remains in metadata.
Only standard `rules=chess` is supported. Other variants, out-of-window records,
excluded time controls/rated policy, missing timestamps, malformed PGNs and
wrong players produce explicit `GameRejection` facts. Filtered/rejected rows plus
accepted records account for all rows in the observed month; they are never
reported as imported games. Unsupported variants are a rejection, not a lapse.

Stable source ID is provider `uuid`, falling back only to an exact HTTPS
Chess.com live/daily game URL's numeric ID. PGN Site, game ordering and fetch time
never establish identity. The PGN string is retained byte-for-byte after JSON
UTF-8 decoding; `source_digest` is SHA-256 of those UTF-8 bytes. `source_metadata`
retains archive/game URL, UUID, original time class/control, rated/rules and a
canonical full-source JSON SHA-256 fingerprint. Metadata values are JSON-encoded
strings in immutable key/value tuples. No response body or transport exception is
included in an error message.

`source_revision` is an explicit caller-supplied positive integer acquisition
label. It is not inferred chronology or a server revision. The durable coordinator
owns assigning revision sequences and quarantining changes under a known stable
ID. Compare PGN and payload fingerprints; an equal revision label alone is not a
duplicate proof. Refetching a current month returns the same IDs and emits changed
PGN/digests again. Identical duplicate rows within one response are accounted as
`duplicate_source_id`; conflicting rows with one ID fail that whole month rather
than silently selecting a winner. No stateful cross-call deduplication hides
corrections.

## Checkpoint and error contract

The first call freezes the validated, sorted, deduplicated listing and processes
at most one bounded slice of one month. A successful `GamePage` has `saturated=False`; `complete=True`
means the observed listing's requested window is exhausted. An empty legitimate
listing or month can complete. Completion does not seal a mutable provider month:
start a later overlapping poll with `continuation=None` and deduplicate by stable
IDs in the owning coordinator.

`continuation` is an opaque, versioned token binding account, UTC bounds, filters
and revision label to the frozen remaining months. It contains no credentials and
is an internal checkpoint, not an authenticated public capability. A malformed,
cross-scope, invalid-month or out-of-range token is rejected before HTTP. All
archive URLs must exactly match `https://api.chess.com/pub/player/{account}/games/YYYY/MM`;
cross-host/account URLs, userinfo, ports, query/fragment, invalid dates, redirects,
HTML, malformed JSON and absent/wrong archive arrays cannot become empty success.

Persist the **whole returned page and its next continuation atomically** in the
owning coordinator; only then request that continuation. If persistence fails,
repeat the original input. The adapter does not commit anything. If month two
fails after month one was saved, `ChesscomAcquisitionError.continuation` retains
the saved input token for month two, without listing or replaying month one. Its `complete=False`,
static `code`, `status_code`, `retryable` and validated `retry_after` describe the
failure. A listing failure uses `continuation=None`. A failure during a first
month retains the original `None` continuation. No partial page escapes.

429 and supplied numeric/HTTP-date Retry-After are returned to the caller; there
is no inline retry or sleep. Timeout/network failures are sanitized. Cancellation
callbacks are checked before HTTP, during bounded reads, between records and
before return. Callback cancellation is typed `code=cancelled`; native asyncio
cancellation propagates after response cleanup, with a cooperative yield every
16 records. Neither advances a checkpoint.
Socket timeouts are explicit (greater than 0, at most 15 seconds); they are not a hard total deadline.
The caller can cancel the task to enforce its own total budget. Bodies above the
configured maximum fail incomplete; increase a lower configured bound up to
32 MiB or subdivide/manual-handle the archive rather than checkpointing it away.
Archive listings above 1200 entries are rejected explicitly.

## Qualification

The regular owner suite is `make python-file FILE=backend/tests/test_product_chesscom.py`.
It uses disposable fake HTTP only: exact two-month ID sets/timestamps, boundary
filters, restart after failure, stable-ID overlap/corrections, provenance,
legitimate empty archives, typed bad URL/HTML/JSON/timeout/rate-limit/cancellation
and import-boundary checks. There is no live account, database, browser or
deployment qualification. T08/#489 owns durable checkpoint/retry cadence;
T10/#491 owns product composition. See the canonical [boundary drawing](README.md).

## Task D bounded month slices (prepared source)

`fetch_chesscom_page` additionally accepts `max_page_records=100`,
`max_page_positions=10000`, `max_page_bytes=8388608`. These bound emitted pages,
independently of the existing whole-response HTTP bound. At most one month is
requested per call, and at most the record limit's raw rows are considered in a
slice, including exclusions. The existing legal-game validator supplies move
counts; a record crossing a page budget stays unconsumed. A single record above
the limit fails explicitly as `record_exceeds_page_budget`; it is never skipped.
Serialized provenance is conservatively bounded below the coordinator's 16 MiB
ceiling, reserving space for the bounded cursor and rejection metadata.

Version 2 cursors retain the account/window/filter/revision binding and frozen
month listing, plus the within-month offset and canonical identity/content
fingerprint. Reordering an unchanged archive does not restart it. Changed
content replays that month from zero inside the same frozen job; stable source
and effect identity prevent duplicate logical effects. Snapshot identity stays
in the cursor, never in source-record metadata. Conflicting duplicate source IDs
within the requested window are rejected before any slice is emitted. Version 1
cursors resume at their existing month boundary and then emit version 2.

Errors retain the original input continuation, including a failure on the first
month. No partial page escapes. Later-visible games or months missed by the
frozen listing require a new reconciliation pass over the configured history.
A continuously changing month may replay repeatedly; progress/completeness is
not claimed until a pass finishes. A response exceeding the configured bound
still fails without advancing. Safe fallback is retaining that job and using an
explicit manual PGN import; this change does not add a provider snapshot API.

`unfinished_game` now distinguishes the donor's unfinished/unsupported result
refusal from `invalid_pgn_or_player`. Neither creates a source record. Task B owns
whether exclusion reasons permit checkpoint advancement; malformed/account/
identity failures must remain unresolved. Regular executable acquisition proof
is in `test_product_continuous_sync.py` and the affected acquisition selection of
`test_product_game_sync_integration.py`; the older standalone suite named above
is absent at the Task D baseline.
