# Tempo

Tempo is an initial product mockup and technical skeleton for a fully local chess-opening spaced-repetition trainer. The interface is deliberately board-first: a large responsive chessboard, immediate move feedback, optional hints, compact progress, and a fixed daily queue.

## What works in this pass

- Interactive six-user-move opening drills, using click-to-move or drag-and-drop.
- Equal-sized responsive ranks, legal-move markers, last-move highlighting, automatic opponent replies, and end-of-card ratings.
- Teaching arrows on first exposure and immediately after a wrong attempted move; picking up and replacing a piece does nothing.
- One-click Lichess analysis for the exact current move history, plus a repertoire tree browser for stepping through positions and branches.
- Representative Train, Repertoire, Progress, PGN import, import-success, wrong-answer, and completed-card states.
- Demo review counts persist in browser storage and reset when the local calendar day changes.
- Docker Compose skeleton with a React + TypeScript web app and a FastAPI backend.
- Local SQLite schema for settings, repertoires, cards, locked child cards, and review history.
- PGN variation parsing and stable SHA-256 card IDs derived from canonical starting FEN plus normalized UCI moves.
- A daily-bucket scheduler where “Again” reshuffles the card behind four other reviews in today’s queue.
- A conservative descendant gate and unlock hook for child cards when a parent reaches maturity.

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

## Settled product decisions

- Initial depth is six **user moves**. Opponent replies are included as needed but do not count toward the six.
- “Again” stays in today’s fixed session and is reinserted after four other cards rather than repeated immediately.
- Each repertoire branch gets its own card. Another move that is valid elsewhere in the repertoire is neutral—not a failure—but the teaching arrow redirects the learner to the branch currently being tested.
- Card identity hashes the canonical starting position (piece placement, turn, castling, and en-passant state) plus normalized UCI moves. FEN clock fields are ignored because they do not change the tested position.
- Calendar rollover should follow Anki-like local-day behavior; unusual clock and timezone cases are intentionally low priority.

## Recommended maturity and depth policy

The optimization target is practical recall from the beginning of a playable line, not maximum theoretical depth. A child card unlocks only when its parent has:

1. successful reviews on three distinct calendar days;
2. a current scheduled interval of at least 14 days; and
3. no “Again” among its two most recent reviews.

All ancestors are therefore mature before a deeper child can appear. A later lapse pauses further unlocking along that branch but does not relock descendants the learner has already encountered.

Local response cards alone can become disconnected fragments, so Tempo should also create a low-frequency **integration checkpoint** after every four newly learned user moves. A checkpoint tests the line from the repertoire’s starting FEN through the newest frontier. New descendants beyond that checkpoint remain locked until the checkpoint matures. This preserves fast, focused response cards while regularly proving that the learner can still reach the deep position from the start.

## Remaining implementation decision

- When a revised PGN removes or renames lines, decide whether missing cards are archived automatically or retained until explicitly deleted. Review history should be preserved either way.
