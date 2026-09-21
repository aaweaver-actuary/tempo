# Code organization audit

Status: initial documentation pass, 2026-09-20

This is a navigational audit, not a behavior change or a proposed rewrite. It
uses the checked-out source tree, current imports, route declarations, schema
definitions, and existing architecture documents. The working tree already
contains user changes; those changes were left untouched.

## Executive summary

Tempo has a sound high-level product split:

```text
React/TypeScript UI  ->  FastAPI transport  ->  SQLite authority
       |                       |                    |
  browser-only demo      domain services       durable jobs/data
       |
 Rust/WASM deterministic primitives behind a typed adapter
```

The main readability problem is not that there are too many files. It is that
several files cross boundaries that the directory names suggest are separate.
The most important examples are:

1. `backend/app/main.py` is both the HTTP adapter and a large portion of the
   application layer. It contains route handlers, queue admission, import and
   export workflows, game workflows, and response shaping.
2. `backend/app/database.py` owns connection policy, initial schema creation,
   compatibility migrations, indexes, and optimization. This makes storage
   changes hard to review in isolation.
3. `backend/app/services/` contains recognizable domains, but they are flat.
   Game ingestion, game-derived insights, repertoire diagnostics, and review
   scheduling are mixed at one level. Several modules reconstruct positions or
   implement adjacent background-job behavior independently.
4. Large frontend views such as `home_view.tsx`, `analysis_view.tsx`, and
   `games_view.tsx` combine screen composition, API calls, validation, worker
   coordination, and domain workflow decisions.
5. Documentation has lagged implementation. For example,
   `GAMES_SYNC_IMPLEMENTATION.md` still describes game-sync files as “NEW” and
   includes an old implementation checklist even though the feature is now a
   substantial service cluster.

The safest simplification is staged namespace and boundary work: document the
current seams, group related modules without changing behavior, extract route
and persistence adapters, then remove duplicated helpers after parity tests are
in place.

## Evidence snapshot

| Area | Current signal | Reading implication |
| --- | --- | --- |
| Backend HTTP | `backend/app/main.py` is about 3,200 lines with route groups for queue, repertoires, tactics, endgames, games, and statistics | Route registration and use-case orchestration are coupled |
| Backend storage | `backend/app/database.py` is about 950 lines and declares the full SQLite schema plus migration logic | Schema review and connection-policy review are coupled |
| Backend services | 30+ flat modules under `backend/app/services` | File count is manageable; domain grouping is missing |
| Backend derived work | Sync, analysis, findings, events, coverage, integrity, priorities, and statistics all have durable/background paths | A shared job contract and domain-level ownership would reduce surprise |
| Frontend screens | `home_view.tsx` and `analysis_view.tsx` are each over 1,000 lines; several other views are several hundred lines | UI composition and workflow code are difficult to scan together |
| Frontend support code | `app/lib` mixes persistence, transport, workers, engines, imports, and chess algorithms | “lib” is a catch-all rather than a boundary |
| Runtime modes | Docker is authoritative; the static build is a marked practice demo with browser storage | Every new feature needs an explicit runtime ownership decision |
| Documentation | Root and feature docs exist, but most maintained directories lacked local guides | Navigation depends on knowing the whole repository |

## Current domains

### Backend service clusters

| Proposed domain | Current modules | Responsibility |
| --- | --- | --- |
| `games/ingestion` | `game_sync`, `game_sync_coordinator`, `game_record`, `game_normalizer`, `lichess_client`, `chesscom_client` | Fetch, normalize, persist, lease, and derive imported games |
| `games/insights` | `game_analysis`, `game_findings`, `gameplay_events`, `guided_review`, `tactical_opportunities`, `statistics` | Turn analyzed games into findings, events, recommendations, review sessions, and reports |
| `repertoire` | `repertoire_comparison`, `repertoire_conflicts`, `repertoire_coverage`, `repertoire_integrity`, `introduction_priorities` | Build repertoire indexes, compare played lines, find gaps/conflicts, and prioritize introductions |
| `learning` | `cards`, `review_service`, `scheduler`, `prefix_split`, `pgn` | Card identity, review scheduling, queue effects, and line parsing/editing |
| `tactics` | `puzzles`, `tactical_catalog`, `motif_detectors`, `tactical_opportunities` | Packaged curriculum and motif classification |
| `providers` | `lichess_client`, `chesscom_client`, explorer calls in `main.py` | External provider requests, filtering, rate/error handling, and cached responses |
| `platform` | `activity_gate`, connection use in services, `analysis`, `endgames` | Cross-cutting concurrency policy and small platform capabilities |

These are grouping proposals, not instructions to combine every module. In
particular, motif detection is reusable domain logic and should remain separate
from persistence-heavy game findings even if both live below a `games` or
`tactics` namespace.

### Frontend boundaries

- `app/domain` is the strongest existing boundary: product types, branded
  primitives, schemas, and external-record adapters belong here and should stay
  independent of React.
- `app/components` owns reusable UI and board ownership. The persistent board
  shell is a useful seam and should remain the only owner of the Chessground
  instance.
- `app/state` owns cross-workspace state. Keep selectors and lifecycle rules
  here rather than duplicating them in each view.
- `app/hooks` owns React lifecycle integration with workers, background APIs,
  and timers.
- `app/lib` currently contains several different categories. The first useful
  split is `api`, `persistence`, `workers`, `engines`, and `chess` rather than a
  broad rewrite.
- `app/views` should primarily compose a screen. Fetching, validation, and
  multi-step workflows can move behind hooks or feature modules over time.

## Consolidation opportunities

### High value, low semantic risk

1. Introduce a shared backend position/line utility for canonical FEN keys,
   replay-to-ply, and repeated “trained color / response move” rules. Start by
   adding parity tests around existing behavior, then migrate callers one at a
   time. `repertoire_comparison`, `repertoire_coverage`, `repertoire_integrity`,
   `game_findings`, and `gameplay_events` are the first callers to compare.
2. Introduce a single provider-client policy for HTTP status validation,
   retryability, provider labels, and structured error reporting. Lichess and
   Chess.com should retain provider-specific pagination/date logic while sharing
   the boundary behavior.
3. Give all durable background handlers the same small interface: claim one
   item, compute outside SQLite, publish one idempotent result, and persist an
   actionable failure. The rules already exist in
   [BACKGROUND-WORK.md](BACKGROUND-WORK.md); the code should make the contract
   visible instead of relying on each module to remember it.
4. Keep the new directory READMEs current as part of structural changes. A
   README should state purpose, ownership, allowed dependencies, and the first
   test or command to run.

### High value, higher coordination cost

1. Split `main.py` into route modules such as `api/queue.py`,
   `api/repertoire.py`, `api/tactics.py`, `api/games.py`, and `api/insights.py`.
   Route functions should validate transport data and call use cases; they
   should not contain long database workflows.
2. Split `database.py` into connection policy, schema definitions, and explicit
   migrations. Keep one public connection seam while the internal files become
   independently reviewable.
3. Group backend services into domain packages. Use temporary re-export shims
   from the old module paths so imports and external callers can migrate without
   a flag-day change.
4. Extract frontend feature modules from the largest views. A feature module
   should contain its API adapter, response schemas, workflow hook, and view
   components; shared domain types stay in `app/domain`.

### Do not combine prematurely

- SQLite persistence and browser IndexedDB persistence are different authorities
  in different runtime modes; share schemas/adapters where useful, not storage
  implementations.
- Rust/WASM deterministic logic and Python compatibility logic should continue
  to share fixtures before either implementation is removed.
- Motif detection, engine analysis, and user-confirmed card creation have
  different trust and side-effect profiles. They can share data contracts but
  should not become one “analysis service.”
- The static demo should not gain server-only behavior by silently falling back
  to sample records. A missing provider or API must remain an actionable error.

## Suggested target layout

This is a destination for incremental work, not a request to move everything at
once:

```text
backend/app/
  api/                 transport-only route modules
  application/         use cases and workflow orchestration
  domain/              pure chess, learning, repertoire, and game rules
  providers/           Lichess, Chess.com, explorer, and tablebase clients
  storage/             connection policy, schema, migrations, repositories
  services/            durable jobs that do not fit a single use case

app/
  api/                 typed request/response adapters
  domain/              types, schemas, brands, and pure adapters
  features/            screen-specific workflows and components
  components/          reusable UI and board shell
  state/               cross-workspace state
  workers/              study, Maia, and engine worker boundaries
  persistence/         IndexedDB and backup/migration code
```

The existing names should be preserved through compatibility imports during the
transition. Namespace moves are valuable only if they reduce the number of
places a reader must inspect for one user workflow.

## Staged work plan

1. **Navigation baseline:** keep the directory READMEs and this audit accurate;
   update stale implementation guides when code replaces a planned checklist.
2. **Dependency baseline:** add or maintain import checks that prevent domain
   modules from importing React, routes from owning persistence policy, and
   background jobs from holding SQLite across computation.
3. **Shared pure helpers:** extract and test position/line/provider contracts.
4. **Backend transport split:** move route groups out of `main.py` while keeping
   endpoint paths and response models unchanged.
5. **Backend storage split:** separate connection policy, schema, and migration
   files without changing the authoritative SQLite model.
6. **Domain namespace moves:** group services with compatibility re-exports;
   delete a shim only after import and integration coverage confirms the move.
7. **Frontend feature extraction:** reduce view files by moving data workflows
   into feature hooks and typed API modules.
8. **Parity and cleanup:** run the complete required suite, then remove obsolete
   shims and update this audit with measured results.

Each behavior change still needs the named regression coverage required by
`CONTRIBUTING.md` and `tests/REGRESSIONS.md`. Documentation-only changes do not
need a new regression test because they do not change runtime behavior.
