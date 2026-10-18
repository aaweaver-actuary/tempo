# Board UI milestone evidence — 2026-10-17

## Repair of the independent review findings

Repair branch: `codex/board-ui-repair-198`, based directly on reviewed commit
`45369257a258ddb127aed14ac24163085844bb13`. The author's branch and checkout
remain unchanged and clean. All repair paths stay in `packages/board-ui`.

The original six symptom reproductions were copied and executed before edits;
all six confirmed the reported defects. Their source and log are retained in
`/Users/andy/Documents/Codex/2026-10-17/task-10/evidence/` as
`before-reproductions.test.tsx` and `before-reproductions.log`.

The repair allowlists shape/nested fields, escapes label text only at the
renderer boundary, keeps canonical snapshots and returns detached literal
drawing data. Explicit shape updates invalidate annotation SVG children to
cover fill-only changes and text-hash collisions without replacing pieces.
A package-owned construction adapter retains Chessground's exact pinned
rendering/gesture modules while owning surface observation, board bindings,
document disposal, guarded redraw scheduling, and disabled-at-construction
ghost/context-menu features. Mode changes only clear geometry. Teardown
cancels held piece/drawing input, and wrapper observers fence old notifications.
No global browser API is patched by the implementation.

Final bounded serial qualification (macOS arm64, Node `v26.10.0`; dependency
versions unchanged):

```sh
npm_config_cache=/Users/andy/Documents/Codex/2026-10-17/task-10/npm-cache \
  node qualify.mjs \
  /Users/andy/Documents/Codex/2026-10-17/task-10/evidence/qualification-final
```

All nine subprocesses passed: offline producer install (0.77 s), producer
typecheck (0.73 s), **44/44 tests in five files, zero skipped/pending/todo**
(2.76 s), build (0.53 s), pack (0.21 s), offline consumer install (0.50 s),
source-removed consumer typecheck (0.68 s), public smoke (0.07 s), and production
consumer bundle (0.34 s). The 27 existing cases remain; 17 added cases cover
BUI-16 through BUI-24. Exact receipts and input hashes are in that evidence
directory. Successful staging was removed. The tarball contains 38 files:

```text
SHA-256: 564d98ef0ab571fa72958329d60d9d597a34da80572d0776b10b71b077733736
```

The consumer retains one React entry, all twelve emitted piece assets and the
real pinned rendering/input modules, with no app/service/package-source inputs.
`node --check browser/scenarios.mjs`, `node --check browser/run.mjs`, and
`git diff --check` also passed. This repair evidence section was added after
qualification; a table-spacing correction in `REGRESSIONS.md` also followed.
Both files are excluded from the npm payload. No executable,
fixture, dependency, build input or shipped documentation changed afterward.

All five native scenarios remain **NOT RUN**. The prepared scenarios now also
assert ghost visibility, right-button drawing/context-menu suppression, and
unmount while a piece is held. Parent independent re-review precedes the parent
resource grant for native execution. No browser/server, service, Docker, model,
root build/suite, publication, push, PR, merge, deployment or CI change ran.
The isolated repair worktree/dependencies and evidence are retained for that
re-review; they have no running task resource. Shared registration and active
Study/C3 work remain with their owners. The earlier milestone evidence below
records its original reviewed baseline, not a passing claim for these repairs.

Base: `d0485d9fdfb85b4fb6c8de2c403f02d87021817d` (current main at ownership
inspection). Branch: `codex/board-ui-package-198`. All tracked changes are
restricted to `packages/board-ui/**`. No competing Board UI worktree, local or
remote branch, open PR, issue assignee, or active task owner was found in the
inspected inventories. Current issue #198 still marks Board UI as planned and
remains open. Main and other task checkouts were only read.

## Final executable evidence

On macOS arm64, Node `v26.10.0`, React/React DOM `19.2.6`, Chessground `10.1.1`,
TypeScript `5.9.3`, Vitest `5.0.1`, jsdom `30.0.1`, and esbuild `0.27.3`:

```sh
npm_config_cache=/Users/andy/Documents/Codex/2026-10-17/task-6/npm-cache \
  node qualify.mjs \
  /Users/andy/Documents/Codex/2026-10-17/task-6/evidence/qualification-03
```

The qualifier was run from `packages/board-ui`. Each subprocess was serial and
bounded to 120 seconds. The measured subprocess times below total 6.13 seconds;
that sum excludes filesystem orchestration and is not total command wall time.

| Check | Outcome | Seconds |
| --- | --- | ---: |
| Package-only `npm ci --offline --ignore-scripts --workspaces=false` | pass | 0.78 |
| Producer `npm run typecheck` | pass | 0.66 |
| Producer `npm test -- --reporter=json --outputFile=…` | 27/27 tests, 4 files, zero failures/pending/todo | 2.27 |
| Producer `npm run build` | pass | 0.53 |
| Producer `npm pack --ignore-scripts --json --pack-destination …` | pass | 0.21 |
| Fresh consumer tarball install, offline/ignore-scripts/workspaces=false | pass | 0.56 |
| Consumer `tsc -p tsconfig.json` | pass | 0.69 |
| Consumer `node smoke.mjs` | pass | 0.08 |
| Consumer `node build.mjs` | pass | 0.35 |

The producer staging contained only this package and its narrow dependencies.
It never contained app/services, root metadata, or Rust/WASM sources. After
pack/install, producer source and installed package source were removed **only
inside disposable staging**. Consumer typecheck/import/build still passed.
The consumer imports every public type and real built renderer, renders the
public React component on the server, resolves CSS, all twelve Merida SVGs and
license notices, and rejects the private controller import. Its small production
browser bundle includes real Chessground, one React copy, CSS and all twelve
emitted SVG assets. That build is not a native browser execution.

Tarball: `tempo-board-ui-0.1.0.tgz` (35 files).

```text
SHA-256: 60f13d9688bfe8afcae55964a1fdd4ff40ef699b15fb346c4f98f0707c4a5a06
```

Raw receipts, exact commands, input SHA-256 map, npm pack file manifest and
integrity, unit JSON, consumer lock, and built consumer are preserved in the
absolute evidence directory above. The final documentation in this file was
added after execution; it is excluded from the npm payload and does not alter
any qualified executable, fixture, dependency, style, or build input. All inputs
recorded by qualification-03 were checked unchanged before commit.

`node --check browser/run.mjs`, `node --check browser/scenarios.mjs`, and staged
`git diff --check` passed. Standalone development typecheck/test/build checks also
passed before the final disposable-staging qualification. The initial narrow
dependency installation installed 86 packages; no root install was performed.

## Earlier attempt and cleanup

Qualification-01 failed because its new Node-environment boundary test shared a
setup file that unconditionally referenced `HTMLElement`. The setup now guards
that jsdom-only geometry stub. No test or assertion was removed or skipped.
Qualification-02 passed 24 cases and the complete installed-consumer path.
Three real pinned-Chessground/jsdom seam cases and stronger native fixture
assertions were then added; qualification-03 is the final 27-case proof.

The failed attempt's receipt, unit report and logs are retained, with its setup
source preserved before removing that task-owned staging. Both successful
staging directories were removed automatically. The task worktree, committed
branch, package-only dependencies/cache and durable handoff artifacts remain
available for parent review and later resource-granted browser work. No browser,
server, container, database, model, global resource or unowned checkout was
started, stopped, or cleaned by this milestone.

## Deferred gates

`browser/scenarios.mjs` prepares five serial native Chromium cases for held drag,
stale input, authoritative piece restoration, free editing, focus/keyboard,
remount and responsive geometry at fractional device scale. **No browser case,
screenshot or trace has been executed or claimed as passing.** The runner
requires the parent resource grant and a prepared pinned Playwright driver.

Remaining acceptance: native interaction/visual review, broader accessibility
and touch/cross-browser checks, shared component/runner/CI registration, real
Study/Workbench adoption and navigation/source-absence proofs. Existing legacy
application regressions, #211/#250 binding ownership, and production behavior
remain with their application owners. This is a reviewable package milestone,
not complete #198 acceptance or hosted merge qualification. Nothing was pushed,
opened as a PR, merged, deployed, activated live, or enabled in CI.
