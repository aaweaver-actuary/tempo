# Ingestion owning regressions

Regular discovery: `uv run --project packages/game-ingestion pytest packages/game-ingestion/tests`.

- `test_ingestion.py`: exact source digest and explicit identities/revisions;
  deterministic one-read input; bounded file input; strict PGN/result/player/
  variant/encoding/starting-position rejection and 1000-ply bound; from-position castling, promotion and en passant through
  shared replay; actual mainline versus variations; unknown date-only time,
  timezone normalization and compatibility normalizer shape.
- `test_independence.py`: installed `python -I` ingestion succeeds with comparer,
  classifier, actions, Game Evidence, backend and Learning implementations absent;
  installed file CLI emits canonical JSON without changing or creating files;
  invalid CLI input reports failure without success JSON.

Game Evidence retains its existing normalizer/replay and comparison corpus tests
as the consuming compatibility proof; those are not replaced or claimed run by
this package's owning test suite.

- `test_lichess.py`: explicit one-request public endpoint/window/limit scope; canonical identities; no false completion at a full limit; reject invalid/duplicate/out-of-window/unsorted/malformed/oversized pages; report HTTP/rate-limit/timeout without retry; default transport exact public URL, ambient-proxy refusal, public-only headers, bounded chunk reads, elapsed-budget stop/response cleanup and redirect refusal; injected public CLI success/failure. The installed independence proof covers this adapter with synthetic HTTP and downstream implementations absent.
