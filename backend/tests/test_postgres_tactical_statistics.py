"""Regression for bounded PostgreSQL tactical insight aggregation."""

from contextlib import contextmanager

from app.services import tactical_opportunities


def test_postgres_tactical_summary_keeps_pin_breakdown_without_loading_all_motifs(monkeypatch):
    statements = []

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Database:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))
            if "GROUP BY ROLLUP" in statement:
                return Cursor([
                    {"motif": None, "opportunities": 2, "exploited": 1, "missed": 1,
                     "missed_cost": 180, "supporting_games": 2,
                     "mean_confidence": .8, "minimum_confidence": .7},
                    {"motif": "pin", "opportunities": 1, "exploited": 0, "missed": 1,
                     "missed_cost": 180, "supporting_games": 1,
                     "mean_confidence": .9, "minimum_confidence": .9},
                    {"motif": "fork", "opportunities": 1, "exploited": 1, "missed": 0,
                     "missed_cost": None, "supporting_games": 1,
                     "mean_confidence": .7, "minimum_confidence": .7},
                ])
            return Cursor([{"game_id": "game-one", "outcome": "missed", "confidence": .9,
                            "evaluation_loss_cp": 180,
                            "evidence_json": '{"motif_evidence":[{"motif":"pin",'
                            '"existed_before":false,"concrete_outcome":{"pin_type":"absolute"}}]}'}])

    @contextmanager
    def database_connection():
        yield Database()

    monkeypatch.setattr(tactical_opportunities, "connection", database_connection)
    body = tactical_opportunities._postgres_tactical_statistics({"provider": "lichess"})
    assert body["overall"]["conversion_rate"] == 50.0
    assert body["overall"]["average_missed_centipawn_cost"] == 180.0
    assert [item["motif"] for item in body["motifs"]] == ["fork", "pin"]
    assert body["motifs"][1]["pin_breakdown"]["absolute"]["opportunities"] == 1
    assert body["motifs"][1]["pin_breakdown"]["created"]["missed"] == 1
    assert len(statements) == 2
    assert all(parameters == ["lichess"] for _, parameters in statements)
