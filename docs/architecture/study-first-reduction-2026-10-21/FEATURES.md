# Feature register: retain, retire, or reimplement later

**Status:** Product-scope decision record, 2026-10-21. This documents features; it does not certify source deletion or physical-table drops. Source examples are from [`7d77ed1b25198dcd320e0265f777cc630f23775e`](https://github.com/aaweaver-actuary/tempo/commit/7d77ed1b25198dcd320e0265f777cc630f23775e).

| Capability | Product disposition | Minimum preservation or historical pointer |
|---|---|---|
| Responsive daily Study | **KEEP / unify** | One Study Web client + independent Learning API; phone and desktop must share the same code and scheduling authority |
| Opening reviews, due cards, next new move from each active repertoire | **KEEP** | Atomic review + queue + FSRS/history + receipts; deterministic introduction when optional services are off |
| Learning data, existing repertoire identity and frozen admitted order | **KEEP DATA** | Migrate by semantic invariants, not just record counts; rank generator may be retired |
| Repertoire PGN intake and accepted publication | **KEEP MINIMUM** | Parse/validate/import, version/content identity, accept/activate; reusable parts of Content and bridge, no large builder requirement |
| Basic repertoire settings and selection | **KEEP MINIMUM** | Active repertoire choice and intake quotas; no forecasting/curriculum dashboard required |
| Backup/export, health, migrations, failure/retry hygiene | **KEEP ESSENTIAL** | One bounded operational owner, database export before irreversible retirement |
| Historical SQLite serving / dual runtime branches | **RETIRE CANDIDATE** | Check deployed consumers and one-time converters separately; `backend/app/main.py`, `database/tempo_legacy_database/connections.py` |
| Legacy HTTP/Celery main/task coordinator | **RETIRE CANDIDATE** | Only after Study is independently served; `backend/app/main.py`, `backend/app/tasks.py`, `backend/app/services/game_sync_coordinator.py` |
| Games account sync, game analysis, stockfish engine worker | **RECORD THEN RETIRE** | Preserve ingested data if requested, no active ingestion/derivation requirement; `backend/app/games/`, `services/game-evidence/`, `services/intelligence/` |
| Lichess Explorer, Maia, probability estimation and adaptive ranking | **RECORD THEN RETIRE** (bounded source candidates below) | Consuming already-persisted frozen ordering is distinct from future ranking production; `backend/app/services/repertoire_coverage.py`, `introduction_priorities.py` |
| Discovery suggestions, coverage opportunities and automatic admissions | **RECORD THEN RETIRE** (source-only `opportunity-feed-refresh-01` candidate) | [Exact cessation boundary and transition truth table](RETIREMENT-BATCHES.json): 1,617-line active core retained; stop-new-refresh ingress/callbacks is gated, with four priority-owner callback handoffs. Preserve current stored-opportunity queue eligibility, reads/state/explicit training, issued command/task drain, pending admission and mounted browser recovery. No unused core helper, production deletion, data action or retirement approval; #390/#382/#386/#359/#267/#369 evidence remains required. Revalidated source: [`ff8a7dc9`](https://github.com/aaweaver-actuary/tempo/commit/ff8a7dc99d9be4f4d644e7d8fa285957d5eb196f). |
| Defense/threat generation, endgame generation, tactical finding producers | **RECORD THEN RETIRE** | Preserve any already admitted Study exercises and genuine review history; `backend/app/services/threat_*.py`, old endgame routes |
| Guided game review, chess statistics, motif insights, dashboards | **RECORD THEN RETIRE** | Old endpoints and data can be described/reimplemented later. Server guided review only: `legacy-guided-game-review-01` in [RETIREMENT-BATCHES.json](RETIREMENT-BATCHES.json), **candidate, no source removed**, based on [main 782bf537](https://github.com/aaweaver-actuary/tempo/tree/782bf5374b87f223a023568305056415d5b63cea). Five implementation files / 399 lines plus three HTTP routes, two durable command kinds and exact shared edits; PR419 browser clients are already retired and excluded. Active server/session execution remains until #390 review, original-authority receipts/broker/browser pending disposition, #382 and matching #386 evidence, and #359/#267 actual launcher/history gates. Tables, saved facts/exports and ordinary Study guided scheduling remain protected; no approved/completed legacy-01 or cutover claim. |
| Standalone Workbench, Builder UI and dedicated non-Study workflows | **RECORD THEN RETIRE** | Keep only a simple import interface; `apps/workbench-web/`, optional `app/` surfaces |
| Multiple device-specific Study clients and offline queue policies | **REPLACE WITH ONE** | Intentionally choose the simpler client and one shared backend; do not keep two offline engines |
| Content validation/integrity used to protect active Study | **KEEP AS NEEDED** | A disabled editor does not imply source legality/revision/eligibility safeguards are unnecessary |
| Historical receipts, accepted publications, compatibility views and old tables | **REVIEW BEFORE DROP** | A saved result or view can still be read after its producer has retired |

## Permanent history without permanent runtime

For every retired capability, the deletion PR must record its old purpose, primary entrypoints, relevant last-good commit/PR/issue, any exported user data, and newly unsupported routes/commands. This register is the history index. Do **not** copy retired code into an `archive/` directory searched by agents; use git history and immutable commit links. Historical possibilities are not active issues to implement.

Reference architecture: [prior component roadmap #192](https://github.com/aaweaver-actuary/tempo/issues/192) and its historical 12-stage children remain useful sources of completed work, **not** a requirement to reassemble every optional component under this new direction.

## Issue #361 partial frontend retirement

The legacy comparison, Insights, Statistics and Progress dashboard UIs were removed from the workspace. Their implementation files were `app/views/comparison_view.tsx`, `app/views/insights_view.tsx`, `app/views/statistics_view.tsx` and `app/views/progress_view.tsx`; legacy aliases `compare`, `insights`, `progress` and `statistics` now open Train. The last-good source snapshot is main commit [`43e2035d05c1f08e59e4d1c8aab9f6897a1d1fd4`](https://github.com/aaweaver-actuary/tempo/commit/43e2035d05c1f08e59e4d1c8aab9f6897a1d1fd4). Follow-up cleanup also removed `app/lib/comparison.ts` and its comparison-only worker task schemas and index; ordinary repertoire position indexing remains. No durable Study progress, backend routes, APIs or stored data were removed.

The follow-up Games UI retirement removes `app/views/games_view.tsx`, `app/views/legacy-games-workspace.tsx` and `tests/unit/shared-board-shell-games-regressions.test.tsx`, plus the Navbar Games entry, Training “Games here” action, Repertoire supporting-games drill-through buttons and callback plumbing, game-only styles, preload mapping and source-inventory references. Legacy `games` navigation now falls back to Train. Study’s board, game contracts and sync commands remain. Recovery baseline: [`a85006424050922bd8d1c793d259ed32fc78b366`](https://github.com/aaweaver-actuary/tempo/commit/a85006424050922bd8d1c793d259ed32fc78b366).

The Builder retirement removes `app/views/analysis_view.tsx`, `app/lib/game-scan.ts`, `app/lib/training-builder-route.ts`, the Builder-only position-index worker path, and the unused `app/utils/lichess.ts` authorization launcher. It also removes Builder navigation and Training, Discoveries, Repertoire and CardEditor handoffs, dedicated UI/browser tests, benchmark and CSS. Repertoire import, Analysis Paste, Study review/queue, shared boards, and Lichess session consumers remain. Legacy `analysis` and `builder` navigation aliases fall back to Train. Recovery baseline: [`7ee2583d6e8f228340fb35a40bfe7262f956b850`](https://github.com/aaweaver-actuary/tempo/commit/7ee2583d6e8f228340fb35a40bfe7262f956b850).

The follow-on Repertoire/discovery retirement removes the Discoveries Tray, discovery preview/admission UI, Repertoire statistics drilldown, and coverage/opportunity refresh commands and preview scheduler. Persisted save/queue receipts, PGN import, repertoire selection/settings, Analysis Paste and Study remain. The daily Study browser case with more than 1,000 queued background items remains registered in the `training` family; it was not run here because its fixture controls Docker services. No recovery-UI browser wiring test was added. Recovery point: branch `7b61086efd3ac70810a484d4e4f8e6c5b627dc8e`; main baseline `263f7ec93853fd9d3845ca97c0b3f0f164e98e4b`. Validation on this batch: the chess-line package compile, 8 focused unit files (123 tests), focused frontend inventory lane, Study and local builds passed. The `ci-reliability` runner had 28 passes and 16 failures; the observed guard error is the unclassified `frozen-opening-order.spec.ts`, which was present at the pre-batch base and is outside this patch. `npm ci` restored 583 lockfile packages but npm withheld six dependency lifecycle scripts; no script approvals, root-wide typecheck, browser/DB tests or live services were used.

## Browser engine orphan retirement (browser-engine-orphans-01)

The browser Stockfish/Maia analysis helpers, background/interactive scheduling brokers, and browser Maia worker had no remaining production caller after the Builder/Game UI retirement in PR #407. Primary source entrypoints were `app/lib/analysis-engines.ts` (`analyzeWithStockfish`, `analyzeWithStockfishRequest`, and `analyzeWithMaia`), `app/lib/engine-broker.ts` (`requestBackgroundAnalysis` and `requestInteractiveAnalysis`), `app/lib/maia-broker.ts` (`requestBackgroundMaia` and `requestInteractiveMaia`), and the `app/lib/maia.worker.ts` worker URL. The last-good pre-removal snapshot is main merge [`b39dd3a9ac2e846f1d2a9242a9d8a2eda682f9a8`](https://github.com/aaweaver-actuary/tempo/commit/b39dd3a9ac2e846f1d2a9242a9d8a2eda682f9a8), which merged PR #407. These were browser-local functions, not HTTP routes or durable Study commands; their app-local exports are no longer supported. No production caller outside this five-module implementation set remained at that snapshot.

No exported user data, Study queue/history, database table, or frozen admitted ordering was removed. `scripts/frozen-maia-inference.mjs`, `scripts/maia-inference.mjs`, their script-side callers, both `all_moves_maia3` inputs, Study message schemas, and the retained `app/lib/study.worker.ts` stay in place. Frozen-rank and server/container proofs remain; only browser-specific engine scheduling and browser/container parity coverage are retired. Architecture impact: none; the canonical source drawings do not represent this orphaned browser-local execution path or claim a Study-to-browser-engine edge, and this change does not alter persistence, service interfaces, or process ownership.

## Optional operator and read-route retirement (batch 02)

The external `tools/analysis-restoration/c_pilot` operator and its optional Intelligence integration probes are retired. They selected bounded historical-game cohorts and drove A-export → Intelligence analysis acceptance; they were not the historical Study converter. The pre-removal implementation and qualification evidence remain in [main snapshot b39dd3a9](https://github.com/aaweaver-actuary/tempo/tree/b39dd3a9ac2e846f1d2a9242a9d8a2eda682f9a8/tools/analysis-restoration/c_pilot). The pilot CLI is no longer supported. Private exports and evidence were not accessed or removed. Content-to-Learning `a_bridge`, Intelligence, Workbench, contracts, and the unresolved historical Study migration requirements remain.

The legacy read routes `GET /api/statistics/overview`, `/api/statistics/breakdown`, and `/api/statistics/insights` and their exclusive overview/breakdown implementations are retired after their UI consumers were removed. Their prior handlers remain in [main.py at b39dd3a9](https://github.com/aaweaver-actuary/tempo/blob/b39dd3a9ac2e846f1d2a9242a9d8a2eda682f9a8/backend/app/main.py). Unknown external clients may lose these optional reports. Daily refresh commands, feature generation, worker receipts, stored statistics, schema, and Study behavior are not retired by this batch. No user data was exported or changed. The unused Workbench sync wrapper is also removed; its hook and pending-command recovery remain.

This batch is source preparation with static checks, not live cutover or an approved `legacy-01` backend cohort. Focused executable tests/builds remain unverified in the cloud environment. The source deletion does not close the broader Study/history/launcher gates.

## Bounded offline-source candidates (2026-10-25)

[RETIREMENT-BATCHES.json](RETIREMENT-BATCHES.json) records exact paths against
[`cb0ed591`](https://github.com/aaweaver-actuary/tempo/commit/cb0ed5910e6bd05894919fd8d49f5dc7bc236975).
All five cohorts remain **candidate**, not an approved `legacy-01` or permission
to merge, deploy, remove tables, or close #390/#391. Retained Study/import,
launcher/update and migration gates (#382/#386/#359/#267/#369) remain separate.

- `ranking-evaluation-01`: remove the standalone offline replay/evaluation package
  and its exclusive fixtures/qualification. The `tempo-evaluate-opening-rankings` CLI is
  unsupported; packaged evidence math, live ranking, frozen ranks and introduction
  remain. Historical purpose and implementation are recoverable at the source SHA.
- `classic-maia-assets-01`: remove the orphaned classic browser worker, its model
  cache adapter and asset plugin, including their build registrations. The public
  `maia-worker.js` and `maia-worker-cache.js` URLs stop shipping. Existing
  `MaiaModels` cache records, model assets, Maia3, inference scripts and the
  independent worker package remain; no user data is exported or deleted.
- `stalemate-offline-cli-01`: retire only the offline archive/mining CLI and its
  exclusive command/process tests. Saved corpora, Study execution/import/grading,
  review-history proofs and the shared backend miner remain. Historical commands
  are unsupported, not replaced by a new producer.

Architecture: the classic model-cache source view is updated in
`database/browser/README.md`. The other two offline entrypoints have no edge in
the maintained architecture drawings; no serving interface, Study dependency,
transaction, data ownership or live process lifecycle changes. These source-only
checks do not establish deployed-client absence or native PostgreSQL qualification.
## Standalone Stockfish worker retirement (stockfish-worker-01)

This bounded candidate removes the 79 tracked files under `workers/stockfish` and only the `stockfish` component registration. The standalone runtime/API (`index.mjs`, `worker.mjs`, `cli.mjs`) and passive Python client (`python/src/tempo_stockfish_client`) executed caller-supplied analysis jobs, cancellation and session fencing without owning Study state. Their build, operator, fixture and qualification files retire with them. The package API, CLI and component test/build selection are no longer supported. Last-good source and regression evidence: [main snapshot cb0ed591](https://github.com/aaweaver-actuary/tempo/tree/cb0ed5910e6bd05894919fd8d49f5dc7bc236975/workers/stockfish).

No outside production import of the concrete worker/client remains. Intelligence's injected-client dispatcher and its negative forbidden-package qualification assertion remain, as do `contracts/analysis-jobs`, Maia, native engine processes/clients, `Dockerfile.engine`, Compose/runtime configuration, browser engines and retained licenses. No user data was accessed, exported or removed; Study, Learning, PGN import, saved analysis/history and database sources are unchanged. This source-only candidate does not approve `legacy-01`, establish live cutover, or close the broader #390/history gates.


## Standalone Maia supervisor retirement candidate

At base `cb0ed5910e6bd05894919fd8d49f5dc7bc236975`, no production consumer
outside `workers/maia` imports `MaiaWorker` or invokes its standalone CLI. This
candidate removes the supervisor, child, CLI, wire/resource helpers and their
exclusive tests/container tooling. It retains core/model/errors/descriptor,
immutable assets, low-level package exports and the root `Dockerfile.maia` /
`scripts/maia-inference.mjs` coverage path unchanged. Shared schema acceptance,
model/provenance, real inference and legacy frozen-helper coverage remain.

The removed runtime API is not replaced with a new framework. Its lifecycle and
resource guarantees are retired rather than attributed to the computation API.
This source-only candidate does not establish deployment absence or complete
legacy-01/#391; real-data/cutover and merge gates remain separate.

## Offline tactics generation and legacy shadow capture retirement (2026-10-25)

Two source-only packets are stacked on PR #411 head
[`8c0b3b5a22fd357607497ac4e50ad9804884c12c`](https://github.com/aaweaver-actuary/tempo/commit/8c0b3b5a22fd357607497ac4e50ad9804884c12c),
the last-good recovery point for these entrypoints. Local tracked-source/import
searches found no serving consumer outside each exclusive implementation set.

- `scripts/build_tactics_decks.py` built the original tactical decks and
  `scripts/expand_tactics_catalog.py` selected/validated the expanded pack catalog.
  Both generation commands are unsupported. The committed manifest, original
  decks, all 692 packs / 17,300 puzzles, licenses/provenance, frontend catalog and
  workspace/card-editor consumers, backend catalog service and Docker assets
  remain byte-for-byte unchanged. [Catalog documentation](../../TACTICS-CATALOG.md)
  identifies the retained invariant tests. No catalog was regenerated.
- `scripts/evaluate_opening_rankings.py` captured current opening candidates or
  compared saved JSON, through `backend/app/services/opening_ranking_snapshot.py`
  and `database/tempo_legacy_database/adapters/opening_ranking/`. This exclusive
  legacy CLI/composition/storage path and its capture-flow drawing are retired;
  both CLI subcommands are unsupported. This is distinct from PR #411's
  `tools/ranking-evaluation` package retirement. [Pure evaluation and history](../../opening-shadow-ranking.md)
  remain, including DTOs/math, selected-policy/serialization tests, card valuation,
  frontier, production admission/capacity, frozen ordering, queue SQL, miss
  projection and the shared connection pool-timeout regression. Only exclusive
  capture/CLI tests, their native shadow proof and its Redis helper retire.

No user data was accessed, exported or removed. No tables, schema, live services,
Study behavior or deployment configuration changed. The retired capture adapter's
canonical drawing is removed with its owning directory; its parent inventory
points to the retained history. Other maintained architecture views are unchanged.
Source checks and native case collection do not establish deployed-client absence,
native PostgreSQL/Redis recovery or merge qualification.


## Retired Repertoire statistics screenshot fixtures

The `repertoire-statistics-visual-fixtures-01` cohort removes only the unused
390px and 1280px PNG baselines under `tests/browser/visual-baselines/`.
[PR #407's source commit](https://github.com/aaweaver-actuary/tempo/commit/4a5a722593fe2d45cff2d01572e3eaa86dcca48b)
removed their screenshot cases. Both PNGs remain recoverable in
[the pre-cleanup source](https://github.com/aaweaver-actuary/tempo/tree/1e7ffb049b9c5489cd0fa4c145d81ba1771813cb/tests/browser/visual-baselines).
Current Study/import and prefix-comparison assertions and fixtures stay unchanged.
This saves 378,527 fixture bytes and credits zero runtime or textual test lines.
No routes, commands, user data or schema change. Architecture impact: none; only
unreferenced test images are removed, with no runtime or ownership boundary change.

## Orphan Games browser action retirement (orphan-games-browser-actions-01)

This source-only candidate retires five uncalled browser action clients left after
PR #407 removed Games UI: finding-card preview/save, game exclusion, finding
curation/decision, defensive-scan refresh and guided-review start/attempt. Their
five exclusive Web Storage adapters and five client-only unit suites retire with
them. Exact paths, preserved checks and protected hashes are in
[RETIREMENT-BATCHES.json](RETIREMENT-BATCHES.json). Last-good implementation and
client-specific replay proofs remain in [main snapshot 1e7ffb04](https://github.com/aaweaver-actuary/tempo/tree/1e7ffb049b9c5489cd0fa4c145d81ba1771813cb).
The app-local command exports are unsupported; no backend HTTP route is retired.

No production caller remained outside each exclusive client/adapter pair. The
Game sync hook/command and complete mixed status-polling suite remain, as do
Study review/queue, imported content, saved exercise execution, Endgames/Tactics
commands, `PendingDiscoverySaves`, generic operation receipts and the Workbench
URL build helper. Existing optional journal values are left cold and unchanged;
the retained connection inspector keeps its existing practice-key selection and
does not cover these optional keys. No existing entry is certified settled,
exported, erased or migrated. Study history, backend/data/schema, launcher and
actual-data gates are unchanged.

Architecture: the owning [browser dictionary](../../../database/browser/adapters/games-analysis/README.md)
now names the four surviving adapters and explicitly records cold optional keys.
Its generic application → named adapter → Web Storage/receipt flow remains accurate
for those survivors; no Study-to-Games edge, transaction or process lifecycle is
introduced. Prior Study/local source-absence builds are retained for unchanged
frontend runtime inputs; live/native/browser qualification remains pending. This
is not `legacy-01` approval or #390/#391 completion.

Focused source-removed candidate evidence: eight installed-tool Vitest files passed
163 tests on host Node 26.12.0, including unchanged sync/status, retained practice,
pending-discovery, PGN pending handling and the Study composition guard. Independent
source review found no unresolved P1/P2 and verified the exact allowlist and 20
protected hashes. Current-main integration preserves the merged tactics/shadow
and statistics-fixture cohorts. The eight-suite and Study/local build receipts
are reused because their relevant executable, dependency, fixture and emission
inputs are unchanged. The changed root TypeScript configuration is checked
separately: strict `npm run typecheck` passed in the official digest-pinned Node
26.10.0 Linux arm64 container, including its normal three-package declaration
build. No units or Study/local builds were repeated for documentation conflicts.
Native/browser/deployment qualification remains unrun.
