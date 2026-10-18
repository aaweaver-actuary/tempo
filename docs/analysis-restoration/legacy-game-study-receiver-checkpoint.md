# Legacy game → Study: receiver checkpoint

Continuation began 2026-10-18 18:29:23 UTC, with a 19:29:23 UTC checkpoint bound.
This follows the [initial checkpoint](legacy-game-study-checkpoint.md) and the
independent ambiguity review of draft PR #311. All inputs are synthetic. No
HTTP intake, Workbench mutation, queue delivery or real Study review is exposed.
The task evidence records final checkpoint time, exact head and commands.

## Current contract and owner boundaries

Game Evidence artifact 3.0.0 adds explicit wire v3; v1/v2 remain versioned and
their schema exports are unchanged. Shared observation linkage moved into one
validator. V3 accepts only an explicit real-game-miss obligation or exact
revision-targeted withdrawal. It carries target/workspace/repertoire identity,
source and catalogue revisions/digests, graph generation/digest, physical
card/revision/full executable digest, route/decision offsets, separate normalized
game-content/raw-input digests, and known occurrence/capture timestamps.
Capture time is provenance, excluded from authority identity.

`LegacyAuthorityToken` and source/graph/decision identity helpers belong to the
contract package. A bridge and backend import that artifact, not each other's
service implementation. The read-only observation translator remains separate
from delivery creation. `LegacyAcceptedDecision` and `LegacyAuthority` extend
the existing remediation port; no Content publication or activation is forged.
The token maps to a namespaced `legacy-graph:` internal authority storage key
without changing the existing 12-table schema. Existing contribution, semantic
incident, frontier, receipt and qualifying-review settlement policy is reused.

The repaired comparator retains all authored alternatives independently of
physical bindings. A same-policy route lacking a binding, or a revision-2 route
unsupported by this adapter, prevents an asserted physical-card miss. The
backend independently verifies those complete facts before shared semantic
validation; an otherwise well-formed forged miss is rejected. The original
`88a1fdf` comparator reproduced the false `...Nf6` versus `...a6` miss. Retained
red/green evidence covers missing and incompatible bindings.

Source identity hashes only declared source/executable authority fields. FSRS,
due dates, ordinary reviews/queue history, names, capture time and introduction
markers/dates do not change identity. Introduction remains an independent
eligibility check at preparation and acceptance. A date-only marker is known
before a game only after that entire UTC day has ended; same-day or missing
chronology is unsupported. Ordinary introduction of another card cannot strand
an existing obligation by changing its source key.

## Backend migration, transaction and coexistence

Backend migration 050 resolves the installed remediation 0.1.0 schema resource
through the ordered migration owner, verifies its exact SHA-256 before opening
a database, and includes the unchanged 12 module tables in `public`. Direct
execution of the wrapper without that resource fails before recording version
50. No runtime-core tables, schedules or histories are migrated. One additional
backend metadata table, `legacy_learning_identity`, creates a persistent random
logical target and fixed backend workspace `legacy`. It is not a second frontier
or scheduler. A game/repertoire ownership index supports coexistence queries.
This is code only; live schema remains 49.

The worker registers `game_evidence.receive_legacy` with the existing command
gateway prepare hook. Reads are paged at 64 rows and bounded to 4,096 rows per
source category; each background read section closes before replay/hashing.
Before/after repertoire/catalogue/graph/integrity generations must agree. A
successful or stale immutable module receipt can replay without recapturing
changed source. Raw source digest is retained as producer provenance; backend
verification independently binds the canonical imported-game identity/content.

Acceptance borrows the existing Celery command writer transaction. It reserves
the game/prefix transition, locks affected physical cards in sorted order before
the remediation lock, fences repertoire/catalogue/graph/integrity authority and
the exact imported game, and rechecks availability/introduction chronology.
Shared source validation applies even to withdrawal-only messages. Foreground
contention defers the command; acceptance does not issue a queue item or review.
The existing operation-receipt owner remains unchanged. No HTTP endpoint, write
guard, authentication allowlist, security setting or independent runtime write
path is copied into the backend. Durable backend operation/module atomicity is
still a native qualification gate, not established by fake-writer tests.

At the first durable accepted bridge contribution, retained module provenance
owns `(legacy workspace, canonical game, repertoire)`. The old PostgreSQL
outstanding-miss predicate excludes that scope, including after withdrawal.
Unknown sibling observations cannot fall back to weaker legacy admission or
the old review-after-game settlement rule. Other games/repertoires retain their
old path. This code is unexposed while combined admission and clearing are
unfinished. Existing issued attempts and histories are preserved.

## Explicit unsupported boundaries and remaining integration

The fixture maps original black physical card `physical-black`, revision 1,
full six-ply executable including opponent prelude, to interior decision offset
5: game `g8f6`, expected `a7a6`. It never creates a suffix card. Revision >1,
multiple physical owners, foreign-policy overlap, unknown catalogue color,
unready/unclean graph state, uncertain introduction chronology and nonempty
canonical repertoire prefixes fail closed. In particular, the current prefix
adapter cannot be claimed applicable to a selected repertoire with a prefix.
Do not expand the game sample to evade an unsupported/no-miss result.

Remaining seams are concrete:

- Producer delivery/ack polling and authenticated legacy HTTP → Celery dispatch.
- Current authoritative physical-card projection into the shared miss predicate,
  normal prelocked queue admission/clearing and truthful queued/deferred state.
- First actual issuance atomically binds the accepted incident frontier before
  origin stamping. Prefetch and already-issued/offline attempts never bind later.
- Normal review completion, reconciliation and invalidation call shared
  qualifying-review settlement with real attempt authority. No weaker legacy
  settlement may handle bridge-owned work.
- Read-only target inspection and small Workbench identity/move/currentness/status
  display, including a protected-head explanation; review stays in normal Study.

Protected heads, frozen order, quotas and unrelated history remain existing
owners' responsibilities. No second scheduler/frontier store, wholesale Content
or history migration, cooldown-B code or runtime-core adoption is required by
the implemented seam. These are integration gates before actual priority delivery.

## Verification and limits

Latest source-only selections: 99 Game Evidence, 32 legacy translator, 79 shared
remediation, 79 backend migration/authority/transport, and 15 v3 model cases pass.
The backend run emits 291 dependency deprecation warnings. Two v3 JSON Schema
fixture cases were explicitly unrun: available environments lack `jsonschema`;
the full contract suite cannot collect for that same missing dependency. The
component checker also cannot load missing `typescript`. Neither result is a
passing qualification. No dependencies were installed, no environment built.
Migration inventory reports 50 and whitespace checks pass.

Five selected module PostgreSQL cases collect, including the synthetic legacy
variant's outer rollback, immutable receipt replay, prior-attempt fence, later
qualifying module review and invalidation. They were not executed. They would
not establish genuine normal Study completion. Native backend migration/role,
capture locking, command/module atomicity and coexistence need their own proof;
installed wheels/sdists/locks and complete v1/v2 exchange remain unqualified.

Resource coordination messaging was rejected by automatic approval review; the
parent instructed this task not to retry. Exact rejected call and denial are in
task evidence. No native/build/browser window ran. The parent receives a finite
resource plan instead. Live services/data, retained 80-game viewer, B, primary
checkout and other task environments remain untouched.

The original 8–12 working-hour total is now 10–14 working hours plus resource
and reviewer waits, with low confidence until native acceptance/coexistence and
the selected repertoire's prefix applicability are resolved. This estimate is
conditional implementation/qualification work, not a deployment promise.

## Independent review: packaging and withdrawal repairs

The production backend image already installs the remediation/contract artifacts.
The maintenance image previously installed only backend requirements, so migration
discovery failed even against a fully applied/no-op history. It now installs just
the module and contract artifacts through their own packaging routes; its schema
resource remains version/checksum checked before any database access. No runtime
core is added to maintenance and no image build has qualified this change yet.

Withdrawal-only preparation now captures a small backend target/canonical-game
header. Acceptance rechecks that header and exact retained contribution lineage:
workspace/target/repertoire, game/evidence IDs, provider, provider_game_id,
content_hash and expected obligation revision. A deleted graph/card/game does
not prevent withdrawal of a proved retained incident. Where the imported game
still exists, its provider identity must agree and any change between preparation
and acceptance is stale. An explicit withdrawal names the retained accepted
content hash even after live content correction. Cross-hash correction therefore
uses a separate withdrawal envelope; a mixed envelope cannot claim two source
headers. The common module still owns source/revision/withdrawal effects.

The two defects were reproduced with nine failing source cases against d7f1fb5a.
Backend path tests exercise these guards with synthetic SQL rows, not merely a
mocked module source callback. Native database/receipt durability and actual
image build qualification remain unexecuted. The coordinator's later aggregate
applicability evidence identifies global catalogue-size and integrity-completion
blockers; this repair does not change capture limits, integrity policy or live
state. A bounded capture/integrity design is pending review before integration.
