import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DB_PATH = Path(os.getenv("TEMPO_DB_PATH", "./data/tempo.db"))


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DB_PATH)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    try:
        yield database
        database.commit()
    finally:
        database.close()


def initialize() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            initial_depth INTEGER NOT NULL DEFAULT 6,
            timezone TEXT NOT NULL DEFAULT 'local',
            new_cards_per_day INTEGER NOT NULL DEFAULT 10
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS repertoires (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY,
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('prefix', 'response', 'checkpoint')),
            start_fen TEXT NOT NULL,
            moves_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('locked', 'new', 'learning', 'mature')),
            due_date TEXT NOT NULL,
            interval_days INTEGER NOT NULL DEFAULT 0,
            ease REAL NOT NULL DEFAULT 2.5,
            repetitions INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            unlock_after_card_id TEXT REFERENCES cards(id) ON DELETE SET NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_cards_due_state
        ON cards(due_date, state)
        """,
        """
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            rating TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            previous_interval INTEGER NOT NULL,
            next_interval INTEGER NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_reviews_card_reviewed_at
        ON reviews(card_id, reviewed_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS puzzle_decks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            themes_json TEXT NOT NULL,
            min_rating INTEGER NOT NULL,
            max_rating INTEGER NOT NULL,
            daily_limit INTEGER NOT NULL DEFAULT 5,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS puzzles (
            id TEXT PRIMARY KEY,
            deck_id TEXT NOT NULL REFERENCES puzzle_decks(id) ON DELETE CASCADE,
            source_fen TEXT NOT NULL,
            start_fen TEXT NOT NULL,
            moves_json TEXT NOT NULL,
            rating INTEGER NOT NULL,
            popularity INTEGER NOT NULL,
            themes_json TEXT NOT NULL,
            game_url TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new', 'learning', 'mature')),
            due_date TEXT NOT NULL,
            interval_days INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_puzzles_deck_due_state
        ON puzzles(deck_id, due_date, state)
        """,
    ]
    with connection() as database:
        for statement in statements:
            database.execute(statement)
        database.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        database.execute("PRAGMA optimize")
