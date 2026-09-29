import json

from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_comparison_cards_returns_active_routes_and_all_repertoire_links(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            for repertoire_id in ("london", "transposed"):
                db.execute(
                    "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
                    (repertoire_id, repertoire_id.title(), "fixture.pgn", "2026-09-28"),
                )
            for identifier, archived, pending, state in (
                ("saved", 0, 0, "locked"),
                ("archived", 1, 0, "learning"),
                ("unvalidated", 0, 1, "new"),
            ):
                db.execute(
                    """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,
                                         due_date,content_type,archived,pending_validation)
                       VALUES(?, 'london', 'prefix', ?, ?, ?, '2026-09-28',
                              'opening', ?, ?)""",
                    (identifier, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                     json.dumps(["d2d4", "d7d5"]), state, archived, pending),
                )
                db.execute(
                    "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('london',?)",
                    (identifier,),
                )
            db.execute(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('transposed','saved')"
            )
        response = client.get("/api/compare/cards")
        assert response.status_code == 200
        assert response.json() == {"cards": [{
            "id": "saved", "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "moves": ["d2d4", "d7d5"], "state": "locked", "kind": "prefix",
            "trained_color": None,
            "repertoires": [{"id": "london", "name": "London"},
                            {"id": "transposed", "name": "Transposed"}],
        }]}
