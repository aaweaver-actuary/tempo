"""Comparison source projections and publication operations on the caller transaction."""
import json


_REPERTOIRE_LINE_SIGNATURE_SQL = (
    "SELECT md5(COALESCE(string_agg(md5(row_to_json(item)::text),'' "
    "ORDER BY item.id),'')) FROM ("
    "SELECT id,repertoire_id,trained_color,start_fen,moves_json "
    "FROM repertoire_lines) item"
)
_CARD_SIGNATURE_SQL = (
    "SELECT md5(COALESCE(string_agg(md5(row_to_json(item)::text),'' "
    "ORDER BY item.repertoire_id,item.id),'')) FROM ("
    "SELECT rc.repertoire_id,c.id,c.start_fen,c.moves_json FROM cards c "
    "JOIN repertoire_cards rc ON rc.card_id=c.id "
    "WHERE c.archived=0 AND c.content_type='opening') item"
)


def repertoire_line_digest(database):
    return database.execute_native(_REPERTOIRE_LINE_SIGNATURE_SQL).fetchone()[0]


def opening_card_digest(database):
    return database.execute_native(_CARD_SIGNATURE_SQL).fetchone()[0]


def repertoire_rows(database):
    return [dict(row) for row in database.execute(
            "SELECT id,is_main,canonical_prefix_moves_json,canonical_prefix_revision FROM repertoires "
            "WHERE id NOT IN ('__tactics__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__') ORDER BY id"
        )]


def line_page(database, line_cursor: str):
    return [dict(row) for row in database.execute(
                "SELECT id,repertoire_id,trained_color,start_fen,moves_json "
                "FROM repertoire_lines WHERE id>? ORDER BY id LIMIT 64",
                (line_cursor,),
            )]


def card_page(database, card_cursor: tuple[str, str]):
    return [dict(row) for row in database.execute_native(
                "SELECT rc.repertoire_id,c.id,c.start_fen,c.moves_json FROM cards c "
                "JOIN repertoire_cards rc ON rc.card_id=c.id "
                "WHERE c.archived=0 AND c.content_type='opening' "
                "AND (rc.repertoire_id,c.id)>(%s,%s) "
                "ORDER BY rc.repertoire_id,c.id LIMIT 64",
                card_cursor,
            )]


def all_line_rows(database):
    return [dict(row) for row in database.execute(
                "SELECT id,repertoire_id,trained_color,start_fen,moves_json FROM repertoire_lines ORDER BY id"
            )]


def all_card_rows(database):
    return [dict(row) for row in database.execute(
                """SELECT rc.repertoire_id,c.id,c.start_fen,c.moves_json FROM cards c
                   JOIN repertoire_cards rc ON rc.card_id=c.id
                   WHERE c.archived=0 AND c.content_type='opening'
                   ORDER BY rc.repertoire_id,c.id"""
            )]


def comparison_game_rows(database, game_ids: list[str] | None):
    where = "" if game_ids is None else f" WHERE id IN ({','.join('?' for _ in game_ids)})"
    return [
            {**dict(row), "moves": json.loads(row["moves_json"])}
            for row in database.execute(f"SELECT * FROM imported_games{where}", game_ids or [])
        ]


def begin_comparison_write(database) -> None:
    database.execute("BEGIN IMMEDIATE")


def clear_game_comparison(database, game: dict, scope_generation: int) -> None:
    database.execute("UPDATE imported_games SET repertoire_scope_generation=? WHERE id=?", (scope_generation, game["id"]))
    database.execute("DELETE FROM game_repertoire_matches WHERE game_id=?", (game["id"],))
    database.execute("DELETE FROM repertoire_decision_events WHERE game_id=?", (game["id"],))


def insert_repertoire_match(database, match: dict, is_primary: int, deviation: dict | None, now: str) -> None:
    database.execute(
                    """INSERT INTO game_repertoire_matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        match["game_id"], match["repertoire_id"], is_primary, match["classification"],
                        match["matched"], match["opportunities"], match["deepest"],
                        deviation["ply"] if deviation else None, deviation["fen"] if deviation else None,
                        json.dumps(deviation["expected"] if deviation else []), deviation["actual"] if deviation else None,
                        match["deviation_card_id"], match["opponent_gap"], match["out_of_book"],
                        json.dumps(match["timeline"]), now,
                    ),
                )


def insert_decision_event(database, event_id: str, game: dict, evidence_owner: dict, event: dict, now: str) -> None:
    database.execute(
                        """INSERT INTO repertoire_decision_events(
                               id,game_id,repertoire_id,card_id,ply,fen_key,
                               expected_uci,actual_uci,outcome,played_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event_id, game["id"], evidence_owner["repertoire_id"],
                            event["card_id"], event["ply"], event["fen_key"],
                            event["expected_uci"], event["actual_uci"],
                            event["outcome"], game["played_at"], now,
                        ),
                    )


def save_primary_comparison(database, game_id: str, repertoire_id: str | None,
                            classification: str, divergence_ply: int | None,
                            divergence_fen: str | None, expected_moves: list[str],
                            actual_uci: str | None, now: str) -> None:
    database.execute(
        """INSERT INTO repertoire_comparisons(game_id,repertoire_id,classification,divergence_ply,divergence_fen,expected_json,actual_uci,updated_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET repertoire_id=excluded.repertoire_id,
                   classification=excluded.classification,divergence_ply=excluded.divergence_ply,divergence_fen=excluded.divergence_fen,
                   expected_json=excluded.expected_json,actual_uci=excluded.actual_uci,updated_at=excluded.updated_at""",
        (game_id, repertoire_id, classification, divergence_ply, divergence_fen,
         json.dumps(expected_moves), actual_uci, now),
    )
