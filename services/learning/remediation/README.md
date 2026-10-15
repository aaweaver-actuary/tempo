# Learning remediation 0.1.0

An installable Learning library, not a service, queue writer, or production
activation. It depends on the installed game-evidence 2.0.0 contract and borrows
the caller's psycopg connection (3.3.6 in native qualification). Existing Learning
review 0.1.0, scheduling 0.2.0 and chess-core 0.1.0
remain the owners of their existing behavior; no scheduler is copied here.

## A / C / F handoff

C produces `tempo_game_evidence_contracts.v2` using the committed fixtures.
`obligation_id` identifies a source-owned incident contribution;
`(obligation_id, obligation_revision)` is immutable. Every source revision change
that changes that payload needs a new obligation revision. `evidence_id` remains
bound to one game. Corrections explicitly withdraw an exact current obligation
revision; omission, including in a higher or partial batch, never withdraws.
Withdrawal-only deliveries have empty observation lists. A source deletion with
many contributions uses bounded explicit withdrawals, not a silent replacement.

A supplies authenticated `ReceiveContext` independently of request declarations,
and a `ContentAuthority` backed by accepted, active, fenced Learning definitions.
The authority maps route aliases to canonical card IDs and supplies the complete
accepted alternatives at the specific position/decision. This library validates
the accepted fact's workspace and explicit allowed aliases as well as the
decision position, publication and card revision. An unrelated incoming card ID
cannot become an alias merely because a lookup returns a valid definition.
The library validates
the coherent observation against those facts; C remains responsible for source
game computation, and A for authentication/current source authority. These ports
do not establish current production adoption of the Content contract.

A owns connection/transaction lifetime. Construct `PostgresRepository` only
inside an explicit outer PostgreSQL transaction, using the Learning runtime
connection and trusted Learning schema search path. It never opens, commits,
rolls back or closes connections. Exceptions after writes require the caller to
roll back its transaction/savepoint; a healthy separate caller-owned transaction
may record a truthful failure with `record_status`. `completed` acknowledges
durable acceptance, not completed studying or successful queue admission.

A acquires encompassing card/attempt/queue locks BEFORE calling mutating module
operations. B then acquires a workspace-scoped remediation advisory lock. A's
`AdmissionPort.apply` is a prelocked capability: it rechecks current content,
quota/override, queue dedup, publication and issued head without acquiring new
encompassing locks. F must adapt A's published runtime seam, not call a second
writer or privately replace the legacy miss predicate. No such production
adapter is included here. Background callers honor foreground admission and
yield between bounded transactions, with no provider/network work under locks.
Background callers construct the repository with `background=True`; its module
lock is nonblocking and intake returns `retryable_failure/foreground_busy` when
occupied. A's durable command/outbox retains retry intent; a rejected lock does
not claim durable obligation acceptance.

At initial authoritative attempt issuance, A calls `bind_attempt` in that same
transaction. It must not bind an already-issued attempt retrospectively when
receiving its review. Retries return the original frontier. A calls `settle`
alongside the actual review/history/schedule/receipt transaction after verifying
controlled outcome and current attempt authority. Only unaided correct study
reviews qualify. Again, guided, failed, invalidated and pre-issuance outcomes do
not. Review invalidation calls `invalidate_settlement` in A's transaction.

Logical work identity is canonical `(workspace, game, ply, card)`, with immutable
semantic versions (position, observed action, accepted alternatives, content
identity and occurrence time). Equivalent reanalysis and aliases reuse their
work sequence, even under new transport/obligation IDs. Meaningful corrections
receive a new sequence. Distinct games or plies remain distinct provenance.
Settlement records cover only a bound sequence frontier and compatible content;
they never update all incident rows. Late imports require a fresh bound attempt.
Unknown times may settle only after durable acceptance precedes issuance.

`pending_page` scans 16 card identities, including rejected/settled cards, and
returns the last scanned cursor. `prepare_admissions` scans at most four pages;
the caller persists its returned cursor with its durable job. Missing facts and
rejected pages still advance. Start a later sweep at the empty cursor to discover
new arrivals behind the previous cursor. Each application invokes one existing
writer. New cards use introduction; studied/future-due cards use review promotion.
The real-game exception bypasses ordinary cap/progression only, including zero
allowance; it cannot bypass validity, revisions, burial, retirement or deletion.
Ordinary candidate input order, including frozen ranks, remains authoritative.

Old game/content revisions remain visible as outstanding retained provenance,
but `has_current_miss` fences them from gameplay admission. A raw game revision
change does not silently withdraw omitted facts; C must publish current support
or explicit withdrawals. This also prevents stale source facts from granting a
curriculum/quota override through an otherwise current card proposal.

`migrations/001_remediation.sql` is a packaged, module-local migration input. A
registers it in the owning ordered migration system. F adopts artifacts only
after both owners land. It has no global number, live migration, legacy source
backfill, HTTP receiver, settings switch, or FSRS/history writes.

## Owning checks

Install only this component with `uv sync --locked --no-editable` from this
directory, then `uv run --locked --no-editable python -m pytest tests`.
`integration/run_postgres.py` runs the actual repository on a uniquely owned
disposable PostgreSQL fixture and refuses external database targets. See
REGRESSIONS.md and VALIDATION.md for executed identities and limits.
