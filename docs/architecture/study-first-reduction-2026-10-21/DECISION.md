# Decision: Study-first reduction

Adopted 2026-10-21; execution clarified 2026-10-22 after PR review. [Programme #357](https://github.com/aaweaver-actuary/tempo/issues/357) and [PLAN.md](./PLAN.md) govern the current task queue. This is not approval to merge implementation, deploy, destroy data or change live services.

## Keep

1. One responsive independent Study web client for phone and desktop. The source-mode split is legacy versus independent, not inherently device-specific. Use the existing `TEMPO_TARGET=study` implementation; do not write a third frontend.
2. One independent Learning API and Learning PostgreSQL authority for due/new cards, progression, review/history, quota, scheduling, issuance and immutable retry receipts. This is the selected engineering target. Its actual user-data migration and deployment remain unqualified until their separate gates pass.
3. Minimal deliberate authored-PGN intake using existing Content validation, explicit approval and accepted Learning publication/activation. Preserve stable identities, active repertoire selection and new-card limits. An on-demand CLI is sufficient; no rich Builder or always-running Content service is required for ordinary Study.
4. Essential configuration, health, explicit schema management, private backup/export, bounded recovery and a one-time faithful migration.

All other features retire by default. Preserve their idea and historical source pointers in [FEATURES.md](./FEATURES.md), not dormant implementations or duplicate runtime modes merely for potential reuse. Existing shared pure code/contracts and local retained Study facts are different from optional producer services.

## Fixed simplifications

- Reuse independent Study's one-issued-card offline behavior: durable answer/original operation, honest pending status and reconnect reconciliation. Do not keep or recreate the legacy full offline-batch/local-repeat scheduler on another device.
- Use one same-origin web/API route in the opt-in composition, with existing credential/workspace/origin controls. Keep PostgreSQL and API internal to that disposable stack; no live ingress change follows from the design.
- Keep the current scheduling/eligibility/quota/transaction behavior while removing unused producers. Do not invent a new ranking model, global quota policy, ORM, event bus or transaction decorator in a packaging task.
- Fresh PGN identities and manifest generation are explicit deterministic functions. Historical card mapping is separate and requires authoritative evidence; reimporting PGN as new cards is not migration.
- Existing Content operator approval/apply/reconciliation is reused for minimal intake. Do not delete required code solely because its directory mentions analysis, games or remediation.

## Design and execution principles

Working Study and authentic learning data are the mandatory preservation boundary. Lost-response/reload/reconnect safety, one logical effect, pending-operation identity and permissions cannot be traded for fewer lines.

Prefer fewer supported workflows and cohesive modules over splitting every existing script. One responsibility is not one SQL statement per file. Keep complete queries in their existing owner adapters and singular transaction/queue-writing authority. Cross-cutting helpers may remove real repetition but must not hide commit/rollback/retry semantics.

Use small implementation leaves with fixed interfaces, explicit file ownership and cheap tests. Existing equivalent tests may satisfy a verification assignment without new source. Historical migration, privilege decisions and deletion approval receive expert review before a lower-capability model executes the mechanical work. No task is permission to perform unrequested live operations.

A current working release stays unchanged until actual authority/data observation, faithful rehearsal, a crash-safe normal-launcher transition and a separately approved cutover. After new-target writes or uncertain acceptance, preserve and repair its authoritative state; do not silently restore a stale pre-traffic backup or restart legacy writers.

## Measurement

Frozen audit `7d77ed1b`: 597 backend/database Python+SQL files / 69,686 physical lines; approximate net-removal target 34,843. Broader implementation/schema denominator: 881 files / 97,217 lines. Track both so moving code cannot count as removal. Tests, docs and generated inventories are separate. Retain correctness regressions for surviving behavior.

## Still unknown and deliberately gated

- Actual phone/desktop origins, cached builds, running images, registered launcher and installed database versions (#358).
- Exact historical identity/provenance mapping and pending browser/server work; private latest-state conversion and recovery (#267/#389).
- Final published/accepted artifact composition and real cross-device behavior (#359/#368).
- Live table consumers and safe physical data disposition (#362).

These uncertainties do not block pure helpers, local contract tests or disposable composition work. They do block unsupported claims of safe live migration, blanket deletion and production readiness. Start with the ready leaves in [PLAN.md](./PLAN.md), not another unrestricted audit.
