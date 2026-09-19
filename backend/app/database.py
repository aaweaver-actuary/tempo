import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


DB_PATH = Path(os.getenv("TEMPO_DB_PATH", str(Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local/share")) / "tempo" / "tempo.db")))


@contextmanager
def connection(*, background: bool = False) -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DB_PATH)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    database.execute(f"PRAGMA busy_timeout = {250 if background else 5000}")
    try:
        yield database
        database.commit()
    finally:
        database.close()


def initialize() -> None:
    if DB_PATH.exists():
        with connection() as existing_database:
            tables = {row[0] for row in existing_database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "tactic_pack_activation" not in tables:
                backup_path = DB_PATH.with_name(DB_PATH.name + ".before-tactics-v1.bak")
                if not backup_path.exists():
                    with sqlite3.connect(backup_path) as backup_database:
                        existing_database.backup(backup_database)
    statements = [
        "CREATE TABLE IF NOT EXISTS tactic_pack_activation(pack_id TEXT PRIMARY KEY,active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)))",
        "CREATE TABLE IF NOT EXISTS tactic_introductions(puzzle_id TEXT PRIMARY KEY,pack_id TEXT NOT NULL,introduction_date TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS tactic_rotation(id INTEGER PRIMARY KEY CHECK(id=1),last_pack_id TEXT NOT NULL DEFAULT '')",
        "INSERT OR IGNORE INTO tactic_rotation(id) VALUES(1)",
        "CREATE TABLE IF NOT EXISTS tactic_discovery_attempts (id TEXT PRIMARY KEY, deck_id TEXT NOT NULL, puzzle_id TEXT NOT NULL, clean INTEGER NOT NULL, result_json TEXT NOT NULL)",
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
        CREATE TABLE IF NOT EXISTS repertoire_cards (
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            PRIMARY KEY(repertoire_id, card_id)
        )
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
        """
        CREATE TABLE IF NOT EXISTS daily_queue_days (
            queue_date TEXT PRIMARY KEY,
            seed INTEGER NOT NULL,
            membership_hash TEXT NOT NULL,
            generated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_daily_queue_order ON daily_queue(queue_date, status, position)",
        """
        CREATE TABLE IF NOT EXISTS position_annotations (
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            fen_key TEXT NOT NULL,
            comment TEXT NOT NULL DEFAULT '',
            arrows_json TEXT NOT NULL DEFAULT '[]',
            squares_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(repertoire_id, fen_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_position_annotations_fen ON position_annotations(fen_key)",
        """
        CREATE TABLE IF NOT EXISTS teaching_states (
            card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            ply INTEGER NOT NULL,
            taught_at TEXT NOT NULL,
            PRIMARY KEY(card_id, revision, ply)
        )
        """,
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
            ,analysis_state TEXT NOT NULL DEFAULT 'pending'
            ,analysis_version INTEGER NOT NULL DEFAULT 0
            ,major_mistake_ply INTEGER
            ,missed_punishment_ply INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS card_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            start_fen TEXT NOT NULL,
            moves_json TEXT NOT NULL,
            history_mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(card_id, revision)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tactic_progress (
            puzzle_id TEXT PRIMARY KEY,
            deck_id TEXT NOT NULL,
            card_id TEXT,
            clean_pass_at TEXT,
            admitted_at TEXT,
            admission_mode TEXT NOT NULL DEFAULT 'light'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tactic_progress_deck_clean ON tactic_progress(deck_id, clean_pass_at)",
        """
        CREATE TABLE IF NOT EXISTS endgame_templates (
            id TEXT PRIMARY KEY,
            card_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            white_material TEXT NOT NULL,
            black_material TEXT NOT NULL,
            trained_color TEXT NOT NULL,
            goal_mix TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS endgame_attempts (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL REFERENCES endgame_templates(id) ON DELETE CASCADE,
            start_fen TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            user_moves INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tablebase_cache (
            fen_key TEXT PRIMARY KEY,
            response_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS game_sync_state (
            provider TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'idle',
            cursor TEXT,
            last_started_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            retry_after TEXT
            ,last_result_json TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS game_sync_jobs (
            id TEXT PRIMARY KEY,
            request_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','running','paused','retrying','complete','failed')),
            result_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_game_sync_jobs_status ON game_sync_jobs(status,created_at)",
        """
        CREATE TABLE IF NOT EXISTS game_derivation_jobs (
            game_id TEXT PRIMARY KEY REFERENCES imported_games(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','running','complete','failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            derivation_version INTEGER NOT NULL DEFAULT 1,
            next_attempt_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_game_derivation_jobs_status ON game_derivation_jobs(status,updated_at)",
        """
        CREATE TABLE IF NOT EXISTS game_position_occurrences (
            game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
            ply INTEGER NOT NULL,
            fen_key TEXT NOT NULL,
            move_uci TEXT,
            PRIMARY KEY(game_id,ply)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_game_positions_fen ON game_position_occurrences(fen_key,game_id)",
        """
        CREATE TABLE IF NOT EXISTS gameplay_card_priorities (
            card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
            source_game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
            finding_id TEXT NOT NULL,
            priority_date TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_gameplay_priorities_date ON gameplay_card_priorities(priority_date,card_id)",
        """
        CREATE TABLE IF NOT EXISTS game_move_analysis (
            game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
            ply INTEGER NOT NULL,
            eval_before_cp INTEGER,
            eval_after_cp INTEGER,
            loss_cp INTEGER,
            label TEXT,
            depth INTEGER NOT NULL,
            PRIMARY KEY(game_id, ply)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_imported_games_account_date ON imported_games(provider, username, played_at)",
        """
        CREATE TABLE IF NOT EXISTS game_analysis_jobs (
            game_id TEXT PRIMARY KEY REFERENCES imported_games(id) ON DELETE CASCADE,
            analysis_version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','leased','complete','failed')),
            lease_id TEXT,
            lease_expires_at TEXT,
            idempotency_key TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_game_analysis_jobs_status ON game_analysis_jobs(status, updated_at)",
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
        """
        CREATE TABLE IF NOT EXISTS game_repertoire_matches (
            game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            is_primary INTEGER NOT NULL DEFAULT 0,
            classification TEXT NOT NULL,
            matched_player_decisions INTEGER NOT NULL DEFAULT 0,
            repertoire_opportunities INTEGER NOT NULL DEFAULT 0,
            deepest_covered_ply INTEGER NOT NULL DEFAULT 0,
            first_player_deviation_ply INTEGER,
            first_player_deviation_fen TEXT,
            first_player_deviation_expected_json TEXT NOT NULL DEFAULT '[]',
            first_player_deviation_actual_uci TEXT,
            deviation_card_id TEXT,
            first_opponent_gap_ply INTEGER,
            out_of_book_ply INTEGER,
            timeline_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(game_id,repertoire_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_game_repertoire_matches_primary ON game_repertoire_matches(game_id,is_primary)",
        """
        CREATE TABLE IF NOT EXISTS game_findings (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
            analysis_version INTEGER NOT NULL,
            ply INTEGER NOT NULL,
            kind TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            repertoire_id TEXT,
            card_id TEXT,
            motif TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','ignored','excluded')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_game_findings_status ON game_findings(status,kind,updated_at)",
        """
        CREATE TABLE IF NOT EXISTS game_insight_recommendations (
            motif TEXT PRIMARY KEY,
            miss_count INTEGER NOT NULL,
            total_loss_cp INTEGER NOT NULL,
            supporting_games_json TEXT NOT NULL,
            recommended_pack_id TEXT,
            updated_at TEXT NOT NULL
        )
        """,
    ]
    with connection() as database:
        database.execute("PRAGMA journal_mode = WAL")
        database.execute("PRAGMA synchronous = NORMAL")
        database.execute("BEGIN IMMEDIATE")
        for statement in statements:
            database.execute(statement)
        database.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        # Existing local databases are migrated in place; user review history is never rebuilt.
        columns = {
            "daily_queue": {"review_result_json": "TEXT", "attempt_failed": "INTEGER NOT NULL DEFAULT 0", "card_bucket": "TEXT", "admission_kind": "TEXT", "gameplay_priority_reason": "TEXT"},
            "game_sync_state": {"username": "TEXT NOT NULL DEFAULT ''", "last_result_json": "TEXT"},
            "settings": {"tactics_new_per_day": "INTEGER NOT NULL DEFAULT 5","lichess_username": "TEXT NOT NULL DEFAULT ''", "chesscom_username": "TEXT NOT NULL DEFAULT ''", "auto_sync_minutes": "INTEGER NOT NULL DEFAULT 3", "engine_line_window_cp": "INTEGER NOT NULL DEFAULT 30", "major_mistake_cp": "INTEGER NOT NULL DEFAULT 100", "light_first_interval_days": "INTEGER NOT NULL DEFAULT 7", "draw_hold_user_moves": "INTEGER NOT NULL DEFAULT 20"},
            "cards": {"fsrs_card_json": "TEXT", "first_correct_at": "TEXT", "reinforcement_pending": "INTEGER NOT NULL DEFAULT 0", "stability": "REAL NOT NULL DEFAULT 0", "guided_review": "INTEGER NOT NULL DEFAULT 0", "maximum_interval": "INTEGER NOT NULL DEFAULT 365", "content_type": "TEXT NOT NULL DEFAULT 'opening'", "scheduling_mode": "TEXT NOT NULL DEFAULT 'normal'", "hard_correct_streak": "INTEGER NOT NULL DEFAULT 0", "recent_attempts_json": "TEXT NOT NULL DEFAULT '[]'", "archived": "INTEGER NOT NULL DEFAULT 0", "superseded_by": "TEXT", "source_ref": "TEXT", "source_fen": "TEXT", "revision": "INTEGER NOT NULL DEFAULT 1", "introduced_at": "TEXT", "trained_color": "TEXT"},
            "reviews": {"internal_rating": "TEXT NOT NULL DEFAULT 'again'", "guided": "INTEGER NOT NULL DEFAULT 0", "source_kind": "TEXT NOT NULL DEFAULT 'study'", "source_ref": "TEXT"},
            "imported_games": {"analysis_state": "TEXT NOT NULL DEFAULT 'pending'", "analysis_version": "INTEGER NOT NULL DEFAULT 0", "major_mistake_ply": "INTEGER", "missed_punishment_ply": "INTEGER", "provider_game_id": "TEXT", "content_hash": "TEXT", "adaptive_excluded": "INTEGER NOT NULL DEFAULT 0"},
            "game_derivation_jobs": {"derivation_version": "INTEGER NOT NULL DEFAULT 1", "next_attempt_at": "TEXT"},
            "game_move_analysis": {"best_move_uci": "TEXT", "principal_variation_json": "TEXT NOT NULL DEFAULT '[]'", "mate_before": "INTEGER", "mate_after": "INTEGER", "engine_version": "TEXT", "network_version": "TEXT"},
            "repertoires": {"is_main": "INTEGER NOT NULL DEFAULT 0"},
        }
        for table, additions in columns.items():
            existing = {row[1] for row in database.execute(f"PRAGMA table_info({table})")}
            for name, definition in additions.items():
                if name not in existing:
                    database.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        database.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imported_games_provider_game_id ON imported_games(provider, provider_game_id) WHERE provider_game_id IS NOT NULL")
        database.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_source ON reviews(source_kind,source_ref) WHERE source_ref IS NOT NULL")
        database.execute("DROP INDEX IF EXISTS idx_imported_games_content_hash")
        database.execute("CREATE INDEX idx_imported_games_content_hash ON imported_games(provider, username, content_hash) WHERE content_hash IS NOT NULL")
        now = datetime.now(timezone.utc).isoformat()
        database.execute(
            """INSERT OR IGNORE INTO game_analysis_jobs(game_id,analysis_version,status,updated_at)
               SELECT id,1,CASE WHEN analysis_state IN ('ready','complete') THEN 'complete' WHEN analysis_state='failed' THEN 'failed' ELSE 'queued' END,?
               FROM imported_games""",
            (now,),
        )
        database.execute("""INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id)
                            SELECT repertoire_id,id FROM cards WHERE content_type='opening'""")
        database.execute(
            """INSERT OR IGNORE INTO game_derivation_jobs(game_id,status,updated_at)
               SELECT id,'queued',? FROM imported_games g
               WHERE NOT EXISTS(SELECT 1 FROM game_position_occurrences p WHERE p.game_id=g.id)""",
            (now,),
        )
        database.execute("PRAGMA optimize")
