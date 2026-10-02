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

Migration 030 stores UCI moves, a monotonically increasing prefix revision, and
an active compatibility preview per repertoire. Empty moves mean unrestricted
analysis. SAN and the endpoint FEN are derived from legal moves from the standard
starting position. Saving the same prefix preserves its revision and refreshes
analysis after recertifying current routes.

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
its lease, opportunity revision, current prefix revision and source revision before
inserting. The additive admission establishes only its verified route against the
new source revision; it does not renew other historical certificates.
Shared-card edits check every affected repertoire. Cards, reviews, scheduling,
and prefix-training routes are never rewritten by a prefix change.

Coverage excludes positions before the boundary, retaining the configured
absolute horizon. Analysis reconstructs verified origins of FEN continuations;
only the assumed prefix has probability one, including after shortening it.
Saved training starts and moves remain intact. Missing data
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
# PR #66 review validation plan

The review fixes cover current-source route certificates, atomic SQLite card edits
and re-imports, unrestricted empty coverage, compatibility scan reuse/retention,
and confirmed persistence followed by a failed client refresh. Start with named
regressions in the canonical backend/component files and reproduce the defects
there. Expand to existing coverage, import, shared-card, discovery, priority,
comparison and PostgreSQL contract callers because these share scope validation.
Then run typecheck, lint and diff checks, the canonical browser workflow and real
PostgreSQL durability. CI owns the final complete candidate gate. Preserve study
history and the live instance; do not merge the PR.

## Review fixes and boundary audit

Migration 031 ties each position certificate to `scope_source_revision`. Unknown
legacy certificates default to -1 and fail closed until checked. Every origin
lookup joins the certificate to the current repertoire revision. Historical result
origins are reusable for prefix shortening only while their source revision is
current; a disconnected line cannot certify itself after deletion of its root.
A successful check of the already-active opening renews its certificate without
changing the opening assumption. After a source mutation, arbitrary-FEN additions
may need a fresh **Check prefix**. Saving that unchanged prefix refreshes derived
analysis. Unverified continuations produce an actionable coverage failure rather
than apparently complete partial coverage. Training/history remain saved.

Equivalent checks reuse a checking/ready/conflicting preview for the same
repertoire, normalized UCI moves, prefix revision and source revision. A failed
scan can be checked again. PostgreSQL serializes admission under the repertoire
lock. Existing command receipts remain authoritative across restart and replay.
The preview task then performs retention at lower priority: retire an obsolete
scan, delete one result/position/event per slice, and finally delete its empty
preview/task. Keep the newest eight previews plus the active certificate; storage
converges to that bound when the durable worker runs. Older unapplied tokens can
expire and require another check. There is no startup sweep or bulk deletion.

The production boundary audit covered imports, branches, paste, discovery
admissions, normal/shared card revisions and integrity repair. SQLite now guards
imports and every owner/link before card mutation; integrity line rewrites also
validate before writing. An integrity replacement belongs to the validated
repertoire, retaining the other shared owner's card. Graph generation and prefix
splitting derive subsets of existing source routes and cannot introduce a different
move order. Removal/archival cannot add a route; source triggers revoke their old
certificates. Tactical/finding capture writes non-opening content to its own
repertoires. Snapshot/migration setup is recovery, not a product admission path.
Analysis readers use current certificates; the opportunity reader's assumed keys
are derived directly from the current prefix because they do not depend on saved
source routes. Queued admissions recheck source state before their short insert.

The UI clears the command state on confirmed persistence. If workspace refresh
then fails, it says **Prefix saved. Reload the repertoire to see the updated
analysis.** and disables a stale save. General refresh coalescing (#40) and the
Discoveries feed scheduler (#33, now merged through #58) remain separate work;
no latency improvement is claimed here.
Retirement invalidates the token before deleting any child, fencing a concurrent
save from activating an incomplete certificate. The real PostgreSQL durability
scenario also submits ten distinct checks and verifies the retained-preview bound.
Reused ready/conflicting admissions are accepted by the typed frontend contract.
