# Retired activity metadata census — 2026-11-03

**NO SAFE DATABASE DELETION.** The initial census did not access live database or browser state, so its grouped counts, job relationships and receipt references remained unknown. The later bounded Safari reconciliation below covers only six browser records and their exact server identities. The 2026-10-30 diagnostic summarized in task 03 observed two unpaused `priority` activity rows, but it describes a different older installation and is not a current count. No exact eligible subset, expected count, allowlist or restore rehearsal is established. No mutation is proposed.

Source inspected: `aa7d0dfe2ef957c270ea4cefe15ebe26dfe9e834` after tasks 09/10. Activity progress/control and command recovery remain supported even though tray projection/routes were removed. In particular, `activity.control`, `activity.progress` and `activity.task.retry` remain registered; accepted operations may be recovered. An error response does not establish that an effect was not accepted.

| Candidate row class | Source provenance and consumer status | Pause / priority impact and receipt references | Decision |
| --- | --- | --- | --- |
| `background_activity` for `priority` and other unrecognized sources | `priority` was historically observed; it is absent from the seven-source current lookup. Absence from that lookup alone does not establish absence of every producer, consumer, historical dependency, or installed-code use. Other unknown sources require their own provenance. | Per-row flags and pending command references unknown. An old row could contain a control. | UNKNOWN; retain |
| `background_activity` for `durable`, `sync`, `derivation`, `integrity`, `coverage`, `game_analysis`, `threat_analysis` | Current control/progress implementation maps these to active owning job tables. Claim and control paths consume flags; some producers continue to emit progress. | Removing a pause may make work claimable; promotion affects ordering. Accepted activity commands can recover; row-specific references unknown. | RETAIN |
| `background_tasks`, including `daily_queue`, feedback, discovery, explicit-import and optional kinds | Durable dispatch and recovery remain active. `origin_import_operation_id` identifies receipt-bound preparation. No per-kind or per-row absence proven. | Controls can affect claim eligibility; task events reference tasks with cascade deletion. Uncommitted receipt references unknown. | RETAIN; no generic task deletion |
| Sync, derivation, game analysis, integrity, coverage and threat jobs and related data | Active producers, recovery paths, FKs and other consumers remain. Some contain user-paused work and evidence. | Controls may affect eligibility or ordering; receipt associations unknown. | RETAIN |
| `discovery_admission_intents`, task events and `operation_receipts` | Intent acceptance, task enqueue and opportunity marker share a transaction; operation receipts support replay and recovery. Effects can exist despite an error. | Receipt and intent relationships must be reconciled individually; unknown now. | RETAIN |
| Browser `tempo-pending-discovery-admissions-v1` and `tempo-pending-activity-control-v1:${source}:${workId}` | Durable client outboxes retain operation identities and are consumed by recovery. Current contents unknown. | Deletion can lose pending or accepted operations or alter control recovery. | RETAIN pending reconciliation |

The 1,758 position seed holds are not abandoned jobs. Study reviews, attempts, cards, schedules, queues, issued plans, accepted game effects, offline journals and backups are outside this cleanup.

The companion [read-only census](retired-activity-dry-run.sql) has a three-second per-statement timeout, groups activity and task state, checks source-specific logical job associations and receipt populations, previews at most 100 `priority` exact keys, and rolls back. Its checked-in table/column names follow the legacy PostgreSQL DDL; it has not been executed or validated against the installed schema. The import marker test is conservative text matching, not parsed identity. Grouped receipt counts cannot prove whether a particular row is referenced; truncated previews cannot prove completeness. Verify actual schema and database identity first and stop on any missing relation or timeout. Do not run against live infrastructure without separate authorization.

Before any future coordinator-owned removal, establish the exact producer/consumer and deployed-version census, all row-specific operation/acceptance/event relationships, current counts and exact IDs, and confirm no pause, priority or durable obligation can be affected. A verified backup and restore rehearsal, explicit allowlist, expected-count guard and appropriately scoped transaction are required under the common contract. No SQL for removal is supplied because no subset meets those conditions.

Source: `database/tempo_legacy_database/adapters/controls/activity/storage.py` (seven logical mappings), `database/tempo_legacy_database/adapters/controls/claim_sql.py` (control predicates), `backend/app/activity_commands.py` (retained command registration), `database/tempo_legacy_database/ddl/migrations/001_initial.sql` and subsequent migrations (schema), and task 02/03 receipts. Source facts do not establish deployed state.

## Bounded Safari reconciliation after the initial census

Read-only inspection of the existing Safari `http://localhost:3000` page found
six failed discovery records, all reporting `Active discovery not found`. A
2,121-byte exact-key backup was captured outside the repository; its SHA-256
matched Safari's read-only digest. A task-owned JSDOM WebStorage restore
rehearsal round-tripped those bytes and preserved an unrelated seeded key. This
is not a live Safari restore or permission to erase another journal.

An exact-ID PostgreSQL transaction on the identified installation (database
`tempo`, schema 58) used read-only mode, a one-second statement timeout and a
100ms lock timeout, then rolled back. It found zero receipts for the six browser
operation IDs, zero admission intents for their opportunity IDs and zero current
opportunities for those IDs. This establishes no current server acceptance or
recovery record for that bounded set; it does not rewrite historical effects or
prove other rows disposable. All browser records and server data remain intact.
No database metadata deletion is proposed. Installation and the exact-key
retirement still require the remaining release gates and applicable authority.
