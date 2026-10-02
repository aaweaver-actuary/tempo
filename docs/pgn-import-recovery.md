# PGN import recovery and payload evidence

## Confirmed client defects

The previous PGN command treated an `unknown` receipt as permanently pending,
so selecting the matching file again never reconstructed or resubmitted it.
It also resubmitted a known `pending` operation and treated other active states
as errors. The dialog reported ordinary asynchronous confirmation as a red
error and sent it to debug notifications.

The named regression `unknown PGN receipt replays the exact file and settings
with its original operation ID` failed on main revision
`92f9aeacdf07bd1b61931ec1ca90be40c3a1619d` before the behavior changed. It passes
with the fix. This establishes a client recovery defect; it does not establish
why the original production message was not delivered.

## Receipt behavior

| Receipt | Result of a deliberate Import / Check again action |
| --- | --- |
| complete | Validate the result before clearing browser state; return it without POST. Invalid completion retains recovery identity and reports a diagnostic. |
| failed | Clear browser state and report the backend error. A subsequent deliberate action may start a new import. |
| blocked | Retain identity and show the last backend error. Only Retry blocked import calls the existing retry endpoint with the original identity. |
| queued, executing, retrying, pending | Sequentially poll the original operation without sending another PGN. |
| unknown | Resubmit matching file/settings once per action using the original Idempotency-Key. A still unresolved response remains recoverable on another action. |
| Unreadable / unavailable status | Retain identity and show informational confirmation guidance; never infer unknown from a failed read. |

The existing browser key `tempo-pending-pgn-import-v1` and fingerprint remain
unchanged. Identity is stored before submission; UUID creation occurs in one
place only when there is no unresolved record. A different file or settings
must first resolve the previous receipt. A previous completed import permits
the new selection; a previous failed import reports its failure before another
deliberate attempt can start.

Confirmation has a 30-second budget from the first network request, with
one-second sequential polling. The deadline aborts slow network requests and
fences late completion. Closing the dialog cancels client confirmation. The
server operation may continue. Ordinary timeout shows an informational Notice
and Check again; blocked and actual failed states retain error presentation.
File/settings and duplicate submission are locked while checking. Completion
still waits for graph, integrity, and queue publication before showing the
existing summary.

Recovery after closing or reload requires retained browser storage and
reselecting the same file name, bytes, color, and depth. Clearing storage loses
that client identity. An unreadable record or malformed complete result needs
service/browser diagnosis; the client does not discard it to manufacture a
new import. This PR adds no API or persistence-schema change.

## Payload diagnostic

Run from the repository root after installing backend requirements:

```sh
PYTHONPATH=backend backend/.venv/bin/python backend/benchmarks/pgn_payload.py \
  --pgn /path/to/opening.pgn --trained-color white --initial-depth 6 \
  --output test-results/performance/pgn-payload.json
```

Omit `--pgn` to reproduce the deterministic synthetic cases below. Repeat it
to measure multiple files. The diagnostic parses source and calls the real
`prepare_import_payload()`, using the configured Kombu/Celery serializer. It
does not connect to PostgreSQL or publish a task. Task-body bytes include the
command arguments and Celery body fields but exclude broker headers, Redis
base64 framing, and transport overhead. Counts describe expanded leaf lines,
including repeated inherited annotations; they are not unique source comments.

Measured October 2, 2026 on macOS 26.6.2 ARM64, Python 3.14.5, configured JSON
serializer, white / depth 6. Source revision was
`92f9aeacdf07bd1b61931ec1ca90be40c3a1619d` plus the uncommitted implementation
and diagnostic in this PR. No latency or delivery claim is made from these
size measurements.

| Fixture | Raw bytes | Games | Leaf lines | Expanded moves | Annotation occurrences | Segment IDs (prefix IDs) | Payload bytes | Task-body bytes | Payload/raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Available local `yusu1.pgn` | 80,110 | 312 | 312 | 2,693 | 0 | 536 (311) | 110,958 | 111,082 | 1.39× |
| comb-16 | 433 | 1 | 17 | 152 | 17 | 16 (12) | 8,026 | 8,150 | 18.54× |
| comb-32 | 765 | 1 | 33 | 560 | 33 | 16 (12) | 15,677 | 15,801 | 20.49× |
| comb-64 | 1,437 | 1 | 65 | 2,144 | 65 | 16 (12) | 37,117 | 37,241 | 25.83× |
| comb-128 | 2,782 | 1 | 129 | 8,384 | 129 | 16 (12) | 104,578 | 104,702 | 37.59× |
| Synthetic branching corpus near reported size | 426,502 | 615 | 4,907 | 96,780 | 4,292 | 12,247 (1,852) | 3,022,628 | 3,022,752 | 7.09× |

The comb repeats legal knight moves with one alternative at each ply and an
inherited root annotation. There are two source move nodes per ply, but each
leaf repeats its shared prefix: expanded moves are `N * (N + 3) / 2`. Segment
IDs remain heavily deduplicated while the serialized line list grows. The
426 KB case uses `benchmarks.pgn_parse.fixture(615)`, with 32-ply games and
deterministic branches every five plies. Neither fixture is a production file.

SHA-256 identities for exact reproduction:

| Fixture | SHA-256 |
| --- | --- |
| yusu1.pgn | `c882b808e26a22d5446d524e0c28959aea7b229348542902cee1a8b1bb1e8e84` |
| comb-16 | `d8e100965376dcda8b04b8028bd5ed8e5ec0afed2a4815dd36f7b5e83da56432` |
| comb-32 | `c3be2bc2674b6e820ba8980f2498dba7bbc32c2c2b3b1e1141bab228b2b815cf` |
| comb-64 | `91b2c0b7d5167c47eb4b4b194c961b001886d7485b4bda74ba2fd4d479bc0680` |
| comb-128 | `f71bd342bbb6cbf5eb83f9817ad43d918efc0b07c9aac52d47f6893946575858` |
| branching-corpus-425kb | `502c5c46cb52f165fda7cf1d156d79d546baae613232f41238f6a30d4bbc4425` |

`ruy_exported.pgn` was not available locally. These synthetic results are not
measurements of that file and do not prove its delivery failure. They warrant
a separate transport investigation: capture production publisher/broker/worker
evidence and compare raw-PGN transport with durable staging, including restart
and retention rules. Any redesign must keep PGN parsing outside PostgreSQL
write transactions. Transport changes remain outside this PR.

## Regression and validation scope

`tests/REGRESSIONS.md` records command, component, browser, deterministic
dispatch, PostgreSQL replay, and diagnostic test names. The real disposable
HTTP/Celery/PostgreSQL test replays the same PGN with one key before and after
service recreation. It checks the exact receipt/result, handler attempt
identity, and unchanged repertoire, line, card-link and card identities/counts.
Mocked dispatch tests establish payload determinism only.

Local evidence uses the isolated `codex/pgn-import-recovery` checkout on the
base revision above with this patch applied. CI owns final required validation
for the committed PR candidate. Local checks are focused evidence, not a full
gate. Exact commands and durations are retained in the PR validation record;
the affected recovery browser file, PostgreSQL durability, pinned visual checks,
units, typecheck, lint, and diff checks are required before handoff. No live
study database or stack is used for validation.
