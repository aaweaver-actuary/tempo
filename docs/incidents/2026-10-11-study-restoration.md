# Tempo study restoration, October 11, 2026

The live `tempo` project at revision `643f736b` and PostgreSQL schema 46 could not issue a usable card. The queue reader selected superseded entry 13051; the writer correctly selected eligible successor 13052 and rejected issuance of 13051. Repeated rejected commands completed quickly. Background transaction timeouts were also observed, but their contribution to this immediate blocker was not established.

The correction makes payload, count and head eligibility exclude superseded and permanently deleted cards while retaining publication, integrity, burial, revision and issuance guards. Queue rows and attempt origins are preserved.

Settings → Training → **Study-only mode** is installation-wide and becomes effective after **Save settings** completes. Migration 47 adds a false-by-default settings column; omitted fields preserve the stored mode. SQLite compatibility initializes the same flag.

While enabled, review saves, authoritative current opening completion, grading against existing evidence, issuance, daily queue refresh, settings and essential receipt recovery continue. Workers and the scheduler stay running. Optional engine/Maia claims, discovery, coverage, imports, derived analysis, maintenance and browser bulk repertoire preparation wait. Expensive accepted aggregations keep their source envelopes for later replay; bounded result/release bookkeeping remains available. Queue refresh skips optional opening ranking/enrichment and retains eligibility, due admission and existing order. Unavailable evidence remains unavailable; no synthetic success or empty evidence substitutes for required data.

To restore normal scheduling, uncheck **Study-only mode** and save. Preserved tasks resume through normal bounded scheduling without restarting containers. Do not downgrade schema 47 or restore a backup over newer reviews. A rollback must use schema-compatible images retaining the queue correction.

Deployment requires refreshed ownership/lock checks, pinned immutable candidate images, a stopped-writer checksum-verified backup, restoration into an owned verification database and schema/content comparison before applying migration 47. Preserve existing live volumes and root checkout. Emergency focused evidence does not establish complete CI qualification.

Incident evidence is retained outside the development clone in the root checkout's dated `test-results/p0-study-restoration-2026-10-11/` directory. Deployment revision, images, backup, migration ledger, queue identities, review/receipt totals and live acceptance results are recorded there. Live acceptance requires three user-selected normal study reviews and persistence after reload; automated reviews are confined to disposable databases. Leave mode enabled and observe latency and critical errors for ten minutes after acceptance.

Related: #186 (post-review study stall), #190 (bulk browser preparation), and merged PR #172 (prepared next-card recovery). This intervention does not merge unrelated work or resolve the full remaining scope of those issues.
