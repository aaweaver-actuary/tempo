# Tempo

Tempo is a functional local-first chess-opening spaced-repetition trainer. The interface is deliberately board-first: a large responsive chessboard, immediate move feedback, guided corrections, compact progress, and a fixed daily queue.

## Local product

Docker Tempo is the full product. Python/SQLite is authoritative for repertoires, daily queues, reviews, accounts, and games. The public GitHub Pages build is a limited practice demo: it does not sync personal games or promise Docker persistence. Use **Start Tempo.command** for the full application.

Every reported defect must have a named regression test before closure. Run `npm test` for the required frontend, backend, Rust, browser, and Docker checks; CI runs the same command. See [CONTRIBUTING.md](CONTRIBUTING.md) and [tests/REGRESSIONS.md](tests/REGRESSIONS.md).

- Interactive opening and tactics drills using Lichess's Chessground board, with click-to-move or drag-and-drop.
- Fluid responsive sizing, Cburnett and Merida piece sets, three board palettes, legal-move markers, last-move highlighting, automatic opponent replies, and native SVG teaching arrows.
- Teaching arrows on first exposure and immediately after a wrong attempted move; picking up and replacing a piece does nothing.
- Answers remain hidden: the move trail reveals only moves already played.
- One-click Lichess analysis for the exact current move history, plus a repertoire tree browser for stepping through positions and branches.
- Train, Repertoire, Builder, Games, Progress, PGN import, wrong-answer, guided-review, and completed-card states.
- Binary Correct/Again grading with automatic clean solves, first-pass reinforcement at the end of the day, Again placement after four cards, and persisted queue ordering.
- Production Docker Compose with a compiled React + TypeScript application, Nginx, and a FastAPI backend.
- Local SQLite schema for settings, repertoires, cards, locked child cards, and review history.
- PGN variation parsing and stable SHA-256 card IDs derived from canonical starting FEN plus normalized UCI moves.
- FSRS 6 scheduling at 92% desired retention, capped lateness benefit, 2.5× interval growth, and a 365-day maximum.
- A stability-based descendant gate requiring three successful review days and no recent lapse.
- Thirteen tactical motifs, each with Easy, Medium, and Hard stages of 100 puzzles and a 250-puzzle focused stage. Discovery through play controls admission to the daily queue.
- A playable analysis board with live repertoire filtering, authenticated Lichess and Masters Explorer results, real local Stockfish 19 and Maia 3 analysis, branch editing, 80/90/95% coverage targets, persistent preferences, source-aware arrows, and keyboard history navigation.
- Incremental Lichess and Chess.com game ingestion, locally cached normalized PGNs, divergence classification, comparison summaries, and a Games workspace for sending gaps to Builder.
- A browser-first Rust core for canonical FEN/UCI validation, stable card identity, and chess-aware position distance, compiled to WebAssembly behind a typed adapter.
- Versioned IndexedDB stores plus verified one-time SQLite transfer and passphrase-encrypted portable backups. SQLite remains an untouched recovery source during migration.

## Run locally

### Easiest on a Mac

Double-click **Start Tempo.command** in this folder. Tempo starts its private local database and opens [http://localhost:3000](http://localhost:3000) automatically. Keep the Terminal window open while using Tempo; press Control-C there when you want to stop it.

If macOS blocks the launcher the first time, right-click **Start Tempo.command**, choose **Open**, and confirm once. The launcher uses Docker Desktop when it is running and otherwise starts the included web and Python projects directly.

### Docker Compose

```bash
docker compose up --build
```

Open `http://localhost:3000`. The local API is available at `http://localhost:8000`, and all durable data is stored in the named Docker volume `tempo-data`, independent of the checkout location. See [storage operations](docs/STORAGE.md) for backups and restore.

You can study in Docker Tempo now. Reviews, FSRS state, daily queue order, reinforcement, guided attempts, tactic discovery progress, repertoire notes, and teaching history are saved automatically in SQLite. Rebuilding or recreating the containers retains the host `data` directory; deleting that directory deletes your study data, so keep a backup. The mandatory Docker test recreates containers and verifies every SQLite store checksum and the exact queue order.

New cards get one unassisted reinforcement later today and a review tomorrow before normal FSRS intervals. Later reviews do not automatically reveal teaching arrows; a mistake or Show move still provides guidance. Clean tactic discovery uses the configured light first interval; failed discoveries and subsequent lapses join normal review scheduling.

Tempo preloads tab data and the first unfinished Hanging Pieces stage in the background. Puzzle validation, repertoire diagnostics/indexing, similarity, and transposition matching run in a study worker. Builder opens with a source-comparison table, including covered moves, rather than burying those details below editing tools. Move and capture audio use the official Lichess standard chess recordings.

The hosted private Site uses local browser storage and representative game data. Docker Compose runs the full local FastAPI + SQLite path, including provider sync and durable review state.

### Static browser-only preview

Double-click **Preview Tempo Static.command** to build the Rust/WebAssembly bundle when needed, serve the generated site over HTTP, and open [http://127.0.0.1:4173/tempo/](http://127.0.0.1:4173/tempo/). Keep its Terminal window open while using the preview.

Do not open `static/index.html` directly. It is Vite source, not the generated application, and `file://` pages cannot run Tempo's modules, Web Workers, WebAssembly engines, or service worker. The launcher serves the deployable `pages-dist/` output with the browser security context those features require.

## Structure

```text
app/                       React + TypeScript application
app/domain/                Shared chess/product models and transport adapters
app/state/                 Training state and focused selectors/actions
backend/app/main.py        FastAPI routes
backend/app/database.py    Local SQLite schema and connection
backend/app/services/      PGN, identity, and scheduling logic
tempo-core/                Rust/WASM deterministic chess and scheduling core
app/lib/tempo-db.ts        Versioned browser persistence and SQLite transfer
static/                    GitHub Pages entry point (base path /tempo/)
docker-compose.yml         Local two-service runtime
```

The [code organization audit](docs/CODE-ORGANIZATION-AUDIT.md) explains the
current boundaries, the backend service clusters, and a staged path for making
the code easier to navigate. Maintained source, test, asset, and documentation
directories also contain a local `README.md`; start with the README closest to
the code you are changing.

## Static build

```bash
npm run build:static
npm run verify:static
npm run preview:static
```

This compiles `tempo-core` to WebAssembly and emits an offline-capable static site in `pages-dist/`. Network orchestration, IndexedDB, encrypted backups, engines, and UI state remain in TypeScript. Docker/Python stays supported as the compatibility and migration source until each deterministic service passes parity fixtures against Rust.

The public practice demo is [https://aaweaver-actuary.github.io/tempo/](https://aaweaver-actuary.github.io/tempo/). GitHub Actions runs the complete local-product suite before publishing the limited `pages-dist/` demonstration. Rust identity/validation/prefix fixtures are shared with Python; scheduling remains Python-authoritative until full parity is proven.

## Settled product decisions

- Initial depth is six **user moves**. Opponent replies are included as needed but do not count toward the six.
- “Again” stays in today’s fixed session and is reinserted after four other cards rather than repeated immediately.
- Each repertoire branch gets its own card. Another move that is valid elsewhere in the repertoire is neutral—not a failure—but the teaching arrow redirects the learner to the branch currently being tested.
- Card identity hashes the canonical starting position (piece placement, turn, castling, and en-passant state) plus normalized UCI moves. FEN clock fields are ignored because they do not change the tested position.
- Calendar rollover should follow Anki-like local-day behavior; unusual clock and timezone cases are intentionally low priority.
- Tactics enter study through the Tactics workspace. Clean discovery starts a light schedule; failed discovery reappears today. Lichess's first UCI move is applied as the setup move; the remaining moves form the card answer. The daily new-card allowance applies to opening cards, while due reviews remain in the queue.

## Lichess assets and puzzle data

Tempo now uses the official `@lichess-org/chessground` package instead of a hand-built board. The default Cburnett set and optional Merida set come from Lichess. Chessground is GPL-3.0-or-later; both piece sets are GPL-2.0-or-later. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The Lichess puzzle database is public domain and provides FEN, UCI solution moves, rating, popularity, motifs, and source-game URLs. Tempo includes 7,150 globally distinct, validated records in `public/data/tactics-decks.json`: 100 in each fundamental stage and 250 in each focused stage for thirteen motifs. Focused stages cover ratings 1250–2000. Opening and puzzle scheduling stay independent even when their due cards are shuffled into one session.

Lichess now requires authentication for Opening Explorer requests. Tempo uses Lichess's PKCE flow, requests no account permissions, and keeps the access token in session storage. Explorer requests authenticate each source independently; local repertoire-coverage work receives the token through a temporary in-memory backend session and pauses until a browser session registers credentials. The token is not stored with queued work or Explorer cache entries. Stockfish 19 runs locally in WebAssembly; Maia 3 runs locally through its simplified ONNX model. Engine inputs and repertoire data do not leave the browser.

## Recommended maturity and depth policy

The optimization target is practical recall from the beginning of a playable line, not maximum theoretical depth. A child card unlocks only when its parent has:

1. successful reviews on three distinct calendar days;
2. a current scheduled interval of at least 14 days; and
3. no “Again” among its two most recent reviews.

All ancestors are therefore mature before a deeper child can appear. A later lapse pauses further unlocking along that branch but does not relock descendants the learner has already encountered.

Local response cards alone can become disconnected fragments, so Tempo should also create a low-frequency **integration checkpoint** after every four newly learned user moves. A checkpoint tests the line from the repertoire’s starting FEN through the newest frontier. New descendants beyond that checkpoint remain locked until the checkpoint matures. This preserves fast, focused response cards while regularly proving that the learner can still reach the deep position from the start.

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
the study worker. Maia inference runs in a separate worker. Board input and DOM
updates remain in TypeScript; Python/SQLite remains authoritative. Rust/WASM
continues behind the existing golden parity fixtures. Recent readiness, move
paint, and workspace switch timings are available in the browser Performance
panel as `tempo:*` measurements.

## Remaining implementation decision

- When a revised PGN removes or renames lines, decide whether missing cards are archived automatically or retained until explicitly deleted. Review history should be preserved either way.
