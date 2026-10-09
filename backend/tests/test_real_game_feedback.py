"""Regression coverage for real-game opening evidence reaching study."""

from datetime import date, datetime, timedelta, timezone
import json
import threading
import pytest

import chess
from fsrs import Card
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.main import queue_today, randomize_daily_queue, requeue, seed_queue
from app.services import repertoire_comparison
from app.services.game_sync_coordinator import _execute_derivation
from app.services.game_findings import refresh_game_findings
from app.services.introduction_priorities import rebuild_introduction_priorities
from app.services.real_game_feedback import apply_real_game_misses
from app.services.repertoire_comparison import compare_games


def _ordinary_queue_card(db, identifier, position, content_type="opening"):
    db.execute(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,content_type) "
        "VALUES(?,'rep','prefix',?,'[\"e2e4\"]','learning',?,?,?)",
        (identifier, START, date.today().isoformat(), date.today().isoformat(), content_type),
    )
    return db.execute(
        "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?) RETURNING id",
        (date.today().isoformat(), identifier, position),
    ).fetchone()[0]


@pytest.mark.parametrize("state,limit", [("new", 0), ("locked", 0), ("locked", 1)])
def test_real_game_miss_admits_locked_or_new_card_despite_daily_limit(tmp_path, monkeypatch, state, limit):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db)
        _ordinary_queue_card(db, "parent", 0)
        db.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES('parent',3,?,0,1,'study')", ((now - timedelta(days=1)).isoformat(),))
        db.execute("UPDATE cards SET state=?,unlock_after_card_id='parent' WHERE id='card'", (state,))
        db.execute("UPDATE settings SET new_cards_per_day=? WHERE id=1", (limit,))
        _seed_game(db, "locked-miss", ["d2d4"], now.isoformat())
        parent_before = dict(db.execute("SELECT * FROM cards WHERE id='parent'").fetchone())
    compare_games(["locked-miss"])
    apply_real_game_misses("locked-miss")
    with database.connection() as db:
        seed_queue(db, date.today().isoformat())
        randomize_daily_queue(db, date.today().isoformat())
        seed_queue(db, date.today().isoformat())
        queued = db.execute("SELECT * FROM daily_queue WHERE card_id='card' AND status='queued'").fetchall()
        assert len(queued) == 1
        assert queued[0]["gameplay_priority_reason"] == "Priority review · missed in a recent game"
        assert db.execute("SELECT card_id FROM daily_queue WHERE status='queued' ORDER BY position,id LIMIT 1").fetchone()[0] == "card"
        assert dict(db.execute("SELECT * FROM cards WHERE id='parent'").fetchone()) == parent_before
        assert db.execute("SELECT state,introduced_at FROM cards WHERE id='card'").fetchone()[0] == "learning"
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='card'").fetchone()[0] == 0


def test_real_game_miss_promotes_existing_entry_ahead_of_ordinary_work_and_replays_once(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=(now - timedelta(days=2)).isoformat())
        for index in range(6):
            _ordinary_queue_card(db, f"ordinary-{index}", index)
        entry_id = db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'card',9) RETURNING id", (date.today().isoformat(),)).fetchone()[0]
        _seed_game(db, "new-miss", ["d2d4"], (now - timedelta(days=1)).isoformat())
    for _ in range(2):
        compare_games(["new-miss"])
        apply_real_game_misses("new-miss")
    with database.connection() as db:
        ordered = db.execute("SELECT id,card_id FROM daily_queue WHERE status='queued' ORDER BY position,id").fetchall()
        assert ordered[0]["id"] == entry_id
        assert [row["card_id"] for row in ordered[1:]] == [f"ordinary-{index}" for index in range(6)]
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card'").fetchone()[0] == 1
        _ordinary_queue_card(db, "added", 20)
        randomize_daily_queue(db, date.today().isoformat())
        assert db.execute("SELECT card_id FROM daily_queue WHERE status='queued' ORDER BY position,id LIMIT 1").fetchone()[0] == "card"


def test_real_game_miss_obligation_survives_next_day_without_fsrs_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=(now - timedelta(days=2)).isoformat())
        _seed_game(db, "durable-miss", ["d2d4"], (now - timedelta(days=1)).isoformat())
        before = dict(db.execute("SELECT * FROM cards WHERE id='card'").fetchone())
    compare_games(["durable-miss"])
    apply_real_game_misses("durable-miss")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with database.connection() as db:
        seed_queue(db, tomorrow)
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE queue_date=? AND card_id='card' AND status='queued'", (tomorrow,)).fetchone()[0] == 1
        assert dict(db.execute("SELECT * FROM cards WHERE id='card'").fetchone()) == before


@pytest.mark.parametrize("study_offset,expected", [(0, False), (-1, True), (1, False)])
def test_real_game_miss_uses_exact_study_instants_including_equal_and_microsecond_times(tmp_path, monkeypatch, study_offset, expected):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    missed_at = datetime.now(timezone.utc) - timedelta(days=1)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=(missed_at + timedelta(microseconds=study_offset)).isoformat())
        _seed_game(db, "precise-miss", ["d2d4"], missed_at.isoformat())
    compare_games(["precise-miss"])
    with database.connection() as db:
        rebuild_introduction_priorities(db, "rep")
        evidence = json.loads(db.execute("SELECT evidence_json FROM repertoire_card_introduction_priorities WHERE card_id='card'").fetchone()[0])
        assert bool(evidence["real_game_repertoire_miss"]) is expected


def test_real_game_miss_transposed_prefix_decision_prioritizes_whole_card_without_split(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    intended = ["g1f3", "d7d5", "g2g3", "g8f6", "f1g2"]
    transposed = ["g2g3", "d7d5", "g1f3", "g8f6", "f1h3"]
    with database.connection() as db:
        _seed_repertoire(db)
        db.execute("UPDATE repertoire_lines SET moves_json=?", (json.dumps(intended),))
        db.execute("UPDATE cards SET moves_json=?", (json.dumps(intended),))
        db.execute("UPDATE repertoires SET canonical_prefix_moves_json=?", (json.dumps(intended[:4]),))
        _seed_game(db, "transposed-prefix", transposed, datetime.now(timezone.utc).isoformat())
    compare_games(["transposed-prefix"])
    with database.connection() as db:
        event = db.execute("SELECT * FROM repertoire_decision_events WHERE game_id='transposed-prefix' AND ply=4").fetchone()
        assert event is not None
        assert event["outcome"] == "miss" and event["card_id"] == "card"
        seed_queue(db, date.today().isoformat())
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1


def test_real_game_miss_inside_configured_prefix_is_not_assumed_away(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(db)
        db.execute("UPDATE repertoires SET canonical_prefix_moves_json=?", (json.dumps(EXPECTED),))
        _seed_game(db, "prefix-miss", ["d2d4"], datetime.now(timezone.utc).isoformat())
    compare_games(["prefix-miss"])
    with database.connection() as db:
        event = db.execute("SELECT * FROM repertoire_decision_events WHERE game_id='prefix-miss'").fetchone()
        assert event is not None and event["outcome"] == "miss" and event["card_id"] == "card"


START = chess.STARTING_FEN
EXPECTED = ["e2e4", "e7e5", "g1f3"]


def _seed_repertoire(db, *, with_card=True, studied_at=None):
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES('rep','Main','main.pgn',?,1)",
        (now,),
    )
    db.execute(
        """INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
           VALUES('line','rep','Main','white',?,?,?)""",
        (START, json.dumps(EXPECTED), now),
    )
    db.execute(
        "INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at) VALUES('rep','clean',?)",
        (now,),
    )
    if not with_card:
        return
    db.execute(
        """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color,introduced_at)
           VALUES('card','rep','prefix',?,?,?,?, 'opening','white',?)""",
        (
            START,
            json.dumps(EXPECTED),
            "mature" if studied_at else "new",
            (date.today() + timedelta(days=20)).isoformat(),
            studied_at[:10] if studied_at else None,
        ),
    )
    db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep','card')")
    if studied_at:
        db.execute(
            """INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind)
               VALUES('card','correct',?,0,7,'study')""",
            (studied_at,),
        )


def _seed_game(db, game_id, moves, played_at, *, color="white"):
    db.execute(
        """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
           VALUES(?,'lichess','TempoPlayer',?,'rapid',1,?,'1-0',?,?)""",
        (game_id, played_at, color, START, json.dumps(moves)),
    )


def test_real_game_miss_maps_to_existing_card_once_and_reaches_introduction_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        _seed_repertoire(db)
        _seed_game(db, "miss", ["d2d4"], now)
        db.execute("UPDATE settings SET new_cards_per_day=1 WHERE id=1")
    for _ in range(2):
        compare_games(["miss"])
        refresh_game_findings("miss")
        apply_real_game_misses("miss")
    with database.connection() as db:
        events = db.execute("SELECT * FROM repertoire_decision_events WHERE game_id='miss'").fetchall()
        finding = db.execute("SELECT * FROM game_findings WHERE game_id='miss' AND kind='repertoire lapse'").fetchone()
        assert len(events) == 1
        assert events[0]["card_id"] == finding["card_id"] == "card"
        assert events[0]["outcome"] == "miss"
        assert events[0]["fen_key"] == " ".join(START.split()[:4])
        assert events[0]["expected_uci"] == "e2e4"
        assert events[0]["actual_uci"] == "d2d4"
        rebuild_introduction_priorities(db, "rep")
        priority = db.execute("SELECT * FROM repertoire_card_introduction_priorities WHERE card_id='card'").fetchone()
        evidence = json.loads(priority["evidence_json"])["real_game_repertoire_miss"]
        assert evidence["weight"] == 1.0 and evidence["game_id"] == "miss"
        assert priority["priority_score"] >= 1.0
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        seed_queue(db, tomorrow)
        queued = db.execute("SELECT * FROM daily_queue WHERE queue_date=? AND card_id='card'", (tomorrow,)).fetchone()
        assert queued["gameplay_priority_reason"] == "Priority review · missed in a recent game"
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='card'").fetchone()[0] == 0


def test_opponent_deviation_and_line_end_create_no_player_miss_event(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        _seed_repertoire(db)
        _seed_game(db, "opponent", ["e2e4", "c7c5"], now)
        _seed_game(db, "line-end", EXPECTED + ["b8c6", "f1c4"], now)
    compare_games(["opponent", "line-end"])
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM repertoire_decision_events WHERE outcome='miss'").fetchone()[0] == 0
        assert db.execute("SELECT outcome FROM repertoire_decision_events WHERE game_id='opponent'").fetchone()[0] == "success"


def test_missing_card_keeps_finding_without_priority_or_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        _seed_repertoire(db, with_card=False)
        _seed_game(db, "gap", ["d2d4"], now)
    compare_games(["gap"])
    refresh_game_findings("gap")
    apply_real_game_misses("gap")
    with database.connection() as db:
        assert db.execute("SELECT card_id FROM repertoire_decision_events WHERE game_id='gap'").fetchone()[0] is None
        assert db.execute("SELECT card_id FROM game_findings WHERE game_id='gap' AND kind='repertoire lapse'").fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_studied_game_miss_prioritizes_without_changing_fsrs_or_creating_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=(now - timedelta(days=2)).isoformat())
        db.execute("UPDATE cards SET interval_days=20,stability=13.5,fsrs_card_json=? WHERE id='card'", (Card().to_json(),))
        _seed_game(db, "miss", ["d2d4"], (now - timedelta(days=1)).isoformat())
        before = dict(db.execute("SELECT * FROM cards WHERE id='card'").fetchone())
    compare_games(["miss"])
    apply_real_game_misses("miss")
    with database.connection() as db:
        after = dict(db.execute("SELECT * FROM cards WHERE id='card'").fetchone())
        assert after == before
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='card'").fetchone()[0] == 1
        assert db.execute("SELECT outcome FROM repertoire_decision_events WHERE game_id='miss'").fetchone()[0] == "miss"
        queued = db.execute("SELECT * FROM daily_queue WHERE card_id='card' AND status='queued'").fetchall()
        assert len(queued) == 1
        assert queued[0]["gameplay_priority_reason"] == "Priority review · missed in a recent game"


def test_priority_game_miss_queue_reports_prior_study_without_a_clean_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=(now - timedelta(days=2)).isoformat())
        db.execute("UPDATE reviews SET rating='again' WHERE card_id='card'")
        _seed_game(db, "miss", ["d2d4"], (now - timedelta(days=1)).isoformat())
    compare_games(["miss"])
    apply_real_game_misses("miss")
    queued = queue_today()["cards"][0]
    assert queued["gameplay_priority_reason"] == "Priority review · missed in a recent game"
    assert queued["first_correct_at"] is None
    assert queued["has_study_review"] == 1


def test_targeted_study_updates_fsrs_and_later_success_is_measurable(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=(now - timedelta(days=2)).isoformat())
        _seed_game(db, "first", ["d2d4"], (now - timedelta(days=1)).isoformat())
    for _ in range(2):
        compare_games(["first"])
        apply_real_game_misses("first")
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='card' AND source_kind='game'").fetchone()[0] == 0
        queued = db.execute("SELECT id,gameplay_priority_reason FROM daily_queue WHERE card_id='card' AND status='queued'").fetchone()
        assert queued["gameplay_priority_reason"] == "Priority review · missed in a recent game"
        before = dict(db.execute("SELECT * FROM cards WHERE id='card'").fetchone())
    assert queue_today()["cards"][0]["gameplay_priority_reason"] == "Priority review · missed in a recent game"
    reviewed = TestClient(app).post("/api/cards/card/review", json={"outcome": "correct", "queue_entry_id": queued["id"]})
    assert reviewed.status_code == 200
    with database.connection() as db:
        after = dict(db.execute("SELECT * FROM cards WHERE id='card'").fetchone())
        assert after["fsrs_card_json"] != before["fsrs_card_json"]
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='card' AND source_kind='study'").fetchone()[0] == 2
        rebuild_introduction_priorities(db, "rep")
        evidence = json.loads(db.execute("SELECT evidence_json FROM repertoire_card_introduction_priorities WHERE card_id='card'").fetchone()[0])
        assert evidence["real_game_repertoire_miss"] is None
        _seed_game(db, "second", EXPECTED, (now + timedelta(days=1)).isoformat())
    apply_real_game_misses("first")
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card' AND status='queued' AND gameplay_priority_reason=?", ("Priority review · missed in a recent game",)).fetchone()[0] == 0
    compare_games(["second"])
    with database.connection() as db:
        later = db.execute("SELECT * FROM repertoire_decision_events WHERE game_id='second' AND fen_key=?", (" ".join(START.split()[:4]),)).fetchone()
        assert later["outcome"] == "success" and later["card_id"] == "card"
        assert later["expected_uci"] == later["actual_uci"] == "e2e4"
        sequence = db.execute(
            """SELECT
                   (SELECT played_at FROM repertoire_decision_events WHERE game_id='first' AND ply=0) AS miss_at,
                   (SELECT MAX(reviewed_at) FROM reviews WHERE card_id='card' AND source_kind='study') AS study_at,
                   (SELECT played_at FROM repertoire_decision_events WHERE game_id='second' AND ply=0) AS success_at"""
        ).fetchone()
        assert sequence["miss_at"] < sequence["study_at"] < sequence["success_at"]


def test_late_imported_miss_is_historical_and_does_not_change_fsrs(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=now.isoformat())
        _seed_game(db, "old", ["d2d4"], (now - timedelta(days=1)).isoformat())
    compare_games(["old"])
    apply_real_game_misses("old")
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM repertoire_decision_events WHERE outcome='miss'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE source_kind='game'").fetchone()[0] == 0
        rebuild_introduction_priorities(db, "rep")
        evidence = json.loads(db.execute("SELECT evidence_json FROM repertoire_card_introduction_priorities WHERE card_id='card'").fetchone()[0])
        assert evidence["real_game_repertoire_miss"] is None


def test_blocked_existing_card_keeps_game_evidence_without_automatic_again(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=(now - timedelta(days=2)).isoformat())
        db.execute("UPDATE cards SET pending_validation=1 WHERE id='card'")
        _seed_game(db, "blocked", ["d2d4"], (now - timedelta(days=1)).isoformat())
    compare_games(["blocked"])
    apply_real_game_misses("blocked")
    with database.connection() as db:
        assert db.execute("SELECT outcome FROM repertoire_decision_events WHERE game_id='blocked'").fetchone()[0] == "miss"
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE source_kind='game'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card'").fetchone()[0] == 0


def test_transposed_decision_keeps_canonical_card_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    intended = ["g1f3", "d7d5", "g2g3", "g8f6", "f1g2"]
    transposed = ["g2g3", "d7d5", "g1f3", "g8f6", "f1g2"]
    with database.connection() as db:
        _seed_repertoire(db)
        db.execute("UPDATE repertoire_lines SET moves_json=? WHERE id='line'", (json.dumps(intended),))
        db.execute("UPDATE cards SET moves_json=? WHERE id='card'", (json.dumps(intended),))
        _seed_game(db, "transposed", transposed, now)
    compare_games(["transposed"])
    board = chess.Board()
    for move in transposed[:4]:
        board.push_uci(move)
    with database.connection() as db:
        success = db.execute(
            "SELECT * FROM repertoire_decision_events WHERE game_id='transposed' AND ply=4"
        ).fetchone()
        assert success["fen_key"] == " ".join(board.fen().split()[:4])
        assert success["expected_uci"] == success["actual_uci"] == "f1g2"
        assert success["card_id"] == "card"


def test_study_review_consumes_real_game_miss_bonus(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    missed_at = datetime.now(timezone.utc) - timedelta(days=1)
    with database.connection() as db:
        _seed_repertoire(db)
        _seed_game(db, "miss", ["d2d4"], missed_at.isoformat())
    compare_games(["miss"])
    with database.connection() as db:
        rebuild_introduction_priorities(db, "rep")
        before = db.execute("SELECT priority_score FROM repertoire_card_introduction_priorities WHERE card_id='card'").fetchone()[0]
        db.execute(
            """INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind)
               VALUES('card','correct',?,0,7,'study')""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        rebuild_introduction_priorities(db, "rep")
        after = db.execute("SELECT priority_score,evidence_json FROM repertoire_card_introduction_priorities WHERE card_id='card'").fetchone()
        assert before - after["priority_score"] == 1.0
        assert json.loads(after["evidence_json"])["real_game_repertoire_miss"] is None


def test_same_second_study_timestamps_consume_only_earlier_game_misses(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    second = datetime.now(timezone.utc).replace(microsecond=0)
    earlier_study = second + timedelta(milliseconds=100)
    missed_at = second + timedelta(milliseconds=500)
    later_study = second + timedelta(milliseconds=900)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=earlier_study.isoformat())
        _seed_game(db, "same-second", ["d2d4"], missed_at.isoformat())
    compare_games(["same-second"])
    apply_real_game_misses("same-second")
    with database.connection() as db:
        rebuild_introduction_priorities(db, "rep")
        before = json.loads(db.execute("SELECT evidence_json FROM repertoire_card_introduction_priorities WHERE card_id='card'").fetchone()[0])
        assert before["real_game_repertoire_miss"]["game_id"] == "same-second"
        db.execute(
            "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) "
            "VALUES('card','correct',?,7,14,'study')", (later_study.isoformat(),),
        )
        rebuild_introduction_priorities(db, "rep")
        after = json.loads(db.execute("SELECT evidence_json FROM repertoire_card_introduction_priorities WHERE card_id='card'").fetchone()[0])
        assert after["real_game_repertoire_miss"] is None


def test_foreground_review_completes_while_real_game_derivation_computes(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    entered = threading.Event()
    release = threading.Event()
    original_compare = repertoire_comparison._compare_game_to_repertoire

    def paused_compare(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return original_compare(*args, **kwargs)

    monkeypatch.setattr(repertoire_comparison, "_compare_game_to_repertoire", paused_compare)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_repertoire(db, studied_at=(now - timedelta(days=2)).isoformat())
            _seed_game(db, "foreground", ["d2d4"], (now - timedelta(days=1)).isoformat())
            entry_id = db.execute(
                "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'card',0)",
                (date.today().isoformat(),),
            ).lastrowid
        worker = threading.Thread(target=_execute_derivation, args=("foreground",))
        worker.start()
        try:
            assert entered.wait(timeout=2)
            response = client.post(
                "/api/cards/card/review",
                json={"outcome": "correct", "queue_entry_id": entry_id},
            )
            assert response.status_code == 200
        finally:
            release.set()
            worker.join(timeout=4)
        assert not worker.is_alive()
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='card' AND source_kind='study'").fetchone()[0] == 2


def test_restart_does_not_relink_archived_opening_card_removed_by_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(db)
        db.execute("UPDATE cards SET archived=1 WHERE id='card'")
        db.execute("DELETE FROM repertoire_cards WHERE card_id='card'")
    database.initialize()
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM repertoire_cards WHERE card_id='card'").fetchone()[0] == 0


def test_reinforcement_order_survives_daily_queue_projection_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    today = date.today().isoformat()
    with database.connection() as db:
        _seed_repertoire(db)
        for card_id in ("card", "other"):
            if card_id == "other":
                db.execute(
                    """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date)
                       VALUES('other','rep','prefix',?,'["d2d4"]',?)""",
                    (START, today),
                )
            db.execute(
                "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",
                (today, card_id, 0 if card_id == "card" else 1),
            )
        randomize_daily_queue(db, today)
        db.execute("UPDATE daily_queue SET status='complete' WHERE card_id='card'")
        requeue(db, today, "card", 4, "reinforcement")
        before = [tuple(row) for row in db.execute(
            "SELECT id,card_id,position,card_bucket,admission_kind FROM daily_queue WHERE status='queued' ORDER BY position,id"
        )]
        randomize_daily_queue(db, today)
        after = [tuple(row) for row in db.execute(
            "SELECT id,card_id,position,card_bucket,admission_kind FROM daily_queue WHERE status='queued' ORDER BY position,id"
        )]
        assert before == after
        assert after[-1][1:] == ("card", after[-1][2], "opening", "review")


@pytest.mark.parametrize('unsafe_change', [
    "UPDATE imported_games SET adaptive_excluded=1 WHERE id='unsafe-miss'",
    "UPDATE cards SET archived=1 WHERE id='card'",
    "UPDATE cards SET pending_validation=1 WHERE id='card'",
    "UPDATE cards SET superseded_by='replacement' WHERE id='card'",
    "INSERT INTO deleted_cards(card_id,deleted_at) VALUES('card','2026-01-01')",
    "INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id,scan_generation,published_at) VALUES('rep','card','unsafe-issue','scan','2026-01-01')",
])
def test_real_game_miss_retains_unsafe_or_excluded_evidence_without_admission(tmp_path, monkeypatch, unsafe_change):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(db)
        _seed_game(db, 'unsafe-miss', ['d2d4'], datetime.now(timezone.utc).isoformat())
    compare_games(['unsafe-miss'])
    with database.connection() as db:
        if 'repertoire_integrity_card_blocks' in unsafe_change:
            db.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,created_at,updated_at) VALUES('unsafe-issue','rep','invalid_source','broken','2026-01-01','2026-01-01')")
        db.execute(unsafe_change)
    apply_real_game_misses('unsafe-miss')
    with database.connection() as db:
        seed_queue(db, date.today().isoformat())
        assert db.execute("SELECT COUNT(*) FROM repertoire_decision_events WHERE outcome='miss'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


@pytest.mark.parametrize('source_kind,invalidated,expected', [('study', False, False), ('game', False, True), ('study', True, True)])
def test_real_game_obligation_uses_valid_study_and_equivalent_timezone_instants(tmp_path, monkeypatch, source_kind, invalidated, expected):
    from app.services.real_game_feedback import has_outstanding_real_game_miss
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(db, studied_at='2026-10-01T08:00:00.123456-04:00')
        db.execute('UPDATE reviews SET source_kind=?,invalidated_at=?', (source_kind, '2026-10-02' if invalidated else None))
        _seed_game(db, 'equivalent-miss', ['d2d4'], '2026-10-01T12:00:00.123456Z')
    compare_games(['equivalent-miss'])
    with database.connection() as db:
        assert has_outstanding_real_game_miss(db, 'card') is expected


def test_real_game_misses_deduplicate_by_card_and_study_consumes_all(tmp_path, monkeypatch):
    from app.services.real_game_feedback import has_outstanding_real_game_miss
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    now = datetime.now(timezone.utc)
    with database.connection() as db:
        _seed_repertoire(db, studied_at=(now - timedelta(days=4)).isoformat())
        _seed_game(db, 'older-miss', ['d2d4'], (now - timedelta(days=2)).isoformat())
        _seed_game(db, 'recent-miss', ['d2d4'], (now - timedelta(days=1)).isoformat())
    compare_games(['older-miss', 'recent-miss'])
    for game_id in ['recent-miss', 'older-miss', 'recent-miss']:
        apply_real_game_misses(game_id)
    with database.connection() as db:
        entry_id = db.execute("SELECT id FROM daily_queue WHERE card_id='card'").fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card'").fetchone()[0] == 1
    with TestClient(app) as client:
        response = client.post('/api/cards/card/review', json={
            'outcome': 'correct', 'queue_entry_id': entry_id, 'expected_revision': 1,
            'attempt_id': 'remediate-all-misses', 'recorded_at': now.isoformat(),
        })
        assert response.status_code == 200, response.text
    with database.connection() as db:
        assert not has_outstanding_real_game_miss(db, 'card')
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE gameplay_priority_reason IS NOT NULL").fetchone()[0] == 0
        _seed_game(db, 'newer-miss', ['d2d4'], (now + timedelta(microseconds=1)).isoformat())
    compare_games(['newer-miss'])
    apply_real_game_misses('newer-miss')
    with database.connection() as db:
        assert has_outstanding_real_game_miss(db, 'card')
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card' AND status='queued'").fetchone()[0] == 1


def test_real_game_priority_retains_identified_active_attempt_and_burial_defers_obligation(tmp_path, monkeypatch):
    from app.services.real_game_feedback import has_outstanding_real_game_miss
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(db, studied_at='2026-01-01T00:00:00Z')
        active_id = _ordinary_queue_card(db, 'active', 0, content_type='tactic')
        _seed_game(db, 'active-miss', ['d2d4'], datetime.now(timezone.utc).isoformat())
    compare_games(['active-miss'])
    apply_real_game_misses('active-miss')
    with TestClient(app) as client:
        assert client.post(f'/api/queue/entries/{active_id}/fail').status_code == 409
        identity = {'card_id': 'active', 'expected_revision': 1}
        assert client.post(f'/api/queue/entries/{active_id}/fail', json=identity).status_code == 200
        assert client.post(f'/api/queue/entries/{active_id}/bury', json={**identity, 'expected_revision': 2}).status_code == 409
        assert client.post(f'/api/queue/entries/{active_id}/bury', json=identity).status_code == 200
        with database.connection() as db:
            miss_id = db.execute("SELECT id FROM daily_queue WHERE card_id='card'").fetchone()[0]
        assert client.post(f'/api/queue/entries/{miss_id}/bury', json={'card_id': 'card', 'expected_revision': 1}).status_code == 200
    apply_real_game_misses('active-miss')
    with database.connection() as db:
        assert has_outstanding_real_game_miss(db, 'card')
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card' AND status='queued'").fetchone()[0] == 0
        seed_queue(db, (date.today() + timedelta(days=1)).isoformat())
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card' AND status='queued'").fetchone()[0] == 1


def test_canonical_decisions_recognize_deep_reentry_and_accepted_alternatives(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    intended = ['g1f3', 'g8f6', 'g2g3']
    # The wrong knight route leaves known positions, then legally returns to one.
    actual = ['b1c3', 'g8f6', 'c3b1', 'f6g8'] + ['g1f3', 'g8f6', 'f3g1', 'f6g8'] * 10 + ['d2d4']
    with database.connection() as db:
        _seed_repertoire(db)
        db.execute('UPDATE repertoire_lines SET moves_json=?', (json.dumps(intended),))
        db.execute('UPDATE cards SET moves_json=?', (json.dumps(intended),))
        _seed_game(db, 'deep-reentry', actual, datetime.now(timezone.utc).isoformat())
    compare_games(['deep-reentry'])
    with database.connection() as db:
        events = db.execute("SELECT ply,outcome,card_id FROM repertoire_decision_events WHERE game_id='deep-reentry' ORDER BY ply").fetchall()
        assert tuple(events[0]) == (0, 'miss', 'card')
        assert tuple(events[-1]) == (44, 'miss', 'card')
        assert not any(event['ply'] == 2 for event in events)  # unknown position
        assert any(event['ply'] == 4 and event['outcome'] == 'success' for event in events)
        db.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,start_fen,moves_json,trained_color,created_at) VALUES('alternative','rep','Alternative',?,'[\"d2d4\"]','white','2026-01-01')", (START,))
        _seed_game(db, 'accepted-alternative', ['d2d4'], datetime.now(timezone.utc).isoformat())
    compare_games(['accepted-alternative'])
    with database.connection() as db:
        assert db.execute("SELECT outcome FROM repertoire_decision_events WHERE game_id='accepted-alternative'").fetchone()[0] == 'success'
