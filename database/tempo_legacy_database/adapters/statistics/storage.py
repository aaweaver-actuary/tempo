"""Retained game-feature projections on borrowed connections."""
from tempo_legacy_database.adapters.games.derivation.features import GAME_FEATURE_UPSERT_SQL, write_feature_values


def game_row(database, game_id: str):
    return database.execute("SELECT * FROM imported_games WHERE id=?", (game_id,)).fetchone()


def analysis_rows(database, game_id: str):
    return database.execute(
            "SELECT * FROM game_move_analysis WHERE game_id=? ORDER BY ply", (game_id,)
        ).fetchall()


def game_event_rows(database, game_id: str):
    return database.execute(
            "SELECT * FROM gameplay_events WHERE game_id=? AND classifier_version=1",
            (game_id,),
        ).fetchall()


def primary_match(database, game_id: str):
    return database.execute(
            "SELECT * FROM current_game_repertoire_matches game_repertoire_matches WHERE game_id=? AND is_primary=1",
            (game_id,),
        ).fetchone()


def configured_timezone(database):
    return database.execute("SELECT timezone FROM settings WHERE id=1").fetchone()[0]
