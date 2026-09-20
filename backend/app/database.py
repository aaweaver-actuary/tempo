import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


DB_PATH = Path(
    os.getenv(
        "TEMPO_DB_PATH",
        str(
            Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local/share"))
            / "tempo"
            / "tempo.db"
        ),
    )
)


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
            tables = {
                row[0]
                for row in existing_database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
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
            created_at TEXT NOT NULL,
            integrity_status TEXT NOT NULL DEFAULT 'unchecked' CHECK(integrity_status IN ('unchecked','clean','needs_repair')),
            integrity_checked_at TEXT
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
        CREATE TABLE IF NOT EXISTS repertoire_integrity_issues (
            id TEXT PRIMARY KEY,
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('missing_response','multiple_responses','invalid_source')),
            fen_key TEXT,
            fen TEXT,
            trained_color TEXT,
            signature TEXT NOT NULL,
            moves_json TEXT NOT NULL DEFAULT '[]',
            sources_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_repertoire_integrity_repertoire ON repertoire_integrity_issues(repertoire_id,updated_at,id)",
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
            ,analysis_evidence_version INTEGER NOT NULL DEFAULT 1
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
        CREATE TABLE IF NOT EXISTS prefix_splits (
            source_card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
            source_revision INTEGER NOT NULL,
            shortened_card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            continuation_card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL
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
            position_fen TEXT,
            PRIMARY KEY(game_id, ply)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS game_move_analysis_candidates (
            game_id TEXT NOT NULL,
            ply INTEGER NOT NULL,
            rank INTEGER NOT NULL CHECK(rank >= 1),
            candidate_uci TEXT NOT NULL,
            score_cp INTEGER,
            mate INTEGER,
            score_text TEXT,
            principal_variation_json TEXT NOT NULL DEFAULT '[]',
            depth INTEGER NOT NULL,
            position_fen TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            network_version TEXT NOT NULL,
            PRIMARY KEY(game_id, ply, rank),
            FOREIGN KEY(game_id, ply) REFERENCES game_move_analysis(game_id, ply) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_game_analysis_candidates_move ON game_move_analysis_candidates(game_id, ply, candidate_uci)",
        "CREATE INDEX IF NOT EXISTS idx_imported_games_account_date ON imported_games(provider, username, played_at)",
        """
        CREATE TABLE IF NOT EXISTS game_analysis_jobs (
            game_id TEXT PRIMARY KEY REFERENCES imported_games(id) ON DELETE CASCADE,
            analysis_version INTEGER NOT NULL DEFAULT 1,
            analysis_evidence_version INTEGER NOT NULL DEFAULT 1,
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
        CREATE TABLE IF NOT EXISTS tactical_opportunities (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
            analysis_version INTEGER NOT NULL,
            ply INTEGER NOT NULL,
            motif TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('exploited','missed')),
            confidence REAL NOT NULL,
            opportunity_value_cp INTEGER NOT NULL,
            evaluation_loss_cp INTEGER NOT NULL,
            played_move_uci TEXT NOT NULL,
            accepted_moves_json TEXT NOT NULL DEFAULT '[]',
            evidence_json TEXT NOT NULL,
            engine_version TEXT,
            network_version TEXT,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_at TEXT,
            UNIQUE(game_id,analysis_version,ply,motif)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tactical_opportunities_motif_outcome ON tactical_opportunities(motif,outcome,active)",
        "CREATE INDEX IF NOT EXISTS idx_tactical_opportunities_game_version ON tactical_opportunities(game_id,analysis_version,ply,active)",
        "CREATE INDEX IF NOT EXISTS idx_tactical_opportunities_updated ON tactical_opportunities(updated_at,outcome)",
        """
        CREATE TABLE IF NOT EXISTS guided_review_sessions (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
            analysis_version INTEGER NOT NULL,
            finding_ids_json TEXT NOT NULL,
            current_index INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','complete')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_guided_review_game ON guided_review_sessions(game_id,status,updated_at)",
        """
        CREATE TABLE IF NOT EXISTS guided_review_attempts (
            session_id TEXT NOT NULL REFERENCES guided_review_sessions(id) ON DELETE CASCADE,
            finding_id TEXT NOT NULL REFERENCES game_findings(id) ON DELETE CASCADE,
            move_uci TEXT NOT NULL,
            correct INTEGER NOT NULL,
            attempted_at TEXT NOT NULL,
            PRIMARY KEY(session_id,finding_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS gameplay_events (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
            analysis_version INTEGER NOT NULL,
            classifier_version INTEGER NOT NULL,
            ply INTEGER NOT NULL,
            kind TEXT NOT NULL,
            motif TEXT NOT NULL,
            beneficiary_color TEXT NOT NULL CHECK(beneficiary_color IN ('white','black')),
            created_by_color TEXT NOT NULL CHECK(created_by_color IN ('white','black')),
            outcome TEXT NOT NULL,
            confidence REAL NOT NULL,
            loss_cp INTEGER NOT NULL,
            best_move_uci TEXT,
            actual_move_uci TEXT,
            principal_variation_json TEXT NOT NULL DEFAULT '[]',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_gameplay_events_game_ply ON gameplay_events(game_id,ply,kind)",
        "CREATE INDEX IF NOT EXISTS idx_gameplay_events_motif ON gameplay_events(motif,outcome,confidence)",
        """
        CREATE TABLE IF NOT EXISTS game_feature_rows (
            game_id TEXT PRIMARY KEY REFERENCES imported_games(id) ON DELETE CASCADE,
            feature_version INTEGER NOT NULL,
            local_day TEXT NOT NULL,
            local_hour INTEGER NOT NULL,
            local_weekday INTEGER NOT NULL,
            outcome_score REAL NOT NULL,
            player_decisions INTEGER NOT NULL,
            mean_loss_cp REAL,
            major_mistakes INTEGER,
            opening_exit_ply INTEGER,
            opening_exit_eval_cp INTEGER,
            endgame_entry_ply INTEGER,
            endgame_entry_eval_cp INTEGER,
            tactical_opportunities INTEGER NOT NULL DEFAULT 0,
            tactical_found INTEGER NOT NULL DEFAULT 0,
            tactical_conceded INTEGER NOT NULL DEFAULT 0,
            primary_repertoire_id TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_game_features_day ON game_feature_rows(local_day,game_id)",
        """
        CREATE TABLE IF NOT EXISTS daily_chess_snapshots (
            local_day TEXT PRIMARY KEY,
            snapshot_version INTEGER NOT NULL,
            metrics_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS daily_chess_insights (
            id TEXT PRIMARY KEY,
            local_day TEXT NOT NULL,
            kind TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
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
        """
        CREATE TABLE IF NOT EXISTS repertoire_coverage_runs (
            id TEXT PRIMARY KEY,
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN ('queued','running','complete','failed')),
            settings_json TEXT NOT NULL,
            total_nodes INTEGER NOT NULL DEFAULT 0,
            completed_nodes INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_coverage_runs_repertoire ON repertoire_coverage_runs(repertoire_id,created_at)",
        """
        CREATE TABLE IF NOT EXISTS repertoire_coverage_nodes (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES repertoire_coverage_runs(id) ON DELETE CASCADE,
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            fen TEXT NOT NULL,
            fen_key TEXT NOT NULL,
            ply INTEGER NOT NULL,
            trained_color TEXT NOT NULL,
            routes_json TEXT NOT NULL,
            covered_replies_json TEXT NOT NULL,
            explorer_status TEXT NOT NULL DEFAULT 'queued',
            maia_status TEXT NOT NULL DEFAULT 'queued',
            explorer_games INTEGER NOT NULL DEFAULT 0,
            lease_id TEXT,
            lease_expires_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id,fen_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_coverage_nodes_work ON repertoire_coverage_nodes(explorer_status,maia_status,updated_at)",
        """
        CREATE TABLE IF NOT EXISTS repertoire_coverage_candidates (
            node_id TEXT NOT NULL REFERENCES repertoire_coverage_nodes(id) ON DELETE CASCADE,
            move_uci TEXT NOT NULL,
            explorer_probability REAL,
            maia_probability REAL,
            blended_probability REAL,
            required INTEGER NOT NULL DEFAULT 0,
            covered INTEGER NOT NULL DEFAULT 0,
            source_state TEXT NOT NULL,
            PRIMARY KEY(node_id,move_uci)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS repertoire_card_introduction_priorities (
            repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
            card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            scoring_version INTEGER NOT NULL,
            completed_line_ids_json TEXT NOT NULL DEFAULT '[]',
            completion_mass REAL NOT NULL DEFAULT 0,
            frontier_decisions_json TEXT NOT NULL DEFAULT '[]',
            frontier_reach REAL NOT NULL DEFAULT 0,
            priority_score REAL NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(repertoire_id,card_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_introduction_priorities_score ON repertoire_card_introduction_priorities(repertoire_id,priority_score DESC)",
        """
        CREATE TABLE IF NOT EXISTS explorer_position_cache (
            cache_key TEXT PRIMARY KEY,
            fen_key TEXT NOT NULL,
            speeds TEXT NOT NULL,
            ratings TEXT NOT NULL,
            response_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
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
            "daily_queue": {
                "review_result_json": "TEXT",
                "attempt_failed": "INTEGER NOT NULL DEFAULT 0",
                "card_bucket": "TEXT",
                "admission_kind": "TEXT",
                "gameplay_priority_reason": "TEXT",
            },
            "game_sync_state": {
                "username": "TEXT NOT NULL DEFAULT ''",
                "last_result_json": "TEXT",
            },
            "settings": {
                "tactics_new_per_day": "INTEGER NOT NULL DEFAULT 5",
                "lichess_username": "TEXT NOT NULL DEFAULT ''",
                "chesscom_username": "TEXT NOT NULL DEFAULT ''",
                "auto_sync_minutes": "INTEGER NOT NULL DEFAULT 3",
                "engine_line_window_cp": "INTEGER NOT NULL DEFAULT 30",
                "major_mistake_cp": "INTEGER NOT NULL DEFAULT 100",
                "light_first_interval_days": "INTEGER NOT NULL DEFAULT 7",
                "draw_hold_user_moves": "INTEGER NOT NULL DEFAULT 20",
                "coverage_reply_denominator": "INTEGER NOT NULL DEFAULT 100",
                "coverage_cumulative_target": "INTEGER NOT NULL DEFAULT 95",
                "coverage_horizon_fullmoves": "INTEGER NOT NULL DEFAULT 15",
                "coverage_path_floor": "REAL NOT NULL DEFAULT 0.0005",
                "coverage_maia_elo": "INTEGER NOT NULL DEFAULT 1500",
            },
            "repertoires": {
                "integrity_status": "TEXT NOT NULL DEFAULT 'unchecked'",
                "integrity_checked_at": "TEXT",
            },
            "cards": {
                "fsrs_card_json": "TEXT",
                "first_correct_at": "TEXT",
                "reinforcement_pending": "INTEGER NOT NULL DEFAULT 0",
                "stability": "REAL NOT NULL DEFAULT 0",
                "guided_review": "INTEGER NOT NULL DEFAULT 0",
                "maximum_interval": "INTEGER NOT NULL DEFAULT 365",
                "content_type": "TEXT NOT NULL DEFAULT 'opening'",
                "scheduling_mode": "TEXT NOT NULL DEFAULT 'normal'",
                "hard_correct_streak": "INTEGER NOT NULL DEFAULT 0",
                "recent_attempts_json": "TEXT NOT NULL DEFAULT '[]'",
                "archived": "INTEGER NOT NULL DEFAULT 0",
                "superseded_by": "TEXT",
                "source_ref": "TEXT",
                "source_fen": "TEXT",
                "revision": "INTEGER NOT NULL DEFAULT 1",
                "introduced_at": "TEXT",
                "trained_color": "TEXT",
            },
            "reviews": {
                "internal_rating": "TEXT NOT NULL DEFAULT 'again'",
                "guided": "INTEGER NOT NULL DEFAULT 0",
                "source_kind": "TEXT NOT NULL DEFAULT 'study'",
                "source_ref": "TEXT",
            },
            "imported_games": {
                "analysis_state": "TEXT NOT NULL DEFAULT 'pending'",
                "analysis_version": "INTEGER NOT NULL DEFAULT 0",
                "analysis_evidence_version": "INTEGER NOT NULL DEFAULT 1",
                "major_mistake_ply": "INTEGER",
                "missed_punishment_ply": "INTEGER",
                "provider_game_id": "TEXT",
                "content_hash": "TEXT",
                "adaptive_excluded": "INTEGER NOT NULL DEFAULT 0",
                "player_rating": "INTEGER",
                "opponent_rating": "INTEGER",
                "rating_change": "INTEGER",
                "time_control": "TEXT",
            },
            "game_derivation_jobs": {
                "derivation_version": "INTEGER NOT NULL DEFAULT 1",
                "next_attempt_at": "TEXT",
            },
            "game_move_analysis": {
                "best_move_uci": "TEXT",
                "principal_variation_json": "TEXT NOT NULL DEFAULT '[]'",
                "mate_before": "INTEGER",
                "mate_after": "INTEGER",
                "engine_version": "TEXT",
                "network_version": "TEXT",
                "mover_color": "TEXT",
                "is_player_move": "INTEGER NOT NULL DEFAULT 1",
                "actual_move_uci": "TEXT",
                "position_fen": "TEXT",
            },
            "game_move_analysis_candidates": {"score_text": "TEXT"},
            "game_analysis_jobs": {
                "analysis_evidence_version": "INTEGER NOT NULL DEFAULT 1"
            },
            "game_findings": {"source_opportunity_id": "TEXT", "review_after": "TEXT"},
            "repertoires": {"is_main": "INTEGER NOT NULL DEFAULT 0"},
        }
        for table, additions in columns.items():
            existing = {
                row[1] for row in database.execute(f"PRAGMA table_info({table})")
            }
            for name, definition in additions.items():
                if name not in existing:
                    database.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )
        database.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_findings_opportunity ON game_findings(source_opportunity_id)"
        )
        database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_imported_games_provider_game_id ON imported_games(provider, provider_game_id) WHERE provider_game_id IS NOT NULL"
        )
        database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_source ON reviews(source_kind,source_ref) WHERE source_ref IS NOT NULL"
        )
        database.execute("DROP INDEX IF EXISTS idx_imported_games_content_hash")
        database.execute(
            "CREATE INDEX idx_imported_games_content_hash ON imported_games(provider, username, content_hash) WHERE content_hash IS NOT NULL"
        )
        # `introduced_at` is a local study-day field. A prefix-split release
        # briefly wrote full timestamps, which violate the queue transport
        # contract and can make an otherwise valid card disappear in the UI.
        database.execute(
            """UPDATE cards
               SET introduced_at=substr(introduced_at,1,10)
               WHERE introduced_at IS NOT NULL
                 AND length(introduced_at) > 10
                 AND substr(introduced_at,5,1)='-'
                 AND substr(introduced_at,8,1)='-'"""
        )
        now = datetime.now(timezone.utc).isoformat()
        database.execute(
            """INSERT OR IGNORE INTO game_analysis_jobs(game_id,analysis_version,status,updated_at)
               SELECT id,1,CASE WHEN analysis_state IN ('ready','complete') THEN 'complete' WHEN analysis_state='failed' THEN 'failed' ELSE 'queued' END,?
               FROM imported_games""",
            (now,),
        )
        # Candidate lines are an additive evidence upgrade. Queue only completed
        # analyses that predate it; cards, reviews, and sync metadata remain intact.
        database.execute(
            """UPDATE game_analysis_jobs
               SET analysis_version=(
                       SELECT MAX(1,g.analysis_version + 1)
                       FROM imported_games g
                       WHERE g.id=game_analysis_jobs.game_id
                   ),
                   status='queued',lease_id=NULL,lease_expires_at=NULL,
                   analysis_evidence_version=2,updated_at=?
               WHERE status='complete' AND analysis_evidence_version < 2
                 AND EXISTS(
                     SELECT 1 FROM imported_games g
                     WHERE g.id=game_analysis_jobs.game_id
                       AND g.analysis_evidence_version < 2
                 )""",
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
