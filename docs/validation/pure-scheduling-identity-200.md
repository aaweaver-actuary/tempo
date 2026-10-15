# #200 — Pure scheduling and stable identity

Implementation starts from main `6b99085f8978346a959b5d4a6ea44b6e56f05dd6`.
Scheduling artifacts were qualified at `c4e1446b36724d9c693737188cab0ce0e9a5149e`;
identity's final guard/test inputs at `81b287334ddf6e7315fbb604e9941a7003ca6f27`.
The [machine-readable evidence](pure-scheduling-identity-200.json) records exact
input hashes, commands, executed case identities, artifact hashes and revisions.
Later changes to this evidence or pending patch do not change those package inputs.

## Delivered boundaries

- `tempo-scheduling==0.2.0` requires aware review time, separate calendar day, and
  complete explicit serialized card state. FSRS remains 6.3.2. Its supported decoder
  receives identity/due values explicitly; no generated identity, clock, collision
  sleep, fuzzing or service work occurs in package execution.
- The backend wrapper retains its full old signature/defaults, naive UTC handling,
  omitted-time/local-day distinction and actual FSRS initial identity generation.
  `Schedule` remains the same class. The old function-identity assertion is replaced
  by forwarding and result equivalence, not removed without replacement proof.
- `tempo-chess-core==0.1.0` exposes only first-four-field FEN keys and unchanged
  persisted card digests. Backend cards delegate. Runtime dependencies are stdlib
  only; no parsing, case folding, move repair or legal en-passant normalization.
- Direct strict callers are the backend wrapper, scheduling package tests, its
  adapter regression and the existing producer-parity file. B's review source and
  application tests are unchanged. Existing wire v1 and TypeScript/Rust source are unchanged.

## Executed proof

| Proof | Result |
| --- | --- |
| Installed scheduling wheel, CPython 3.12.12 | 55 passed |
| Installed scheduling wheel, CPython 3.14.8 | 55 passed |
| Installed identity wheel, CPython 3.12.12 | 105 passed |
| Installed identity wheel, CPython 3.14.8 | 105 passed |
| Wheel + sdist builds; independent sdist-to-wheel rebuilds | Both packages passed |
| Zero-selected installed suites | Each exits 5 as required |
| Backend adapter/boundary/application seams | 50 passed |
| Pending metadata patch, clean temporary backend setup + same seams | 50 passed |
| Selected installed Python producer-parity lane | 2 passed |
| Pending patch applicability, conflict and whitespace checks | Passed |

Each wheel consumer has unrelated source absent from its import path, and its
build/extracted source directories are deleted before execution. Service imports
and network/database initialization are guarded. Package-owned child processes
also reject subprocess/service initialization and clock/ID/sleep/random APIs while
running actual FSRS decoding/calculations. Each fixed-input corpus is executed
repeatedly under UTC, America/New_York and Asia/Tokyo. Guard negative controls
fail as intended. No selected test is skipped or TODO-only.

Before edits, 12 scheduling scenarios and 90 identity cases were captured from the
baseline. Existing scheduling goldens and all eight shared digest fixtures remain
unchanged. The baseline rejects non-UTC review offsets; 0.2.0 now normalizes aware
instants to UTC while preserving the separate day. The producer lane compares
Python FEN keys with the equivalent unchanged TypeScript API. Rust's existing
card-ID fixtures/implementation were inspected; its position-search canonicalizer
has different legal en-passant semantics. No fresh Rust execution is claimed.

Commands are enumerated with their actual isolated paths in the JSON evidence.
Regular package commands remain `uv sync --locked --no-editable`,
`uv run --locked --no-editable python -m build`, and
`uv run --locked --no-editable python -m pytest tests` from each package root.
Focused backend proof installs the two packages and pytest/Import Linter/Grimp;
the clean metadata rehearsal also installs its declared python-chess dependency.
No whole-backend/model environment is installed. Node proof reuses existing read-only
dependencies through task-owned links, building only chess-line. The first saved
seam run reported an unrelated cache-write warning outside the checkout; the
clean-setup proof disables that cache and passes without warnings.

## Pending integration and independent review

This branch intentionally leaves D-owned initial shared metadata/tooling unchanged.
The [exact registration patch](pure-scheduling-identity-200-registration.patch)
contains only scheduling/chess-core component entries, their backend pins/path
installs/lock entries, explicit image package copy/install inputs and relevant
regression registration. It applies to the starting shared files (`git apply
--check` passes), and its dependency changes were resolved/installed successfully
in a disposable copy. Its test `cwd` fields target D's announced selector design.

After D's baseline is merged, reconcile/apply this patch, retaining D's Python
range, other component entries and unrelated lock data. Then run D's scoped
component validation, discovery and restricted artifact commands with
`--component scheduling --component chess-core`. No competing checker/schema is
introduced. No image rebuild or deployment is part of this task.

Independent `codex review --uncommitted` found two P1 integration gaps: current
backend installs omit chess-core, and the current backend pin/lock still requires
scheduling 0.1.0. The pending patch addresses both mechanically, but they remain
unresolved on the actual branch until D integration. Therefore the PR is draft,
uses **Refs #200**, and does not claim #200 or parent #253 complete.

#200 mapping: executable pure APIs, unchanged stable digests, legacy adapter proof,
installed artifacts, deterministic processes and forbidden-import rejection are
delivered. Actual shared registration/dependency integration and its scoped proof
remain. Package unit success does not prove Learning queue/receipt durability.
PostgreSQL, browser matrices, full-system gates, model work, image builds, registry
publication, migrations, merges and deployment were not run.

Raw commands/results, JUnit reports, artifacts, baseline capture, review findings
and reproduction scripts are retained outside the task checkout at
`/Users/andy/tempo/test-results/agent-c-200/`. Task-owned environments/checkout are
removed after verifying pushed durable commits and absence of active users;
shared dependencies, caches, other checkouts and live study resources are retained.
