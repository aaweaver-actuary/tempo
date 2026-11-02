# Workflow behavior in this checkout

This file summarizes the checked-in workflows, not the adopted future component qualification policy. For task-level test choice, use [docs/testing.md](../../docs/testing.md); for the target component map, use [the architecture guide](../../docs/architecture/component-reconstruction.md) and [issue #192](https://github.com/aaweaver-actuary/tempo/issues/192).

At this revision, `pages.yml` selects repository-wide FAST, STUDY, or FULL plans and defines `quality` and `merge-gate` jobs. `verify-layer.yml` and `verify-browser.yml` implement current verification layers. Their actual selection logic is in the workflow and planner code; do not infer that a component package is independently qualified from a selected tier.

The adopted policy is to qualify the affected component, actual consumers, and relevant contracts/boundaries. A full-system check is an integration or periodic checkpoint, not a universal task entrance gate. This documentation does not change workflows, hosted required checks, branch protection, or the result of any check. Confirm hosted protection settings directly before describing the current merge gate.

## Verification without publishing

`pages.yml` retains its filename for existing consumers but is named **Verify
Tempo**. Push, PR, release, merge-group, nightly and manual events retain their
existing verification selection, artifacts, `quality` and `merge-gate`. It has no
Pages build, upload or deployment jobs, publishing permissions or manual publish
option. This removes workflow publication; it does not delete the hosted site.

```mermaid
flowchart LR
  Events["Push / PR / release / merge group / nightly / manual"] --> Selection["Existing FAST / STUDY / FULL selection"]
  Selection --> Focused["Selected head checks"]
  Selection --> Complete["Complete layers and quality"]
  Focused --> Gate["Existing merge gate and retained evidence"]
  Complete --> Gate
  Gate --> End["Verification ends; no Pages publication"]
```

Source: [`pages.yml`](pages.yml),
[`ci-quality.mjs`](../../scripts/ci-quality.mjs), and
[`ci-merge-gate.mjs`](../../scripts/ci-merge-gate.mjs). This diagram describes the
changed workflow source, not runtime installation or verification execution.

## Owned PostgreSQL proof lifetime

The existing full and CI durability PostgreSQL plans include the packaged Games
reader proof during the same owned stack lifetime. Generic backend pytest runs
its import/refusal boundary tests; it does not require a stack that a later
verification stage has yet to create. FAST/STUDY selection and hosted protection
settings are unchanged.

```mermaid
flowchart LR
  Plan["Full or durability PostgreSQL plan"] --> Build["Existing budgeted image preparation"]
  Build --> Ready["Owned stack startup and service health"]
  Ready --> Reader["Mandatory packaged Games reader proof"]
  Reader --> Remaining["Remaining existing native and selected browser checks"]
  Remaining --> Cleanup["Existing verified owned cleanup"]
  Ready -. "failure" .-> Cleanup
  Reader -. "failure or refused target" .-> Cleanup
  Remaining -. "failure" .-> Cleanup
```

Sources: [executable plan](../../database/tests/postgres-test-plan.mjs),
[runner](../../database/tests/test-postgres-docker.mjs),
[reader proof](../../database/tests/check_postgres_game_review_reader.py), and
[exact stage evidence checks](../../scripts/ci-run-layer.mjs). This is implemented
verification wiring, not a claim of a completed native run or installation.
