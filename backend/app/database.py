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
            new_cards_per_day INTEGER NOT NULL DEFAULT 10,
            lichess_username TEXT NOT NULL DEFAULT '',
            chesscom_username TEXT NOT NULL DEFAULT ''
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
            ,fsrs_card_json TEXT
            ,first_correct_at TEXT
            ,reinforcement_pending INTEGER NOT NULL DEFAULT 0
            ,stability REAL NOT NULL DEFAULT 0
            ,guided_review INTEGER NOT NULL DEFAULT 0
            ,maximum_interval INTEGER NOT NULL DEFAULT 365
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
            ,internal_rating TEXT NOT NULL DEFAULT 'again'
            ,guided INTEGER NOT NULL DEFAULT 0
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
        """
        CREATE TABLE IF NOT EXISTS daily_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_date TEXT NOT NULL,
            card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            cycle INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            attempt_state TEXT NOT NULL DEFAULT 'clean',
            UNIQUE(queue_date, card_id, cycle)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_daily_queue_order ON daily_queue(queue_date, status, position)",
        """
        CREATE TABLE IF NOT EXISTS repertoire_lines (
            id TEXT PRIMARY KEY,
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            trained_color TEXT NOT NULL,
            start_fen TEXT NOT NULL,
            moves_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS game_accounts (
            provider TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            last_synced_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS imported_games (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            username TEXT NOT NULL,
            played_at TEXT NOT NULL,
            speed TEXT NOT NULL,
            rated INTEGER NOT NULL,
            color TEXT NOT NULL,
            result TEXT NOT NULL,
            start_fen TEXT NOT NULL,
            moves_json TEXT NOT NULL,
            game_url TEXT,
            opening_name TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_imported_games_account_date ON imported_games(provider, username, played_at)",
        """
        CREATE TABLE IF NOT EXISTS repertoire_comparisons (
            game_id TEXT PRIMARY KEY REFERENCES imported_games(id) ON DELETE CASCADE,
            repertoire_id TEXT,
            classification TEXT NOT NULL,
            divergence_ply INTEGER,
            divergence_fen TEXT,
            expected_json TEXT NOT NULL DEFAULT '[]',
            actual_uci TEXT,
            updated_at TEXT NOT NULL
        )
        """,
    ]
    with connection() as database:
        for statement in statements:
            database.execute(statement)
        database.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        # Existing local databases are migrated in place; user review history is never rebuilt.
        columns = {
            "settings": {"lichess_username": "TEXT NOT NULL DEFAULT ''", "chesscom_username": "TEXT NOT NULL DEFAULT ''"},
            "cards": {"fsrs_card_json": "TEXT", "first_correct_at": "TEXT", "reinforcement_pending": "INTEGER NOT NULL DEFAULT 0", "stability": "REAL NOT NULL DEFAULT 0", "guided_review": "INTEGER NOT NULL DEFAULT 0", "maximum_interval": "INTEGER NOT NULL DEFAULT 365"},
            "reviews": {"internal_rating": "TEXT NOT NULL DEFAULT 'again'", "guided": "INTEGER NOT NULL DEFAULT 0"},
        }
        for table, additions in columns.items():
            existing = {row[1] for row in database.execute(f"PRAGMA table_info({table})")}
            for name, definition in additions.items():
                if name not in existing:
                    database.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        database.execute("PRAGMA optimize")
