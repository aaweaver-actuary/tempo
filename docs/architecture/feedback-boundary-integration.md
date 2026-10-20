# Feedback, queue and Discovery integration checkpoint

This is an isolated integration branch, not a merge/deployment authorization.
Inputs are pinned: feedback PR316 `1a7a8bd88404f77b9a4923b72ef96b75167de8ff`,
Discovery PR313 `215f8a27132ad2ad4e2efe46109eb6a5b687e7d1`, and queue PR314
`269ce9e1e78e9fa1f66758c04bf581658031a12f`. Feedback depends on PR311's
accepted legacy scope contract at `501f25e`; merged AGENTS guidance from main
`45aeb03` is included. Independent reviewed/source trees remain unchanged.

## Actual joins and ownership

The only production integration change rebinds two queue-adapter imports:
`outstanding_miss_query` uses `storage.real_game_miss_projection`, while the
connection-taking `has_outstanding_real_game_miss` uses
`services.real_game_feedback_adapter`. `MISS_REASON` stays in the SQL-free
feedback policy. No raw connection is passed to a policy port and no duplicate
accepted-scope query or conditional fallback is introduced.

- Feedback policy owns admission-versus-review decisions over typed facts.
  Its borrowed adapter owns persistence/locks and delegates to canonical writers.
- Queue admission policy owns membership/quota/frozen/capacity decisions; its
  adapter borrows the existing caller transaction and canonical lock authority.
- Discovery acceptance policy owns prepared-choice freshness/replay decisions;
  its store owns scope/opportunity queries and durable intent/task effects.
- Composition roots wire these implementations. Existing command gateway,
  queue/first-issuance writer and review settlement owners remain authoritative.
  Cancellation/retry do not create independently committing repositories or new
  queue/settlement paths. These owners are not replaced by this integration.

There were no merge conflicts. Both regression inventories were retained.
The import guard now resolves backend-relative imports, with a control preserving
Learning runtime's separate same-named module. A retained red identifies the
queue adapter's old relative import; an earlier absolute-only audit passed and
is explicitly not treated as proof of this relative seam.

## Focused proof and reuse

Current source proof includes157 feedback/contract cases plus9subtests and58
queue/Discovery owner cases. The latter run blocks socket connects/binds and
SQLite connections. The combined composition cases prove feedback store wrapping
from the queue adapter, queue-root argument preservation and Discovery's borrowed
store/semantic intent identity. They do not prove actual database execution.
Six native identities collect with0executed and no infrastructure.

The native accepted-scope case additionally exercises the public combined queue
adapter's actual PostgreSQL projection: an accepted scope is suppressed, a valid
miss in another repertoire still yields the unchanged priority reason, and an
excluded game yields no outstanding miss. Temporary admission-state changes are
restored with the caller savepoint before leaving the fixture.

Reuse is input-specific. Unchanged Discovery store/application source can retain
its independently recorded15-case PostgreSQL proof. Queue's independent native
result is owned/reported by its task; do not infer it from this integration.
Its lock/writer algorithms are unchanged, but the two changed feedback query
joins require the combined native case. Keep the existing three real command/
Study/recovery cases for transaction, issuance, settlement and replay wiring.
No whole-system, browser, engine or live/current-game reimport suite is proposed.

The shortest remaining native path is the existing same-head284 artifact
prerequisite followed by the six-case backend-native entry on this combined
snapshot, under the already-reviewed caps/deadlines. The producer library
sources are not changed by the joins; the prerequisite is retained because the
existing runner requires same-head qualified artifact/install receipts. It is
not evidence that all producer components changed, nor a replacement for the
focused fake-store policy checks. Native execution must wait for the parent's
serial resource release and clear temporary work before12:13UTC in the current
approved idle window. Preserve live Study unless an actual conflict is shown.

## Limits

No globally clean architecture claim: legacy durable queue adoption/read SQL and
the pre-existing remediation-to-command borrowed-connection dependency remain
explicit scoped debt. Native contracts and source review are required before
product acceptance. PR merges, deployment, live-data changes and unrelated
architecture rewrites remain excluded.
