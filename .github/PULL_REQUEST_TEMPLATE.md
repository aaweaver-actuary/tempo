## Outcome and scope

State the user-visible or documentation outcome and owning issue. Preserve explicit non-goals.

## Validation

Record actual checks/results and relevant revisions; distinguish not run, failed and blocked. Do not imply hosted CI passed when it is disabled.

## Architecture look (for code changes)

For code changes, briefly note whether a Guardian-like subagent or an existing independent reviewer checked for coupling, duplicate responsibility and interface drift. State any actionable findings or material unreviewed risk. Deepen the review when the change affects component ownership, public APIs, state or dependencies. Routine documentation/media changes need no special Guardian check.

This is a prompt, not an automatic gate or mandatory receipt. See [Guardian guidance](../docs/architecture/guardian.md).

## Delivery state

State implemented / demonstrated / merge-qualified / installed separately, with remaining owner/action. Publication does not authorize merge or deployment.
