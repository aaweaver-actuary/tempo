# Issue 240 development test plan

Base: c5f773b18c4a5b30a3abda84ecac01ab625a3902 (latest remote main at checkout creation).

Changed behavior: diagnostics family ownership and conservative fast-lane escalation;
list/detail browser synchronization observes admitted reads inside the product deadline.
Risks: omitted consumers, mixed/renamed paths, missing inventory or execution evidence,
lost automatic retry responses, masking database refusals, expired admission deadlines.
Boundaries: evidence producers, graph publication, foreground/Redis admission,
PostgreSQL reader transactions, queue publication, shared UI state and browser fixtures.
Smallest proofs: named Node planner cases, bridged regular unit files, existing diagnostics
and background-read files, then the owning real PostgreSQL browser spec. CI owns the
complete current-candidate gate. No production change, local full gate, merge or deployment.
#239 owns general fixture/test-only classification; #240 owns diagnostics selection only.
