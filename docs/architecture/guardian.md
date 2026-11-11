# Architecture Guardian

Version 1.0 · November 11, 2026. This is the canonical Tempo architecture-review workflow, not another component catalog. [AGENTS](../../AGENTS.md) and [CONTRIBUTING](../../CONTRIBUTING.md) adopt it. [#984](https://github.com/aaweaver-actuary/tempo/issues/984) tracks implementation and validation of automatic enforcement. This document does not install an agent, classifier, CI job or branch-protection rule, and does not establish adoption by an already-running execution team.

## Required coverage, not another management layer

Every integrated candidate must have an architecture disposition before acceptance or a merge-qualified claim. Review the cumulative integration diff, not merely worker summaries or separately approved leaf diffs. Early review is useful for proposed new interfaces; final coverage is still required. Do not review every keystroke or repeat unchanged work.

Use the actual retained source, existing component registry/contracts, owner READMEs and applicable rules. Do not invent a complete interface registry, treat planned components as deployed, or require finishing all of #984 before reviewing a bounded change. Missing information that prevents evaluating the changed boundary is an explicit coverage gap.

## Routing: lightweight inspection or full Guardian review

Apply hard triggers before any model judgment. Until broader mechanical coverage is validated and the repository owner explicitly approves a narrower policy, full review is required for any executable-source change, including apparently internal refactors and one-line changes. It is also required for:

- Public interfaces, schemas, ownership, state transitions, error/retry/ordering semantics, imports/re-exports, dependencies, registration, composition or persistence changes. Unchanged function signatures do not establish unchanged contracts.
- Tests/fixtures, configuration, dependency manifests/lockfiles, migrations, build inputs, generated executable/schema output, loaders or verification selection changes. Include additions, deletions, renames and mode/symlink changes, not only modified text.
- Agent prompts, architectural rules, interface documentation, review exemptions, gate definitions or receipt-validation changes. A `.md` extension does not make policy changes exempt.

**LIGHT_ONLY** is permitted only when an independent lightweight reviewer inspected the complete change and established that every change is ordinary non-operative prose or an opaque static image/audio replacement, with no change to executable content, loading/configuration, contracts, instructions, data/schema semantics or acceptance policy. File extensions, task labels, author assurances and confidence scores are insufficient. Mixed changes take the stricter route. Embedded scripts, machine-consumed Markdown, generated files of unknown origin and uninspectable assets cannot take this route.

**FULL_REQUIRED** means any hard trigger, plausible boundary/ownership/duplication risk, or uncertainty about the lightweight exemption. A low-cost agent may identify risks and prepare the review packet but cannot downgrade this route. Known mandatory changes may go directly to full review without spending a separate agent call on classification.

**INCOMPLETE** means required inputs, coverage or reviewer access are missing. Escalate or obtain the missing evidence; never convert it to LIGHT_ONLY or a pass. These are routing states, not claims that a product defect exists.

Use the approved policy at the base as the review authority. A candidate that changes the policy cannot use its own relaxed rules to exempt itself; evaluate and approve that policy change independently before it governs later work.

## Creator, Manager and reviewer responsibilities

The Creator remains the sole spawner/resumer in Tempo's established team pattern. At kickoff and resume, ensure the resolved packet includes this acceptance dependency, the source/policy pins and a named independent reviewer. Schedule review on demand within the execution host's actual capacity; release worker slots rather than adding a permanent fifth layer. The Manager must not be the only place this requirement exists.

The Manager names the owning component before dispatch, supplies the final cumulative diff and relevant consumer context, and maintains one architecture row/receipt in the existing task/PR ledger. Workers report new interfaces, dependency edges, reused capabilities and ownership changes, but their reports are inputs rather than clearance. The final acceptance reviewer checks the receipt independently of the Manager's summary.

The Guardian is read-only with respect to the candidate and policy being reviewed. An existing independent technical reviewer may perform both functional and architectural review in one pass, with separate explicit results; it must not have authored the reviewed changes. A different role label or self-review is not independence. No particular model brand is required: use a low-cost capable reviewer for the narrow lightweight class and stronger reasoning for full semantic review.

No reviewer access means incomplete review, not silent permission to proceed. Continue safe independent work and name the missing capability. The Manager may resolve nonblocking design questions with evidence but cannot waive mandatory coverage, redefine triggers to pass its own change or override established safety rules. A repository-owner exception must name the exact candidate, reason, residual risk and follow-up; it is an exception, never a pass or standing authorization.

## Full review scope and findings

Check declared public access and dependency direction; one authoritative owner per capability/state transition; actual consumer adoption; hidden coupling through callbacks, shared state, SQL, registration and configuration; harmful semantic duplication; and consistency of changed declarations/docs with code. Use deterministic checks wherever already applicable, then inspect what those checks do not cover. Do not claim a static import check proves runtime isolation or that a generated symbol list proves behavioral compatibility.

Each blocking finding identifies the changed path/line, applicable approved rule or concrete defect, supporting evidence, affected owner/consumer and smallest remedy. Separate **BLOCK** (evidenced violation) from **REVIEW** (reasoned semantic concern needing disposition). A bounded **PASS** requires complete declared coverage, no unresolved blockers and a documented disposition for REVIEW findings. Missing required evidence produces **INCOMPLETE**. Preserve actual check failures and distinguish them from access limitations.

Do not require maximum decomposition, zero textual duplication, speculative abstractions, unrelated cleanup or a whole-repository redesign. Nonworsened pre-existing debt is not a new blocker unless it violates an independently applicable existing acceptance requirement. An unchanged signature can hide changed ownership or side effects, so inspect the relevant body and consumers.

## One candidate-bound receipt

Keep a concise receipt in the existing PR/task, not a new tracking system. Record:

1. Integration base SHA, reviewed head SHA, approved policy revision and relevant contract/check input revisions.
2. Changed-path coverage, route (LIGHT_ONLY / FULL_REQUIRED / INCOMPLETE) and evidence for that route.
3. Independent reviewer identity and review scope; self-review must be labeled as such.
4. Applicable mechanical checks and actual results, including not-run/blocked limitations and justified reused evidence.
5. Findings, dispositions and final result, or the exact owner-authorized exception.

Missing or stale receipts do not satisfy acceptance. After a new commit, base change or policy/contract input change, repeat routing over the current cumulative diff and review the new delta and affected dependencies. Explicitly rebind still-valid evidence to the new candidate; do not silently carry forward an old approval or rerun unrelated expensive tests. Recheck after conflict resolution and final integration, not only before them.

## Closing the mechanical enforcement gap

Until automated enforcement is implemented, this is an explicit manual workflow gate. A prompt and a checked box cannot guarantee invocation. #984 must separately implement a mandatory entrypoint/acceptance check, conservative routing, candidate-bound receipt validation and verified merge-gate integration without depending on Manager memory. Hosted CI disabled/unavailable is not proof of a pass; preserve the authorized local qualification path and its evidence limits.

Prove rejection of missing/stale receipts, incomplete diffs, unknown paths, a one-line private import, a behavioral ownership change with unchanged exports, changed test/gate selection, and a candidate trying to exempt its own policy edit. Also prove the narrow legitimate LIGHT_ONLY case. Pilot and measure false negatives and avoidable escalations before widening exemptions. Documentation adoption and implemented mechanical enforcement must be reported separately.
