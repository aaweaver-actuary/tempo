# PR #278 integration qualification

## Historical evidence

Original tested implementation: `52f1ac8fc1e7168e1b1aa8580468be60ec45ef9c`;
original evidence-only head: `56764278a1d1cd57ecbc789d5b47cba2bf8b6143`.
The original [machine-readable record](evidence/qualification.json) is preserved
unchanged. Its 43 installed cases per runtime, 24 native cases, 55 additional
legacy cases and 177-pass/1-failure affected selection are historical results,
not qualification of the integrated candidate. Original raw evidence remains at
`/Users/andy/tempo/test-results/learning-review-224`.

## Integration

Current main `80db7972b2e2ec525084120f8d541509379673a9` includes merged #275,
#277 and #280. Ordinary integration commit:
`ad806afedaf02d15b7949b1c02ee6725e9a6e775`. The merge had no conflicts.
Scheduling 0.2.0, chess-core 0.1.0, all prior registrations, selectors and image
inputs are preserved. Learning 0.1.0 is now included in the actual dependency,
lockfile, test-install, image and component declarations. The original pending
patch is archived outside tracked source and removed from the application.

The production backend Dockerfile is the affected image path. The maintenance
image copies only migration-specific modules and does not import review commands;
it requires no Learning installation. There is no separate affected worker image.

## Completeness repair

The original nonempty-JUnit acceptance predicate accepts a reproduced partial
selection: **1 passed, 42 deselected**. Both runners now reject inherited selectors
before resource allocation. The native runner narrowly permits one external JUnit
destination in either supported spelling/form, preserving the component lane's
structured reporting contract. External pytest plugin injection is disabled.

Independent controlled collection must match unique executed node IDs. Every
setup/call/teardown phase must pass, with zero deselections, missing/duplicate
identities, skips, failures or errors. JUnit must also contain the complete passing
inventory. Test totals are derived from collection, not hardcoded.

## Integrated candidate evidence

Final qualification is recorded after the executable repair commit. Raw command
logs, receipts, installed package origins, artifact hashes, resource records and
patch archive are kept outside the tracked checkout at:
`/Users/andy/tempo/test-results/pr278-integration-20261015/`.

No merge, deployment, live-data/schema change, full historical CI, browser, model,
Rust or Maia workload is part of this qualification. No standalone Learning
service, domain database cutover or production wire-v1 adoption is claimed.
