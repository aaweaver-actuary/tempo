# Canonical repertoire prefix

An explicit opening prefix defines repertoire membership by exact move order.
Training retains the assumed moves; coverage and game feedback start after them.
Position-based transpositions remain supported after the boundary. A continuation
starting from a FEN must have a verified route from the assumed opening.

## Development validation plan

Changed boundaries: repertoire metadata, line/card writes, coverage, imported-game
comparison, discovery admission, introduction priorities, and the repertoire UI.
Risks: early off-scope gaps, disconnected continuations, stale previews/results,
shared-card edits, interrupted commands, and background/foreground contention.
Start with named backend scope and durable-preview regressions, then relevant
existing backend callers and component/command tests. On the settled candidate,
run a real PostgreSQL browser workflow, Docker durability, typecheck, lint, and
pinned visual checks. CI owns final required candidate validation. No tests or
development services may connect to the live study instance.

## Persistence and boundaries

Migration 026 stores UCI moves, a monotonically increasing prefix revision, and
an active compatibility preview per repertoire. Empty moves mean unrestricted
analysis. SAN and the endpoint FEN are derived from legal moves from the standard
starting position. Setting the same prefix is a no-op.

The compatibility task reads one line or card, closes its read connection,
validates the route, and commits one result under its current lease. Further
passes resolve continuations whose anchors appear later in the saved lines.
Conflicts retain their first disagreement; unresolved positions become explicit
conflicts. A source revision advances on changes to lines, card links, or card
moves/starts/archival. Saving locks the repertoire and atomically checks both
source and prefix revisions against the ready preview. Snapshot copy preserves
these source revisions rather than incrementing them during restoration.

The ending position and verified downstream routes anchor FEN continuations.
Discovery admission can also verify a new route from its coverage or imported
source game before entering the short write transaction. The admission rechecks
its lease, opportunity revision, and current prefix revision before inserting.
Shared-card edits check every affected repertoire. Cards, reviews, scheduling,
and prefix-training routes are never rewritten by a prefix change.

Coverage excludes positions before the boundary, retaining the configured
absolute horizon. Assumed opponent edges have probability one. Missing data
remains unknown. A prefix with no opponent positions within the horizon reports
an actionable failure rather than complete coverage. Exact-order, complete
membership filters games before position-based matching resumes after the
boundary. Imported games and their general analysis remain intact.

A change immediately hides old coverage/discovery/game-comparison publications
and retires the affected introduction-priority publication. Durable tasks rebuild
coverage, game feedback, opportunities, and priorities. Revision checks prevent
stale publications and admissions; foreground saves and opportunity publication
acquire repertoire metadata before refresh-task locks.

## API

All paths are beneath `/api/repertoires/{identifier}/canonical-prefix`:

| Method/path | Purpose |
| --- | --- |
| `GET` | Read normalized UCI, SAN, endpoint FEN, and revision. |
| `POST /preview` | Check one SAN `movetext`; return a durable preview identifier. |
| `GET /preview/{preview_id}?after=…` | Read pending/ready/conflicting/stale/failed state, a shared-opening suggestion, and paginated conflicts. |
| `PUT` | Apply the ready `preview_id` with `expected_revision`; a preview of empty movetext clears the prefix. |

PostgreSQL writes use the existing foreground command gateway and
`Idempotency-Key`. The browser preserves the original operation identity and
payload across interrupted delivery, pending receipts, and server failures.
Reopening recovers unfinished saves and previews. Suggestions require explicit
acceptance; no prefix is enabled automatically.
