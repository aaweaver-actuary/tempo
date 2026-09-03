# Tempo

Tempo is an initial product mockup and technical skeleton for a fully local chess-opening spaced-repetition trainer. The interface is deliberately board-first: a large responsive chessboard, immediate move feedback, optional hints, compact progress, and a fixed daily queue.

## What works in this pass

- Interactive opening drill for `1. e4 c5 2. Nf3 d6 3. d4 cxd4`, using click-to-move or drag-and-drop.
- Legal-move markers, last-move highlighting, wrong-move feedback, hint highlighting, automatic opponent replies, and end-of-card ratings.
- Representative Train, Repertoire, Progress, PGN import, import-success, wrong-answer, and completed-card states.
- Demo review counts persist in browser storage and reset when the local calendar day changes.
- Docker Compose skeleton with a React + TypeScript web app and a FastAPI backend.
- Local SQLite schema for settings, repertoires, cards, locked child cards, and review history.
- PGN variation parsing and stable SHA-256 card IDs derived from normalized starting FEN plus move text.
- A provisional daily-bucket scheduler and unlock hook for child cards when a parent reaches maturity.

## Run locally

```bash
docker compose up --build
```

Open `http://localhost:3000`. The local API is available at `http://localhost:8000`, and all durable data is stored in `./data/tempo.db` on the host computer.

The hosted mockup is intentionally sample-data-only. The Docker Compose path is the intended fully local product shape; its next implementation pass should connect the existing screens to the FastAPI endpoints.

## Structure

```text
app/                       React + TypeScript product mockup
backend/app/main.py        FastAPI routes
backend/app/database.py    Local SQLite schema and connection
backend/app/services/      PGN, identity, and scheduling logic
docker-compose.yml         Local two-service runtime
```

## Unresolved decisions that materially affect implementation

1. **Depth unit and side:** Does “6 moves” mean six plies or six full moves, and how is the trained color chosen for each PGN or chapter?
2. **Maturity rule:** Which signal unlocks a child card—interval length, consecutive successes, stability/difficulty, or a combination—and can a lapse relock descendants?
3. **Fixed-day handling of “Again”:** Should a failed card repeat inside today’s already-fixed session, or become due only on a later calendar day?
4. **Branch acceptance:** If several repertoire responses are valid from one position, should any valid move pass the card, or should separate cards test each intended branch?
5. **Canonical identity:** Should hashing use SAN or UCI moves, and should FEN clock fields be removed so equivalent positions and transpositions merge predictably?
6. **Re-import semantics:** When a revised PGN removes or renames lines, should Tempo archive missing cards, preserve their history, or keep them active until explicitly deleted?
7. **Calendar edge cases:** How should timezone changes, daylight-saving transitions, missed days, and manually changed system clocks affect the next daily queue?
