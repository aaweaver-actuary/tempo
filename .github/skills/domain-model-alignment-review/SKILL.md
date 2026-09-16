---
name: domain-model-alignment-review
description: 'Review and refactor code to consolidate similar objects and data structures into domain-based shared models. Use when improving stakeholder readability, reducing duplicate technical representations, and applying SOLID only when it simplifies the codebase and clarifies business intent.'
argument-hint: '[scope or feature area]'
user-invocable: true
---

# Domain Model Alignment Review

## Outcome
- Produce a focused refactor that combines similar object and data elements into shared domain-based models.
- Make the relationship between code and product intent easier for non-technical stakeholders to follow.
- Preserve behavior while reducing accidental complexity.

## When To Use
- Multiple types or payload shapes represent the same domain concept with minor technical differences.
- Feature code is organized around framework or storage details instead of domain language.
- A review request asks for simplification, better naming, or clearer domain traceability.

## Inputs
- Target scope: file set, folder, or feature area.
- Domain intent statement: what user or business goal this code supports.
- Constraints: backward compatibility, API contracts, migration limits, release timeline.

## Procedure
1. Define domain intent first.
   - Write a one-paragraph statement in domain language, not framework language.
   - Identify core entities, value objects, and actions.
2. Inventory duplicated or parallel structures.
   - List objects, DTOs, state slices, and response shapes that model the same concept.
   - For each item, record where it is used and which fields are truly domain fields versus transport or UI fields.
3. Choose the consolidation strategy.
   - Default to a shared core plus adapters when domain meaning is shared but boundary concerns differ.
   - Use direct merge only when two structures have the same invariant and lifecycle.
   - Keep separate models only when invariants or lifecycle differ in a domain-significant way.
4. Refactor toward a shared domain model.
   - Create or promote domain objects in a domain-oriented module.
   - Move validation and invariants close to the shared model.
   - Rename symbols to domain terms stakeholders recognize.
5. Apply SOLID selectively to simplify.
   - Single Responsibility: split mixed validation, persistence, and presentation logic.
   - Open/Closed: extend through small adapters at boundaries instead of duplicating domain rules.
   - Interface Segregation and Dependency Inversion: introduce abstractions only at clear boundary seams.
   - Reject abstractions that increase indirection without reducing real complexity.
6. Protect behavior with tests.
   - Add or update tests that verify observable outcomes.
   - For any user-reported defect touched by this change, add a named regression test and record it in `tests/REGRESSIONS.md`.
7. Prepare a stakeholder-readable summary.
   - Map old technical structures to new domain structures.
   - Explain how the new model reflects original product goals.

## Decision Points
- If consolidation changes public contracts, stop and define an explicit migration or compatibility layer.
- If two models only look similar but enforce different domain rules, do not merge them.
- If a proposed SOLID abstraction adds more concepts than it removes, prefer a simpler concrete design.

## Completion Checks
- Every consolidated model has a clear domain name and documented purpose.
- Duplicate domain logic is removed from at least one technical layer.
- Existing behavior is preserved and verified with automated tests.
- Regression coverage is added for user-reported defects and listed in `tests/REGRESSIONS.md`.
- A non-technical stakeholder can trace each key domain concept to a single primary model.

## Deliverables
- Refactored code with shared domain-based objects.
- Updated tests and regression entries where required.
- Stakeholder mapping table: old technical structures -> new domain model.
- Short change log in domain language: what was unified, why, and what remains intentionally separate.