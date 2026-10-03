# Tactic Capture MVP

## Development test plan

Changed behavior: foreground authoring and ingestion of tactic cards, capture
provenance, accepted game tactical misses, and source-neutral daily admission.
Boundaries: shared CardEditor controls, typed API/command receipts, SQLite
compatibility, PostgreSQL persistence, and the ordinary mixed Training queue.

Risks to prove: invalid or incomplete positions; illegal continuation moves;
duplicate/changed retries; cross-content or archived-card collisions; history
loss; quota races; finished queues; source-link assumptions; modal board input
leaking into Solve; and migration/import/restore fidelity.

Smallest proof first: reproduce the plural game-tactic defect in its existing
Python regression, then add focused capture service/API/quota cases and editor
and pending-save unit cases. Run affected caller files when coherent. Verify
real FEN and manual setup through the isolated PostgreSQL browser runner, then
pinned rendering and settled PostgreSQL durability. Use synthetic positions.
CI owns final current-candidate verification, including the complete browser
and pinned coverage selected by migration/shared-contract changes. No live
study database or product service is part of validation.

Fixture boundary: product browser setup must deactivate any packs left by layout
activation checks before archiving queued cards. Otherwise the active-card count
correctly admits replacement tactics into the next test's mixed queue. Prove the
activation/Study sequence first, then the affected Study and capture files; CI
still owns the complete browser matrix.

## Intended behavior

A capture is provenance; its card is the scheduled training object. New captures
enter today's queue after approximately four intervening cards. Exact tactic
matches reuse their card and preserve reviews. New captured tactics use remaining
automatic tactic-introduction capacity, but captures are always admitted even
after that capacity is exhausted. Existing admissions are never evicted.

The user authors the solution. Refutations, motif/latency analytics, transfer
probes, bulk extraction, engine verification, and a capture library are deferred.

## Persistence and queue semantics

`POST /api/tactics/captures` accepts a capture UUID, starting FEN, complete UCI
solution, source kind (`puzzle_rush`, `game`, `manual`, `other`), optional reference
and HTTP(S) URL, and note. The UUID is also the default Idempotency-Key; a supplied
key must match. PostgreSQL dispatches `tactics.capture.create` through the existing
foreground worker and operation receipt boundary. SQLite uses its serialized
foreground writer. A 202 is pending, never a success notification.

The small shared service validates a playable python-chess board and every move,
then uses the established card identity (first four FEN fields and complete
solution). It derives trained color from the starting turn. One transaction
creates or reuses the card, records the capture and normalized request/original
result, updates learning/due/light scheduling, and inserts a guided queue entry
with a capture priority reason. New cards belong to `__captured_tactics__` and
have repertoire membership. Reused cards retain ownership, historical
introduction date, reviews, FSRS state, and schedule counters. Archived,
superseded, validation-blocked, or different-content matches return a conflict.
A capture never fabricates a completed review. Multiple external encounters can
reference the same source without a global source-reference uniqueness rule.

The game tactical-miss branch calls this service only after its existing trust,
confidence, opportunity/version, accepted-move, and legal-line checks. Its stable
UUID derives from the finding ID, with game provenance and `__game_tactics__`
ownership for newly created cards. Other finding kinds retain their path.

Daily automatic capacity counts active, unsuperseded tactic cards introduced on
the queue date, regardless of source. Packaged introduction bookkeeping remains.
Capture and automatic publication acquire the daily admission lock before card
and queue-position locks; PostgreSQL rechecks capacity in publication.

| Case | Outcome with automatic allowance 5 |
| --- | --- |
| A: capture 3 before automatic admission | 3 captures plus at most 2 new automatic cards |
| B: 2 automatic cards, then capture 3 | Keep all 5; admit no further new automatic cards |
| C: allowance already exhausted, then capture 3 | Keep the 5 prior admissions and queue all 3 captures |
| D: allowance zero or queue finished | Capture still queues; finished cards receive a new guided cycle |
| E: recapture a tactic introduced earlier | Reuse its card; consume no new automatic slot |

New queue entries use the existing after-four placement helper. Existing queued
cards are handled through that helper; replaying the same capture returns its
saved result before any queue mutation. A completed queue can reopen. Refresh
uses the existing durable queue entrypoint, with no new analysis pipeline or
startup job.

## Schema and migration

Migration 025 adds `tactic_captures`, its card index and cascading card foreign
key, constrained source kinds, and normalized request/original result. It repairs
only known game-created plural `tactics` cards and their queue buckets, retaining
IDs, reviews, schedules, and archival state. SQLite has the matching compatibility
schema and recorded `tactic-capture-v1` repair. Migration snapshots preserve
capture events; PostgreSQL backup comparison checks every table and receipt.

Existing authoritative PostgreSQL installations require the normal stopped-write
migration procedure before running schema-25 services. This development work
does not upgrade the live study instance.

For a fresh historical SQLite import, keep the source snapshot read-only. Apply
schema migrations, copy, and perform the unchanged exact source/destination
verification **before** any destination normalization. Then, while destination
writers remain stopped, run `scripts/repair_verified_game_tactics.py` with that
same snapshot and destination DSN. The command repeats exact verification before
its first repair and records the source file SHA-256 in `internal_migrations`;
replaying the repair with those same bytes is inert. Preserve the exact pre-repair
verification artifacts, then verify a post-repair PostgreSQL backup against its
restored PostgreSQL copy. Do not run original-source equality checks on the
intentionally repaired rows or edit historical source bytes to make them match.

## Browser behavior

Capture accepts manual/FEN authoring or **Paste Chess.com puzzle PGN**. Paste the
puzzle export and click **Load PGN** to inspect and edit the position, solution,
and source before **Add to training**. Loading alone does not save anything.
Chess.com exports include a leading opponent/setup move: import advances that
move, shows the resulting starting position, and uses the remaining mainline as
the authored solution. The resulting side to move determines board orientation.

A puzzle needs an explicit FEN and either `PuzzleID` or a Chess.com `/puzzles/`
Link. `PuzzleID` and `Link` populate existing Reference and URL fields, Source
becomes Puzzle Rush, and Note is preserved. Other metadata is unused. Invalid
PGNs preserve the current capture; loading another valid PGN replaces it.
URL validation, persistence, scheduling, and pending recovery still use the
ordinary tactic capture pipeline. Arbitrary game-to-tactic PGN extraction is
outside this feature.

Capture opens on an empty board. Incomplete setup can be placed, removed, or
freely dragged; malformed FEN text never reaches the board renderer. A playable
position enables legal solution recording. Navigation can replace a continuation,
and promotion choice includes underpromotion. Starting-position changes require
confirmation before clearing recorded moves.

The browser persists exact request bytes and UUID before its first send. Pending
and uncertain saves lock editing across close, reopen, and reload. Retry checks
receipts and resends the same request when delivery is uncertain. Only confirmed
terminal failure unlocks editing for a new capture. Durable success invalidates
workspace data, signals the queue change, closes the dialog, and shows the new
or reused-card confirmation. The dialog traps/restores focus and isolates its
board from the underlying Solve attempt. Practice-demo capture explains that
local Tempo is required. Only packaged-owned tactic cards receive Lichess links
and original restoration controls.
