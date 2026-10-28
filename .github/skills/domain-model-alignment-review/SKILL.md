---
name: domain-model-alignment-review
description: 'Review explicitly assigned domain-model duplication or contract clarity. Preserve the requested scope and action type: review produces findings; refactoring occurs only when implementation is explicitly authorized. Not a mandatory step for ordinary feature, naming, or documentation changes.'
argument-hint: '[explicitly assigned scope or feature area]'
user-invocable: true
---

# Domain Model Alignment Review

## Applicability and authority

Use only for domain-model work within the current assignment. Follow [root working rules](../../../AGENTS.md), [SPEC](../../../SPEC.md), and the assigned acceptance case. Selecting this skill does not authorize implementation, broaden the write boundary, or create additional acceptance requirements.

A request to review, assess, explain, or recommend remains read-only unless edits are explicitly authorized. A justified no-change result is successful completion. Similar names or fields alone do not establish duplicate responsibilities.

## Procedure

1. Read the named scope, its public contracts, and the directly relevant consumers.
2. Distinguish genuine duplication from deliberate differences in ownership, invariants, lifecycle, or transport representation.
3. Report concrete findings and the smallest useful correction. Prefer retaining a simple existing design when consolidation has no clear benefit.
4. Only for authorized implementation, make the assigned correction within its allowed files. Do not introduce a shared model, adapter framework, migration, or renamed API merely to satisfy this skill.
5. Use [affected-check selection](../../../docs/testing.md#select-the-smallest-relevant-proof). Fixed defects need named owning regressions; existing coverage may suffice. Follow component-local versus transitional registration rules rather than always editing the root registry.

## Stop and report

Stop when the requested review or implementation acceptance is complete. Missing authority or an out-of-scope contract change is a reported blocker, not permission to build a compatibility programme.

Use the existing task/PR. Report findings or the implemented outcome, relevant evidence, and material limitations. No minimum amount of refactoring, stakeholder mapping table, separate change log, new test, or code change is required solely to complete this skill.
