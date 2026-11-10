# Tempo

## Product specification

Tempo is a single-user chess study app. The [product specification](SPEC.md) defines three journeys.

- **Study** opens an existing repertoire, teaches and reviews moves, saves the result and continues to the next playable card. Import and correction, blocking answer conflicts, and Study settings are available from Train.
- **Tactics** captures puzzles including Chess.com Puzzle Rush PGN, activates packs, and saves solving progress. Captured tactics can enter ordinary Training.
- **Game Review** lists and replays games from their original position, using configured Chess.com and Lichess refresh. It identifies the first repertoire departure and submits eligible own departures to Study through the existing learning authority.

The local product uses PostgreSQL for durable records, with Redis and Celery for command execution and recovery. Browser journals preserve offline review and retry identity. The normal Mac entry is **Start Tempo.command**. See [phone training](docs/PHONE-TRAINING.md) for prepared offline practice.

Use [the testing guide](docs/testing.md) to select component and boundary checks. See [CONTRIBUTING.md](CONTRIBUTING.md) for delivery and resource ownership.

## Run locally

This section is an operating reference, not a development acceptance step. Install/start/restart/maintenance commands may change the registered installation. Run them only within an authorized operational assignment, not to validate a feature branch or documentation edit.

### Easiest on a Mac

For the existing PostgreSQL installation, install the management command once:

```sh
./tempo install
```

After a merge, run `tempo start`. It checks the exact revision's complete CI,
updates a clean main checkout, prepares images, verifies a backup before any
migration, and opens [http://localhost:3000](http://localhost:3000) after readiness
passes. Tempo runs in the background; use `tempo stop` to stop it. `tempo doctor`
explains startup problems, and `tempo start --plan` previews maintenance.
See [CLI commands and recovery](docs/TEMPO-CLI.md).

You can also double-click **Start Tempo.command**; it uses the same CLI and
registers the existing installation on its first run. Initial SQLite cutover
remains a separate [PostgreSQL maintenance procedure](docs/POSTGRES-MAINTENANCE.md).

If macOS blocks the launcher the first time, right-click **Start Tempo.command**, choose **Open**, and confirm once. Docker Desktop is required. The launcher checks Docker and the PostgreSQL Compose configuration before starting.

### Docker Compose

Use `tempo start` for normal operation. The manual Compose procedures below remain available for maintenance diagnosis; they do not replace the CLI's source, backup, migration, and readiness checks.

Open Tempo at `http://localhost:3000`. Its local API is at `http://localhost:8000`. External volumes preserve PostgreSQL and Redis state across container replacement. See the [maintenance guide](docs/POSTGRES-MAINTENANCE.md) for backup and restore.

### Static browser-only preview

Double-click **Preview Tempo Static.command** to build the Rust/WebAssembly bundle when needed, serve the generated site over HTTP, and open [http://127.0.0.1:4173/tempo/](http://127.0.0.1:4173/tempo/). Keep its Terminal window open while using the preview.

Do not open `static/index.html` directly. It is Vite source, not the generated application, and `file://` pages cannot run Tempo's modules, Web Workers, the chess WebAssembly core, or service worker. The launcher serves the deployable `pages-dist/` output with the browser security context those features require.

## Structure

```text
app/                       React + TypeScript application
app/domain/                Shared chess/product models and transport adapters
app/state/                 Training state and focused selectors/actions
backend/app/main.py        FastAPI routes
database/                  Database owners, DDL/adapters/tooling, ER charts and data dictionaries
backend/app/services/      PGN, identity, and scheduling logic
tempo-core/                Rust/WASM deterministic chess and scheduling core
database/browser/tempo-db.ts        Versioned browser persistence and SQLite transfer
static/                    GitHub Pages entry point (base path /tempo/)
docker-compose.yml         PostgreSQL API, web, and Celery workers
```

The [component reconstruction guide](docs/architecture/component-reconstruction.md)
describes the adopted target, current transition, and migration index. The
[code organization audit](docs/CODE-ORGANIZATION-AUDIT.md) is historical
evidence from its dated baseline. Maintained source, test, asset, and
documentation directories also contain a local `README.md`. Read the relevant owner README for APIs, state ownership, and commands. Its linked plans and operating procedures do not enlarge the assignment. Consult only the parts needed for the changed boundary.

## Static build

```bash
npm run build:static
npm run verify:static
npm run preview:static
```

This compiles `tempo-core` to WebAssembly and emits an offline-capable static site in `pages-dist/`. Network orchestration, IndexedDB, encrypted backups and UI state remain in TypeScript. This describes the static build; it does not assign a general Rust port or runtime migration. Docker/Python support is not contingent on completing every deterministic service port.

The public practice demo is [https://aaweaver-actuary.github.io/tempo/](https://aaweaver-actuary.github.io/tempo/). GitHub Actions runs the complete local-product suite before publishing the limited `pages-dist/` demonstration. Rust identity/validation/prefix fixtures are shared with Python; scheduling remains Python-authoritative until full parity is proven.

## Earlier implementation decisions

These notes describe earlier behavior. [SPEC.md](SPEC.md) governs the target product, particularly the strict per-repertoire own-response rule, introductions, and connected practice. This documentation update does not change runtime behavior.

- Initial depth is six **user moves**. Opponent replies are included as needed but do not count toward the six.
- “Again” stays in today’s fixed session and is reinserted after four other cards rather than repeated immediately.
- Each repertoire branch gets its own card. Another move that is valid elsewhere in the repertoire is neutral—not a failure—but the teaching arrow redirects the learner to the branch currently being tested.
- Card identity hashes the canonical starting position (piece placement, turn, castling, and en-passant state) plus normalized UCI moves. FEN clock fields are ignored because they do not change the tested position.
- Calendar rollover should follow Anki-like local-day behavior; unusual clock and timezone cases are intentionally low priority.
- Tactics enter study through the Tactics workspace. Clean discovery starts a light schedule; failed discovery reappears today. Lichess's first UCI move is applied as the setup move; the remaining moves form the card answer. The daily new-card allowance applies to opening cards, while due reviews remain in the queue.

## Lichess assets and puzzle data

Tempo now uses the official `@lichess-org/chessground` package instead of a hand-built board. The default Cburnett set and optional Merida set come from Lichess. Chessground is GPL-3.0-or-later; both piece sets are GPL-2.0-or-later. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The Lichess puzzle database is public domain and provides FEN, UCI solution moves, rating, popularity, motifs, and source-game URLs. Tempo includes 7,150 globally distinct, validated records in `public/data/tactics-decks.json`: 100 in each fundamental stage and 250 in each focused stage for thirteen motifs. Focused stages cover ratings 1250–2000. Opening and puzzle scheduling stay independent even when their due cards are shuffled into one session.

## Validated domain and responsive boards

Zod 4 schemas live in `app/domain/schemas`. Transport adapters validate external
records before producing branded FENs, moves, and identifiers. Controlled Tempo
and worker envelopes are strict; third-party move data permits additional fields
but validates all consumed values. Invalid records are set aside with an
exportable diagnostic; their source data is not deleted or silently repaired.

Chessground uses one measured, square pixel surface and one instance per board.
Input availability comes from the queue-entry attempt lifecycle, not a separate
lock flag. Background queue refreshes preserve an active attempt; replacement
entries cancel old reply and completion timers.

Queue/repertoire validation, puzzle preparation, and position comparisons run in
the study worker. Board input and DOM updates remain in TypeScript. Rust/WASM
continues behind the existing golden parity fixtures. Recent readiness, move
paint, and workspace switch timings are available in the browser Performance
panel as `tempo:*` measurements. PostgreSQL is authoritative for Docker product
records; SQLite remains a compatibility and verified-import path.

## Remaining implementation decision

- When a revised PGN removes or renames lines, decide whether missing cards are archived automatically or retained until explicitly deleted. Review history should be preserved either way.

Database source, keys and stored fields: [database/README.md](database/README.md).
