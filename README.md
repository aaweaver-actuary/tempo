# Tempo

Tempo is a functional local-first chess-opening spaced-repetition trainer. The interface is deliberately board-first: a large responsive chessboard, immediate move feedback, guided corrections, compact progress, and a fixed daily queue.

## What works in this pass

- Interactive opening and tactics drills using Lichess's Chessground board, with click-to-move or drag-and-drop.
- Fluid responsive sizing, Cburnett and Merida piece sets, three board palettes, legal-move markers, last-move highlighting, automatic opponent replies, and native SVG teaching arrows.
- Teaching arrows on first exposure and immediately after a wrong attempted move; picking up and replacing a piece does nothing.
- Answers remain hidden: the move trail reveals only moves already played.
- One-click Lichess analysis for the exact current move history, plus a repertoire tree browser for stepping through positions and branches.
- Train, Repertoire, Analysis, Games, Progress, PGN import, wrong-answer, guided-review, and completed-card states.
- Binary Correct/Again grading with automatic clean solves, first-pass reinforcement at the end of the day, Again placement after four cards, and persisted queue ordering.
- Docker Compose skeleton with a React + TypeScript web app and a FastAPI backend.
- Local SQLite schema for settings, repertoires, cards, locked child cards, and review history.
- PGN variation parsing and stable SHA-256 card IDs derived from canonical starting FEN plus normalized UCI moves.
- FSRS 6 scheduling at 92% desired retention, capped lateness benefit, 2.5× interval growth, and a 365-day maximum.
- A stability-based descendant gate requiring three successful review days and no recent lapse.
- Twelve packaged Lichess tactics decks: forks, pins, skewers, and discovered attacks at easy, medium, and hard levels, with 100 cards in every deck.
- A playable analysis board with live repertoire filtering, authenticated Lichess and Masters Explorer results, real local Stockfish 19 and Maia 3 analysis, branch editing, 80/90/95% coverage targets, persistent preferences, source-aware arrows, and keyboard history navigation.
- Incremental Lichess and Chess.com game ingestion, locally cached normalized PGNs, divergence classification, comparison summaries, and a Games workspace for sending gaps to Analysis.

## Run locally

```bash
docker compose up --build
```

Open `http://localhost:3000`. The local API is available at `http://localhost:8000`, and all durable data is stored in `./data/tempo.db` on the host computer.

The hosted private Site uses local browser storage and representative game data. Docker Compose runs the full local FastAPI + SQLite path, including provider sync and durable review state.

## Structure

```text
app/                       React + TypeScript product mockup
backend/app/main.py        FastAPI routes
backend/app/database.py    Local SQLite schema and connection
backend/app/services/      PGN, identity, and scheduling logic
docker-compose.yml         Local two-service runtime
```

## Settled product decisions

- Initial depth is six **user moves**. Opponent replies are included as needed but do not count toward the six.
- “Again” stays in today’s fixed session and is reinserted after four other cards rather than repeated immediately.
- Each repertoire branch gets its own card. Another move that is valid elsewhere in the repertoire is neutral—not a failure—but the teaching arrow redirects the learner to the branch currently being tested.
- Card identity hashes the canonical starting position (piece placement, turn, castling, and en-passant state) plus normalized UCI moves. FEN clock fields are ignored because they do not change the tested position.
- Calendar rollover should follow Anki-like local-day behavior; unusual clock and timezone cases are intentionally low priority.
- Tactics use the same daily queue and review controls, but have a separate daily cap so puzzles cannot crowd out opening work. Lichess's first UCI move is applied as the setup move; the remaining moves form the card answer.

## Lichess assets and puzzle data

Tempo now uses the official `@lichess-org/chessground` package instead of a hand-built board. The default Cburnett set and optional Merida set come from Lichess. Chessground is GPL-3.0-or-later; both piece sets are GPL-2.0-or-later. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The Lichess puzzle database is public domain and provides FEN, UCI solution moves, rating, popularity, motifs, and source-game URLs. Tempo includes 1,200 deterministic records in `public/data/tactics-decks.json`: 100 for each motif/difficulty pair. Easy is rating 700–1100, medium is 1101–1500, and hard is 1501–2000; all selected puzzles have popularity of at least 70, at least 100 plays, and rating deviation no greater than 110. Opening and puzzle scheduling stay independent even when their due cards are shuffled into one session.

Lichess now requires authentication for Opening Explorer requests. Tempo uses Lichess's PKCE flow, requests no account permissions, and keeps the access token in session storage. Stockfish 19 runs locally in WebAssembly; Maia 3 runs locally through its simplified ONNX model. Engine inputs and repertoire data do not leave the browser.

## Recommended maturity and depth policy

The optimization target is practical recall from the beginning of a playable line, not maximum theoretical depth. A child card unlocks only when its parent has:

1. successful reviews on three distinct calendar days;
2. a current scheduled interval of at least 14 days; and
3. no “Again” among its two most recent reviews.

All ancestors are therefore mature before a deeper child can appear. A later lapse pauses further unlocking along that branch but does not relock descendants the learner has already encountered.

Local response cards alone can become disconnected fragments, so Tempo should also create a low-frequency **integration checkpoint** after every four newly learned user moves. A checkpoint tests the line from the repertoire’s starting FEN through the newest frontier. New descendants beyond that checkpoint remain locked until the checkpoint matures. This preserves fast, focused response cards while regularly proving that the learner can still reach the deep position from the start.

## Remaining implementation decision

- When a revised PGN removes or renames lines, decide whether missing cards are archived automatically or retained until explicitly deleted. Review history should be preserved either way.
