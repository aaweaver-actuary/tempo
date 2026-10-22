# Contract for a small implementation task

Use with the assigned leaf issue under [#357](https://github.com/aaweaver-actuary/tempo/issues/357). Parent issues coordinate; they are not assignments. Repository data-safety, isolation and transaction rules still apply.

## Start with a bounded input packet

Read root AGENTS.md, the assigned leaf, and its named files. Read CONTRIBUTING.md for behavior changes. Do not automatically load the historical 881-module/292-table inventories, the old 12-phase roadmap, every linked PR or all architecture diagrams. Follow an additional import only to resolve a concrete dependency needed by this task.

Confirm current base/head, no conflicting owner, prerequisite artifact/interface availability, exact allowed writes and named tests. A repaired handoff must correspond to published source, not merely a PR-description SHA. If an equivalent implementation/test already satisfies the task, report its exact proof rather than creating a duplicate.

## Status meanings

| State | Allowed action |
| --- | --- |
| READY | Implement only the fixed leaf with its available prerequisites. |
| BLOCKED | Do not guess the missing dependency or start a second owner implementation. |
| EXISTING PR / REVIEW | Reconcile and review the current branch; do not open another version. |
| HOST | Wait for actual installation/data observation; pure unrelated tasks can proceed. |
| EXPERT | A reviewer must freeze identity, transaction, migration or deletion authority first. |

A small-model label is a routing suggestion, not permission to weaken checks. Use a stronger reviewer for DB privileges, source deletion and cross-authority effects.

## Keep implementation mechanical where possible

One outcome, one write allowlist, one draft PR. Preserve existing APIs unless the leaf supplies their exact replacement. Do not improve adjacent code, rename packages, invent extensibility, introduce decorators hiding transactions, or restore deferred features. One module means one cohesive responsibility, not one function per file.

New contract discovery, ambiguous history/aliasing, live-state access, a second scheduling/writing authority, or a required edit outside the allowlist is a stop condition. Return the exact missing fact/function/file and a minimal failing fixture. Do not expand the task or repeatedly ask the unavailable user for host access.

If a dependency is missing, distinguish environment failure from application failure. Retain the first actionable error. Do not repeatedly reinstall unrelated stacks, run all tests to find a green subset, raise timeouts or disable assertions.

## Verification ladder

1. Run the leaf's pure/local tests during edits. Collection must be nonzero and expected cases must execute.
2. Native SQL/locking/replay/privilege checks use the existing owner runner and disposable resources only. Essential profile avoids optional fixture installations once #374 is merged. Content/operator qualification intentionally uses its required fixture set.
3. Integration owner runs the fixed combined selection for the assembled revision. Reuse prior receipts only if relevant source, dependency, fixture and toolchain inputs match. A mock pass is not a real PostgreSQL/browser pass.
4. Actual user history, physical devices and registered deployment are separate gates. Never label them passed from source-only or synthetic results.

Physical source absence must occur before configuration loading, not just inside an application bundler hook. Bundler inventory is not a complete public-file inventory. A successful HTTP acknowledgement is not necessarily final durable completion. A readiness route must check its declared authorities without manufacturing business writes.

## Minimal handoff

Record: outcome; tested executable SHA and published SHA; exact changed paths; exact commands and collected/passed/failed/skipped counts; source/category of evidence; current limitations; task-resource cleanup. Include normalized base-to-head diagnostic differences when a global check already fails. Counts and removed/added diagnostics must reconcile. No ellipsis commands or invented hashes.

Keep one concise receipt with links to sanitized retained logs. No raw secrets, private records, copied dependency trees, or a new reporting framework. Documentation-only publication after testing should be identified rather than forcing all tests to rerun.

Do not merge, deploy, touch live services/volumes, mutate stored history, drop tables, clear browser intents, change CI permissions or delete another agent's branch. Only separately authorized release/operator work may cross those boundaries.
