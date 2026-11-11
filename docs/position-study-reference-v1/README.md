# Tempo position-owned Study — reference implementation kit (v1)

**Scope:** preparation, interface hardening and disposable/pure reference algorithms only. **Not a production implementation, a deployable migration, or a live-backed test.**

Source pinned: `aaweaver-actuary/tempo` `main` at `ea68e5d7038c02b813e690584c022bf5f0d9edb6`, as reviewed November 2, 2026. At implementation time re-read the current SHA and source-owner README.

Coordinator: https://github.com/aaweaver-actuary/tempo/issues/832  
Frozen design: https://github.com/aaweaver-actuary/tempo/issues/847#issuecomment-6578135210  
Review findings: https://github.com/aaweaver-actuary/tempo/issues/832#issuecomment-6577329278

## Quick verification (no database or network)

```sh
cd tempo-position-study-reference-v1
python -m pytest -q tests
node --test browser/*.test.mjs
python -m compileall -q reference
```

**Validated in this preparation environment:** 26 Python reference tests and 4 Node reference tests passed on Python 3.13.5 / Node 22.16.0. This validates the *local reference functions*, not `Tempo` native integration, FSRS 6.3.2, PostgreSQL, or legal chess replay. `python-chess` and `fsrs` were unavailable in this container (network install blocked), so their actual semantics need targeted tests in Tempo's own environment. The intentionally small synthetic test witnesses are *assumed source-certified*; the authoritative source must independently perform legal replay.

## Existing product authority is reused

1. **Repertoire:** existing `RepertoireSnapshotPort.snapshot(repertoire_id)` and pure `PositionIndex` supply source-approved learner positions and authored routes. Source and conflict/currentness proofs live with Repertoire, not Study. We do NOT invent a second persistent graph, source database, or route service.
2. **Study/Learning:** owns `PositionKey = (repertoire_id, variant='standard', canonical_position_key)` FSRS, due state, ledger, grade receipts, recovery and attempts. The approved response/policy version is a **separately fenced definition**. Source edits that retain the same answer do not discard the learning state.
3. **Composition:** a bounded pure route-option adapter and pure presentation planner combine known legal authored paths with due targets and previously saved fully clean prefix certificates. The selected attempt plan is frozen at issuance by Study. The graph and scheduler never import each other's implementations.
4. **Browser:** renders only a server-issued plan; it neither predicts due status nor chooses authored routes or synthesizes reviews.

## File classification

| Category | Reference files | Grounding / limits |
|---|---|---|
| Executable pure values / policy | `reference/contracts.py`, `grades.py`, `planner.py`, `graph.py`, `source_change.py`, `game_mapping.py`, `metrics.py`, `owner_epoch.py` | Local tests pass; integration requires real source/DB authority. |
| Source adapter proposal | `reference/source_adapter.py`, `ports.py` | Structural reference to existing `PositionIndex`, not tested against live source. |
| FSRS donor/seed dry run | `reference/donor.py`, `migration_preflight.py` | JSON validation/re-keying tested; *must* validate FSRS 6.3.2 decoder, unique card IDs, entire persistence transaction in repo. |
| Issuance / due read | `reference/issued_plan.py`, `due.py` | Pure examples; Study issuer must fence and persist under real PostgreSQL locks. |
| Browser payload | `browser/issued_plan.mjs`, `offline_plan.mjs` and tests | Node payload validation tested; K1 must use the project's existing TS/Zod wire owner, not add a second parser to production. |
| SQL reference | `sql/*.sql` | Not live migrations; `_ref` SQL tables are deliberately inert/example-only. Never execute against production. |
| Integration recipes | `recipes/WORKER_INDEX.md`, `recipes/TRANSACTION_AND_ROLLOUT.md`, `recipes/AGENT_HANDOFF.md` | Worker-specific paths, exact call sequence/stop and anti-coupling checks; senior tasks intentionally remain integration skeletons. |

## Key semantics already decided

- First genuine correct unassisted response when **due at issuance** may earn **one Good**, only after that position's complete saved Study attempt confirms that first response; same local day never gets another Good. A later positive from a stale position revision is retained only as observation.
- Any first genuinely wrong response, including an incidental non-due context move and a new later mistake after that day's Good, earns an **Again as soon as server-verified negative evidence is durably saved**. An `alternate`/`unverified` move must *not* be graded wrong until verified; guided correction or duplicate outbox send cannot schedule again.
- An earlier clean prefix is eligible to skip only if a **fully complete, saved, unassisted, entirely clean exercise** proves the exact same authored moves up to the cut. An otherwise failed exercise can produce some correct first-response observations without earning a prefix skip. If proof is absent, show a safe full line or hold/defer.
- Exact transpositions reached through different opponent move orders share a schedule, but the route selector chooses one deterministic authored route for the attempt (no alternate-path search across edges). Approximate similarity is never position identity.
- One physical card's existing FSRS memory parameters may seed several individual position records in a one-time **shadow** migration, with a distinct allocated internal FSRS ID for each, one complete donor per position, and retained original card history. Not an automatic promotion to learned state without verified source/intro facts.
- For a v1 online-only position-cohort pilot, reject new offline-issued position attempts explicitly. Legacy offline card mode continues unchanged until offline reconciliation passes.
- R11 physical recombination must not run on position-owned cohorts. Games first-departure original effect identities, urgent remediation and settlement remain owned by existing Study/Games contracts.

## Important remaining implementation checks (not decisions to invent)

- Current Repertoire index's `complete` property can also depend on physical-card mapping diagnostics. An adapter that uses it wholesale may conservatively hold source paths despite valid graph legality. The Repertoire owner must expose/verify a suitable scoped *authored route currentness certificate* without weakening content/conflict requirements.
- Native `FSRS` card-ID allocation/re-keying and its compatibility with imported `Card.from_dict` must be tested against the version actually installed. The reference `clone_state` only validates/changes a JSON field.
- Immutable issue/grade/queue receipts, lock ordering, source-edit rollback, pending legacy attempts, and Games settlement require the *actual* selected database adapter and transaction owner. Never lift the SQL example directly into a migration.
- Current live installed source, schema, donor candidates, position count, and real user test outcomes were not inspected in this work. Read-only E1 preflight and independent N2 acceptance remain gates.

## Cost-conscious first execution wave

A1 contract reviewer (one senior) plus A2/N1 fixtures and E1 read-only inventory design (two/three cheap workers) first. Once frozen: B1/B2/C1/C3, source-change M2, E2 donor picker and metrics N3 can be dispatched independently. Avoid launching 34 worktrees at once. Combine adjacent source-owned pure leaves into cohesive PRs when it *reduces* integration overhead; never combine two different scheduling/DB writers.

## Not a substitute for issue instructions

The *current* coordinator and worker issues remain canonical; the code in this kit is a reference and must be reconciled with newer source, generated migrations, current test gating, external correction requirements, and independent review. Workers must copy/adapt only the file they own, with explicit source proof and focused tests. Do not merge the kit wholesale into the application.