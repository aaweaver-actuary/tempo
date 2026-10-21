"""Locked repertoire-note persistence; position/comment decisions stay with the caller."""
import json


def repertoire_for_update(database, repertoire_id: str):
    return database.execute(
        "SELECT 1 FROM repertoires WHERE id=? FOR UPDATE", (repertoire_id,),
    ).fetchone()


def delete_position_annotation(database, repertoire_id: str, fen_key: str) -> None:
    database.execute(
        "DELETE FROM position_annotations WHERE repertoire_id=? AND fen_key=?",
        (repertoire_id, fen_key),
    )


def save_position_annotation(database, repertoire_id: str, fen_key: str, comment: str,
                             arrows: list[dict], squares: list[dict], updated_at: str) -> None:
    database.execute(
        """INSERT INTO position_annotations(
                   repertoire_id,fen_key,comment,arrows_json,squares_json,updated_at
               ) VALUES(?,?,?,?,?,?) ON CONFLICT(repertoire_id,fen_key) DO UPDATE SET
               comment=excluded.comment,arrows_json=excluded.arrows_json,
               squares_json=excluded.squares_json,updated_at=excluded.updated_at""",
        (repertoire_id, fen_key, comment, json.dumps(arrows),
         json.dumps(squares), updated_at),
    )
