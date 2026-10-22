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
| Lichess Explorer, Maia, probability estimation and adaptive ranking | **RECORD THEN RETIRE** | Consuming already-persisted frozen ordering is distinct from future ranking production; `backend/app/services/repertoire_coverage.py`, `introduction_priorities.py` |
| Discovery suggestions, coverage opportunities and automatic admissions | **RECORD THEN RETIRE** | Existing introduced cards remain in Study; `repertoire_opportunities.py`, `discovery_admission.py` |
| Defense/threat generation, endgame generation, tactical finding producers | **RECORD THEN RETIRE** | Preserve any already admitted Study exercises and genuine review history; `backend/app/services/threat_*.py`, old endgame routes |
| Guided game review, chess statistics, motif insights, dashboards | **RECORD THEN RETIRE** | Old endpoints and data can be described/reimplemented later; `backend/app/main.py` Games/statistics/guided sections |
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
