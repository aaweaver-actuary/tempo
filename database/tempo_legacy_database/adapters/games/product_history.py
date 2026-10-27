"""Bounded existing-library read projection and explicit run identity lock."""
import json

from app.games.feedback.history import RetainedHistoryGame, RetainedHistoryPage


class PostgresRetainedHistory:
    def __init__(self, connections):
        self.connections = connections

    def page(self, selection):
        aliases = ["chess.com", "chesscom"] if selection.provider == "chesscom" else ["lichess"]
        with self.connections() as database:
            execute = getattr(database, "execute_native", None) or database.execute
            rows = execute("""SELECT id,adaptive_excluded,moves_json FROM imported_games
                WHERE provider=ANY(%s) AND lower(username)=lower(%s)
                  AND played_at::timestamptz>=%s::timestamptz
                  AND played_at::timestamptz<%s::timestamptz
                  AND speed=ANY(%s) AND (%s::int IS NULL OR rated=%s::int)
                  AND (%s::text IS NULL OR id>%s::text)
                ORDER BY id LIMIT %s""", (aliases, selection.account, selection.window.start_at,
                selection.window.end_at, list(selection.speeds),
                None if selection.rated is None else int(selection.rated),
                None if selection.rated is None else int(selection.rated), selection.after_game_id,
                selection.after_game_id, selection.maximum_games + 1)).fetchall()
        games = []
        for row in rows[:selection.maximum_games]:
            positions, reason = None, None
            try:
                if len(row[2].encode()) > 65536:
                    raise ValueError("retained_history_row_exceeds_bound")
                moves = json.loads(row[2])
                if not isinstance(moves, list) or not 1 <= len(moves) <= 1000:
                    raise ValueError("retained_history_move_bound_invalid")
                positions = len(moves)
            except (ValueError, TypeError, AttributeError):
                reason = "retained_history_moves_unavailable"
            games.append(RetainedHistoryGame(row[0], bool(row[1]), positions, reason))
        return RetainedHistoryPage(tuple(games), len(rows) > selection.maximum_games)


def lock_history_run(database, workspace_id, run_id):
    """Borrow the caller's transaction; no task or Study state is written here."""
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("tempo:retained-game-history:" + workspace_id + ":" + run_id,))


def assert_history_selection(database, selection, game_ids, *, fence=False):
    """Recheck factual account/window/filter scope; never infer it from a DTO."""
    identifiers = tuple(sorted(set(game_ids)))
    if len(identifiers) > selection.maximum_games:
        raise ValueError("retained_history_game_bound_invalid")
    if not identifiers:
        return
    aliases = ["chess.com", "chesscom"] if selection.provider == "chesscom" else ["lichess"]
    execute = getattr(database, "execute_native", None) or database.execute
    rows = execute("""SELECT id FROM imported_games
        WHERE id=ANY(%s) AND provider=ANY(%s) AND lower(username)=lower(%s)
          AND played_at::timestamptz>=%s::timestamptz
          AND played_at::timestamptz<%s::timestamptz
          AND speed=ANY(%s) AND (%s::int IS NULL OR rated=%s::int)
          AND adaptive_excluded=0 AND (%s::text IS NULL OR id>%s::text)
        ORDER BY id""" + (" FOR SHARE" if fence else ""),
        (list(identifiers), aliases, selection.account, selection.window.start_at,
         selection.window.end_at, list(selection.speeds),
         None if selection.rated is None else int(selection.rated),
         None if selection.rated is None else int(selection.rated), selection.after_game_id, selection.after_game_id)).fetchall()
    if len(rows) != len(identifiers) or {row[0] for row in rows} != set(identifiers):
        raise ValueError("retained_history_source_outside_selection")
