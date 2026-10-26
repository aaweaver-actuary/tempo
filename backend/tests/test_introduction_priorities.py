"""Saved score admission remains independent of the retired producer."""

import json
from datetime import date, datetime, timedelta, timezone

from app.main import seed_queue
from tempo_legacy_database import connections as database

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _seed_repertoire(db, lines: list[list[str]], cards: list[list[str]]) -> None:
    db.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Rep','rep.pgn','2026-09-20T00:00:00+00:00')"
    )
    for index, moves in enumerate(lines):
        db.execute(
            """INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
               VALUES(?,?,?,'white',?,?,?)""",
            (f"line-{index}", "rep", f"line-{index}", START, json.dumps(moves), "2026-09-20T00:00:00+00:00"),
        )
    for index, moves in enumerate(cards):
        db.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date)
               VALUES(?,?,'prefix',?,?,?)""",
            (f"card-{index}", "rep", START, json.dumps(moves), "2026-09-20"),
        )
        db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',?)", (f"card-{index}",))
    db.execute(
        "INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at) VALUES('rep','clean','2026-09-20T00:00:00+00:00')"
    )


def test_next_day_admits_from_last_published_scores_without_delaying_due_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    opening_lines = [
        ["e2e4", "e7e5", "g1f3"],
        ["d2d4", "d7d5", "c1f4"],
        ["c2c4", "e7e5", "b1c3"],
    ]
    with database.connection() as db:
        _seed_repertoire(db, opening_lines, opening_lines)
        db.execute("UPDATE settings SET new_cards_per_day=1 WHERE id=1")
        from app.services.canonical_scope_freshness import game_scope_generation
        current_evidence = json.dumps({"game_scope_generation": game_scope_generation(db)})
        for generation, scores in (
            (1, [0.9, 0.8, 0.1]),
            (2, [0.1, 0.4, 0.95]),
        ):
            for card_index, score in enumerate(scores):
                db.execute(
                    """INSERT INTO repertoire_card_priority_generations(
                           repertoire_id,generation,card_id,scoring_version,priority_score,updated_at,evidence_json
                       ) VALUES('rep',?,?,1,?,?,?)""",
                    (generation, f"card-{card_index}", score, now, current_evidence),
                )
        db.execute(
            "INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) VALUES('rep',1,?)",
            (now,),
        )
        seed_queue(db, today)
        today_cards = [row["card_id"] for row in db.execute(
            "SELECT card_id FROM daily_queue WHERE queue_date=?", (today,)
        )]
        assert today_cards == ["card-0"]
        db.execute(
            "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('card-0','good',?,0,1)",
            (now,),
        )
        db.execute(
            "UPDATE cards SET state='learning',due_date=? WHERE id='card-0'",
            (tomorrow,),
        )
        db.execute(
            "UPDATE repertoire_priority_publications SET generation=2,updated_at=? WHERE repertoire_id='rep'",
            (now,),
        )
        db.execute(
            """INSERT INTO repertoire_priority_jobs(
                   repertoire_id,generation,status,attempts,next_attempt_at,updated_at
               ) VALUES('rep',3,'queued',0,?,?)""",
            (now, now),
        )
        seed_queue(db, tomorrow)
        tomorrow_cards = [row["card_id"] for row in db.execute(
            "SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued'",
            (tomorrow,),
        )]
        seed_queue(db, tomorrow)
        repeat_cards = [row["card_id"] for row in db.execute(
            "SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued'",
            (tomorrow,),
        )]
    assert set(tomorrow_cards) == {"card-0", "card-2"}
    assert repeat_cards == tomorrow_cards
