# Study priority v1 — release and rollback

Candidate identifier: `study-priority-v1.0.0-rc.1`. Bind the final release and image
receipt to the full tested commit after required qualification. No version tag or
live deployment is created by this implementation task.

## Qualification before release

Keep the database/control, browser/inference and development-resource reviews
focused, and qualify their integration. Run the affected unit inventories, native
PostgreSQL six-case proof, real browser/worker cases, inference image checks and
bounded resource rehearsal. Preserve executed identities, source/fixture/toolchain
hashes, command lines, timing samples and verified cleanup outside disposable
checkouts. A failed, missing, cancelled or zero-selected required check is not a pass.

Required commands include the affected backend/frontend/runner selections in the
validation report and these owning integration boundaries:

```sh
node scripts/check-merge-conflicts.mjs
node tools/component-build/test-lane.mjs controlled-browser
node scripts/test-controlled-browser.mjs priority.spec.ts --project chromium
node scripts/test-controlled-browser.mjs interaction.spec.ts phone.spec.ts --project chromium
node --test tests/runner/inference-priority.test.mjs
node scripts/check-inference-priority-images.mjs
node scripts/development-budget.mjs backend/.venv/bin/python database/tests/check_postgres_study_priority.py
```

The regular PostgreSQL study/schema scenarios invoke the native proof on a marked
private database. Its schema helper temporarily gets a larger allocation while
this invocation's application clients are stopped; PostgreSQL, Redis and the helper
remain below the aggregate development budget. Restore and verify those clients
afterward. Never stop another task's resources to make room.

The candidate requires schema 49: three receipt-recovery indexes and a version row.
There is no study-record backfill or live-data conversion. Follow Tempo's registered
lifecycle: verify backup/restore evidence, stop writers for the migration window,
apply the checked-in migrations, start the exact qualified image receipt and verify
readiness, schema and source identity. Live deployment remains a separately
authorized action after qualification.

## Short study pilot

Use the existing persistent Study-only setting first. Verify current and next cards,
ordinary introduction, a confirmed review, phone reconnection and receipt replay.
Then allow optional work, confirm actual analysis/preparation has started, and enter
study input including a held drag. Retain browser, network and server samples.
Check no duplicate schedule/history/evidence effects, no invented opening evidence,
no real timeout hidden as cancellation, and useful optional progress after quiet.

Initial acceptance targets remain: local cancellation 50 ms; optional server yield
250 ms after priority; browser interaction p95 100 ms; confirmed save p95 500 ms and
p99 one second; prepared-next after confirmation p95 250 ms. Do not relax thresholds
or durability assertions to qualify. A representative pilot supplements finite
synthetic measurements; it does not replace required CI.

## Rollback without discarding accepted study

Retain the previous verified image/source receipt (`0edf566a`, schema 48) and the
candidate receipt. First capture a current backup and stop study/application writers
and optional workers through the registered lifecycle, retaining PostgreSQL. Preserve
all pending browser intents and server receipts. Confirm there is no later migration.

Use `database/tempo_legacy_database/ddl/rollbacks/049_study_priority_recovery.sql` in the stopped-writer maintenance
window. It requires the complete schema-49 history, removes only the three new
indexes and version 49 marker, and leaves cards, reviews, scheduling, required evidence
and receipts in place. The native proof validates 49 → 48 → 49 with identical study
snapshots and accepted reviews. Do not run schema-48 binaries against schema 49 or
remove a later migration. Reapplication uses the normal migration 49.

Select and start the retained schema-48 image/source receipt, verify readiness and
resume a short study check with original immutable attempt IDs. Clear no outbox,
receipt or user data. Expiring new control snapshots are harmless to the older
compatible clients. Preserve the failure traces and both release receipts. An older
database backup is not the default rollback: it could discard post-release reviews.

If the schema guard, snapshot comparison or readiness check fails, retain the current
database and all browser intents and leave writers stopped for diagnosis. Do not
force a mismatched binary/schema pair or silently mark a save confirmed.
