# Issue #221 characterization scope

Baseline: current main 25363aa0bc7073646ff8a00b94857d4f3127f694; no production changes.

Risk: lost immutable review intent and stale metadata/deletion across browser realms.
Boundaries: real localStorage, issued queue heads, gateway and logical receipts,
PostgreSQL scheduling/history, true reload, prepared-continuation authorization.

Smallest proof: two Chromium pages, actual outbox code, debugger pause before native
setItem, both write orders. No fake storage or injected production await. UI completion
covers distinct and competing outcomes; a bundled actual-module harness covers mutation
pairs without recreating component state. Pause failures are infrastructure evidence,
not proof of protection. Lost response commits through the real API before abort/reload.

Execute the new browser spec first; retain unmet normative assertions as failing
baseline evidence. Then run affected unit files, typecheck/lint, existing progression,
and settled PostgreSQL durability. CI owns complete applicable candidate validation.
No skips, expected-failure declarations, timeout increases, or green-loss assertions.
Characterization overlays may remain failing on durable audit refs; no correction PR
or completion/merge-readiness claim until a separately scoped correction is qualified.

Only fresh runner-owned resources; no live study services, other-task checkouts,
mutable state reuse, or broad local full gate. Preserve evidence externally before
runner teardown and checkout removal.
