# #200 — Pure scheduling and stable identity

Implementation starts from main `6b99085f8978346a959b5d4a6ea44b6e56f05dd6`.
Scheduling artifacts were qualified at `c4e1446b36724d9c693737188cab0ce0e9a5149e`;
identity's final guard/test inputs at `81b287334ddf6e7315fbb604e9941a7003ca6f27`.
The [machine-readable evidence](pure-scheduling-identity-200.json) records exact
input hashes, commands, executed case identities, artifact hashes and revisions.
Later integration changes do not relabel those original runs.

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

## Historical integration review at 3562e6fc

At the original review, D's foundation had not merged. Independent review found
backend installation omitted chess-core and still pinned scheduling 0.1.0. Those
findings described actual head `3562e6fc`, despite a passing disposable rehearsal.
The original unapplied patch and complete original validation documents are
archived under `/Users/andy/tempo/test-results/pr275-integration-20261015/`.
The patch is incorporated into actual configuration and removed in `5b1436af`;
it is no longer an implementation instruction. The table above and the original
JSON root fields remain historical results at their exact original revisions.

#200 mapping: executable pure APIs, unchanged stable digests, legacy adapter proof,
installed artifacts, deterministic processes and forbidden-import rejection are
delivered. At that original revision, shared registration/dependency integration and its
scoped proof remained outstanding; the integrated qualification below resolves them. Package unit success does not prove Learning queue/receipt durability.
PostgreSQL, browser matrices, full-system gates, model work, image builds, registry
publication, migrations, merges and deployment were not run.

Raw commands/results, JUnit reports, artifacts, baseline capture, review findings
and reproduction scripts are retained outside the task checkout at
`/Users/andy/tempo/test-results/agent-c-200/`. Task-owned environments/checkout are
removed after verifying pushed durable commits and absence of active users;
shared dependencies, caches, other checkouts and live study resources are retained.


## Integrated current-main qualification — October 15, 2026

Executable integration commit: **`5b1436affbe6395a90588466794a02af0e54a124`**.
Current target main: `dfcb08faeef40ad7522a65c4cada9ece1761448c`.
Foundation #277 head `cbc12f77b114c60e91a91d1f9d4b8cb929f4153f` is included through
merge `9e99159148cc6b1b656caf1eeeb3f9a7208d7a55`. PRs #276/#279 are preserved.
Learning #278 is still unmerged; the existing application seam suite is unchanged.

Actual backend metadata, lock, test requirements and image inputs now install
`tempo-scheduling==0.2.0` and `tempo-chess-core==0.1.0`. Both existing manifest
entries are implemented with matching versions, public exports and explicit suite
working directories. All other components, shared suites, schema/catalog inputs,
Python >=3.12,<3.15, FSRS 6.3.2 and unrelated resolved versions match current main.
No package algorithm or backend application behavior changed in this integration.

| Integrated proof | Actual result |
| --- | --- |
| Clean declared backend setup, CPython 3.12.12 | Both distributions installed noneditably; adapter identities pass |
| Backend adapter/application/boundary suites | 52 passed, including two installed-distribution regression cases |
| Selected manifest validation and discovery | Both implemented packages selected and validated |
| All three selectors with Python/uv/npm unavailable | Listing succeeds without setup |
| Registered component suites | Scheduling 55; chess-core 105; declared package cwd |
| Restricted installed wheel, Python 3.12.12 | Scheduling 55 ordinary + 55 guarded; chess-core 105 ordinary + 105 guarded |
| Restricted installed wheel, Python 3.14.8 | Scheduling 55 ordinary + 55 guarded; chess-core 105 ordinary + 105 guarded |
| Restricted wheel/sdist builds and artifact hashes | Four artifacts; all match original final package artifact hashes |
| Restricted empty selections | Exit 5 for each package/runtime; no false pass |
| Component tooling regular suite | 72 passed; zero failures/skips/TODOs |
| Scheduling and TypeScript FEN-key/card-ID producer parity | 2 passed |
| Conflict, complete PR whitespace, lock drift and input checks | Passed |

The clean setup command was `uv sync --project backend --locked --no-editable
--python 3.12`. Imports resolve inside `backend/.venv/lib/python3.12/site-packages/`,
with installed metadata versions 0.2.0 / 0.1.0 and editable=false. `Schedule` and
`card_id` identities match their packages; the legacy review function remains an
intentional wrapper. `PYTHONPATH` was empty. The registered suite commands remain
D's `uv run` commands: uv chose package-local CPython **3.13.5** environments,
while `TEMPO_COMPONENT_PYTHON` selected the installed backend interpreter for the
checker/report reader. Endpoint qualification is the separate restricted proof.
Ordinary and guarded counts above are repeated executions of the same unique
case sets, not additional unique cases. Restricted builds install wheels;
independent sdist rebuilds remain the original historical proof.

Exact commands, runtimes, case identities, input hashes, artifact hashes and
reports are in JSON `integratedCandidate` and outside the task checkout at
`/Users/andy/tempo/test-results/pr275-integration-20261015/`.
`restricted-build.json` retains the full installed-artifact report and
`restricted-case-identities.json` separates component/runtime case sets.
The pending patch's original bytes are retained as `original-registration.patch`.

The new named registration regression failed against the pre-integration planned
chess-core entry, then passed after integration. Initial producer attempts ran no
cases because the task dependency links omitted the chess-line workspace link;
correcting that task-only link yielded the two passing cases. The first backend
run passed 52 cases with a denied unrelated home-cache warning; disabling only
pytest's cache provider yielded 52 passes without warnings. The first restricted
run passed, but its report filename collided with task command metadata; a repeat
after correcting the recorder preserved the complete report. These diagnostics
remain recorded separately and are not passing qualification evidence.

Independent read-only source and evidence review found **no actionable findings**.
The reviewer verified original evidence preservation, input/artifact hashes, installed
versions/origins, runtimes, counts and case identities; it did not rerun tests.
Engineering verdict: **ready for independent final review and merge**, subject to the
normal human review decision. This qualifies the bounded package/backend integration; it does
not claim deployment or full-product qualification. Actions remains disabled and
is not treated as a correctness failure. No Docker image, PostgreSQL, browser,
model, full-system, Rust, publication, migration, merge or deployment work ran.
Task resources are removed only after evidence and pushed refs are verified.
