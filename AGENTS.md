# Tempo agent working rules

Tempo is a single-user, local-first chess study product. Protect playable Study, Tactics, saved reviews, issued attempts, learning history, and accepted obligations while delivering the smallest useful change. Prefer a modular monolith with explicit ownership over new services, frameworks, or broad rewrites.

## 1. Authority, assignment, and stopping

- [SPEC.md](SPEC.md) defines intended product behavior and delivery order. The current explicit assignment defines scope, action type, exclusions, and authorization. Read the relevant SPEC acceptance case, current source, owning component guidance, and [test selection](docs/testing.md#select-the-smallest-relevant-proof). Older issues, plans, audits, and guides are evidence, not automatic new assignments or permission to perform their procedures.
- Before editing, identify the observable outcome, affected owner and consumers, allowed files/interfaces, preservation requirements, finite proof, delivery owner, and stop condition. Resolve ordinary reversible decisions without another planning cycle. If the source and SPEC disagree, report the discrepancy rather than silently changing requirements.
- A component worker owns its implementation and focused proof. For parallel work, one delivery owner coordinates shared files, contracts, migration numbers, integration, and the assembled user journey. Do not absorb neighboring issues, optional features, or unmerged branches. Missing delivery ownership does not prevent independent safe component work; report the gap.
- Stop when the assigned acceptance and safeguards pass. Distinguish **implemented**, **component-tested**, **assembled-demonstrated**, **merge-qualified**, **installed**, and **activated**. None implies the next. A blocked merge or install is not a reason to expand completed feature code; report the blocker and next owner.
- Work on an isolated feature branch/worktree based on current main or an explicitly approved dependency base. Do not edit main or the live Study checkout. Preserve other owners' changes and use only verified task-owned disposable test resources. Never borrow live data, delete retained work, or globally prune shared resources. Follow [CONTRIBUTING](CONTRIBUTING.md#task-resources-and-cleanup).
- Existing authorization remains effective; do not ask again for already-approved actions. An assignment, worker role, or documentation command does not grant new permission for consequential merges, deployments, migrations, data changes, or service operations.

## 2. Reliability invariants: Study is the protected product

- **Essential Study and Tactics must work without optional Games, analysis, engines, external providers, or their workers.** An optional failure, missing source, retryable hold, stopped collector, or unavailable feature mode must not indefinitely block an unrelated valid card, a review save, or accepted Study work.
- **Fail closed at the affected authority boundary, not across the whole product.** An unsafe, stale, ambiguous, or unqualified target remains held with its actual reason and durable identity. Do not bypass source/currentness/integrity/legality checks, manufacture a safe target, or discard uncertain work to make the queue appear healthy.
- Distinguish **new optional producer work**, **already accepted Study-owned obligations**, **ready actionable priority**, **recoverable prerequisite holds**, and **ordinary Study**. Pending work is not automatically an issuance veto. Disabling Games must not erase or strand accepted Study effects. Keep exact original effect/operation IDs, clocks, receipts, dispositions, retry history, and deduplication.
- **Study/Learning is the sole owner** of scheduling, grades, review receipts, queue membership/admission, issuance, and settlement. Optional producers submit typed semantic commands through the owner; they never write learning tables or run a second scheduler.
- Review saving and ordinary issuance must not synchronously perform provider I/O, full repertoire replay, optional health checks, broad exports, or unbounded source reconstruction. Prepare expensive work outside short writer transactions; revalidate immutable proposals under the existing owner fences before applying.
- Preserve original attempts, revisions, queue origins, offline journals, accepted feedback, and human review counts. A retry, lost acknowledgment, page reload, worker restart, or queue refresh must not lose a committed review, duplicate an Again, create an extra issued plan, or silently reset accepted work.

## 3. State ownership, transactions, and recovery

- Each durable state transition has one authoritative writer and an explicit owner. Application/domain policy consumes typed facts and public capabilities. Database adapters own SQL, physical schema, and storage locks; application services must not introduce new direct SQL or cross-owner writes. When touching an existing direct-SQL seam, prefer a narrow existing adapter rather than a new generic repository abstraction.
- Preserve the original **atomic effect + receipt + checkpoint** boundary. Retain idempotency keys, operation generations, leases, source/revision epochs, and lock ordering. Never turn a retry into a fresh logical operation merely to escape a hold, or mark an unfinished projection ready.
- Prepare outside writer transactions; publish only if the exact source, queue membership, revision, and lease remain current. On a race, retry bounded work or return a truthful hold. Repeated invalidations must preserve useful refresh progress and eventually converge after input becomes quiet.
- A foreground save that commits must remain committed even if a post-commit broker wake fails. Wakes are advisory; durable work must recover through existing finite periodic paths. Essential queue and receipt recovery must not depend on an idle optional worker or a browser remaining active.
- Classify failures explicitly: immutable conflict, safety hold, transient contention, pending operation, terminal failure, and unavailable dependency are different states. Retain the original identity across HTTP 202/poll/reload and distinguish a completed hold from an in-flight command. Do not replace a real failure with a generic success or infinite silent retry.
- Bound database connections, locks, transactions, retries, source preparation, and worker occupancy. Foreground queue routing alone does not guarantee capacity if expensive preparations occupy all foreground workers or share the same database pool. Measure before increasing global deadlines, memory, concurrency, or container count.

## 4. Component boundaries and startup isolation

- Cross-component calls use declared public interfaces, not private sibling imports. Composition roots inject concrete implementations; domain modules do not import composition roots or infrastructure. Importing a public contract must not start providers, connect to databases, register workers, or trigger application startup.
- Keep command registration explicit and owned. Preserve persisted command names and compatibility during extraction; removing an eager import is not safe until its registered handlers and recovery consumers are accounted for. Optional features must not be essential API/worker startup prerequisites.
- Use one cohesive responsibility per module, not one function per file. A facade may re-export its own implementation; it must not disguise reverse dependencies. Pure shared libraries and intentional public contracts are legitimate dependencies. Separate services/databases are not required for modularity.
- Before building or replacing a component, inspect the current API, owning README, actual consumers, registration, and reusable implementations. Source import graphs and file size suggest risks, but do not prove runtime harm; verify emitted bundles, executed paths, or actual failure mechanisms before refactoring.
- Keep the browser's durable review/operation/offline journals and their distinct authorities. Shared transport states should be typed and consistent, but do not collapse separate business workflows into one global store or discard compatibility paths without positive reachability and replay proof.

## 5. Verification: prove the changed boundary and the usable journey

- Select the smallest **real** test set that covers the changed component, its consumers, and its ownership boundary; see [docs/testing.md](docs/testing.md). Pure logic gets pure tests. Concurrency, lock order, leases, rollback, migrations, and SQL semantics require the applicable native PostgreSQL proof. Browser pending/receipt behavior requires real transport and browser coverage when mocks cannot establish it.
- Every user-reported defect needs a discoverable named regression (reuse an existing suitable one). Reproduce the failure before claiming its fix when feasible. Include negative cases: optional producer unavailable, stale source, concurrent request, worker death, timeout, lost broker wake, replay, and protected active attempts as applicable. Never weaken a safety assertion merely to make a test pass.
- For changes to queue issuance, review saving, feedback admission, or deployment, the delivery owner must demonstrate the **assembled saved-review → authoritative playable-next-card** journey on the appropriate disposable stack. A 200/202 response, healthy container, passed unit suite, or merged PR is not proof of a playable card or installed behavior. Exercise relevant failure injection and exact-once receipts, not only the happy path.
- Avoid indiscriminate full-suite execution for every leaf. Use targeted contract/native gates and a single coordinated assembled proof for the release; full-system runs remain integration or periodic checkpoints. Record which checks actually ran, on which SHA/schema/runtime, and which were skipped, pending, or unavailable. Never report a planned test or prior unrelated receipt as a current pass.
- The release/installation record must distinguish source SHA, immutable image identities, schema, active modes, worker identities, migration state, and observed functional acceptance. Verify installed behavior separately from merge qualification. Preserve backup and recovery safeguards; reinstalling an older image is not necessarily a safe database rollback.
- The current Tempo CLI `--no-ci` workaround is explicitly approved while hosted CI is unavailable. It permits using that flag without renewed approval; **it does not mean CI passed, waive applicable local/native/assembled acceptance, or authorize an otherwise unapproved installation**. State `ci-disabled` or equivalent truthfully.

## 6. Delivery discipline and documentation

- Optimize for the smallest supported single-user MVP. Prefer deleting unnecessary dependencies over adding orchestration. Do not introduce speculative services, event buses, schedulers, broad refactors, or defensive machinery without a demonstrated requirement. One credible failure mode and its bounded regression are more useful than a general hardening campaign.
- Coordinate shared-file edits, interface changes, and migrations with the delivery owner. Use focused tests and scoped review under [CONTRIBUTING](CONTRIBUTING.md#changes-and-review). Record residual nonblocking risks and an actionable owner in the existing issue/PR; create a new issue only for independent work that merits one.
- Update the affected canonical architecture drawing when a change materially alters dependency direction, state ownership, transaction/persistence, or lifecycle/failure isolation. No material architecture change means no new diagram. See [drawing procedure](docs/architecture/diagrams/README.md#maintenance-and-review-procedure).
- Database operations belong in the existing database owner. Schema/storage changes update the affected data description and diagram per [database documentation](database/README.md#before-merge). Do not turn a scoped change into a whole-database census or migration.
- Report: exact changed boundary, preserved invariants, tests and observed evidence, remaining uncertainty, source/assembled/install state, and the next owner. Stop rather than expanding scope to eliminate unrelated risks.

## Guidance index

- [SPEC.md](SPEC.md): product behavior, scope, and delivery order.
- [CONTRIBUTING.md](CONTRIBUTING.md): review, publication, permissions, and resource cleanup.
- [docs/testing.md](docs/testing.md): test selection, regression ownership, and runner limitations.
- [Component architecture](docs/architecture/component-reconstruction.md): public boundaries and discovery.
- [Architecture diagrams](docs/architecture/diagrams/README.md): canonical drawings and change procedure.
- [Database guidance](database/README.md): database ownership and schema documentation.
- [Small-task guide](docs/architecture/study-first-reduction-2026-10-21/SMALL-TASKS.md): finite task execution.

This file sets cross-cutting invariants. Owner-local guidance supplies concrete interfaces and commands without silently enlarging an assignment. Resolve specific conflicts with the delivery owner; do not launch another policy audit.
