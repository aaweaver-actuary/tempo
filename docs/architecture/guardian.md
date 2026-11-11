# Architecture Guardian

Version 1.1 · November 11, 2026. This is concise guidance for a pragmatic architectural second look, **not** a new approval bureaucracy or a universal merge gate. [AGENTS](../../AGENTS.md) and [CONTRIBUTING](../../CONTRIBUTING.md) describe its place in normal development; [#984](https://github.com/aaweaver-actuary/tempo/issues/984) tracks improvements to coverage and tooling.

## Intent

Prevent small, unnoticed changes from accumulating into the coupling Tempo has been removing. The Guardian reviews **what the code does and where responsibility lives**, not merely whether a form was completed. Favor one sensible component owner, narrow public APIs, understandable data flow and simple implementation. It is not an extra management layer.

## When to use it

For **code changes**, the default is a brief architecture look at the integrated diff by a separate, low-cost subagent. The Creator should arrange this in the work packet or reusable handoff so that the step does not rely only on the Manager remembering it. A current independent technical reviewer can do the same job rather than adding another agent. The Manager decides how to act on recommendations. Respect the actual agent capacity; the Guardian need not stay running.

A routine one-file bug fix may need just a quick review; don't spend a large-model pass simply to demonstrate compliance. Spend more attention on changes that introduce or alter:

- Public interfaces, DTOs, module exports, dependency edges, wiring, cross-component calls or ownership of business behavior.
- State mutation, transactions, persistence, retries, scheduling, lifecycle and error/hold semantics.
- New services/abstractions that resemble capabilities already present elsewhere, or changes that move domain policy into an HTTP route, browser view or storage adapter.

Documentation, opaque media and other non-code changes have **no default Guardian requirement**. A change to architecture policy itself deserves ordinary thoughtful review, not automatic full Guardian escalation solely because it is policy text.

The objective is not to prove that no imaginable architectural defect exists. A reviewer with incomplete context should identify what it could not assess, not invent certainty. Risk and evidence determine the depth of review, not arbitrary file counts, labels or model confidence.

## What the Guardian examines

Inspect the actual cumulative code diff and relevant direct consumers against the current retained components. Reuse [components.json](../../components.json), component-owned READMEs, the [contract catalog](../../contracts/catalog.json), [Python import rules](../../.importlinter) and applicable type/lint checks as useful evidence. These sources have incomplete or historical coverage; do not assume they prove ownership or runtime behavior.

Ask: Does an existing component own this behavior? Does this create a second state writer, scheduler, loader or implementation? Does a domain import another domain's internals? Have contract semantics changed despite stable signatures? Could a simpler local solution avoid another abstraction? Did integration introduce edges not present in worker diffs?

Return a **short substantive assessment**: reviewed scope, specific risk or no significant concern found, evidence/examples, and the smallest suggested action. Distinguish concrete defects from preferences and nonworsened pre-existing debt. Never demand whole-repository restructuring, zero textual duplication, or unnecessary component splitting.

## Decisions and workflow

The Guardian is read-only on the reviewed code. It can recommend a focused correction or a separate follow-up; it cannot independently enlarge work, rewrite the architecture, waive functional tests or decide merges. The Manager considers its findings and coordinates an actual fix where warranted. Existing acceptance and safety requirements remain in force regardless of Guardian involvement.

**Do not block a PR merely because a dedicated Guardian subagent was unavailable, the review note is short, or a formal receipt is absent.** State material risk honestly and use the normal reviewer/owner judgment. Conversely, a Guardian's “looks fine” does not override a reproducible bug or failed required check. After meaningful integration changes, revisit only the affected architectural questions; don't redo the same unchanged review repeatedly.

## Lightweight automation

A cheap reviewer/classifier can help identify high-risk diffs and bring them to attention, but it should not become a silent architectural pass/fail authority. Deterministic boundary checks are valuable wherever already supported and should be expanded with narrow negative tests. Any future automation under #984 should **surface relevant risks and promote useful review**, rather than require a machine-validated receipt on every PR.

Do not confuse a documented intention with an installed CI rule or functioning agent dispatch. The practical first step is a repeatable small Guardian prompt in the Creator's coding handoffs, alongside incremental enforcement of real dependency rules.
