from datetime import date

from fastapi.testclient import TestClient

from app import database
from app.main import app, materialize_daily_queue
from app.services.database_executor import submit_foreground_write


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_database_initialize_migrates_study_tables_for_direct_queue_users(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.read_connection() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='study_exercises'").fetchone()
        assert "study_exercise_id" in {row["name"] for row in db.execute("PRAGMA table_info(cards)")}


def _seed_cards() -> None:
    today = date.today().isoformat()
    with database.connection() as db:
        db.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('queue-test','Queue','test',?)",
            (today,),
        )
        for index, content_type in enumerate(
            ["opening", "tactic", "endgame", "middlegame"] * 2
        ):
            card_id = f"review-{index}"
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,introduced_at)
                   VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,?,?)""",
                (card_id, "queue-test", START_FEN, today, content_type, today),
            )
            db.execute(
                """INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval)
                   VALUES(?,'correct',?,0,1)""",
                (card_id, f"{today}T00:00:00+00:00"),
            )
        for index, content_type in enumerate(
            ["opening", "tactic", "endgame", "middlegame"] * 2
        ):
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,introduced_at)
                   VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,?,?)""",
                (f"new-{index}", "queue-test", START_FEN, today, content_type, today),
            )
    submit_foreground_write(
        lambda connection: materialize_daily_queue(connection, today),
        label="test-daily-queue",
    )


def test_daily_queue_is_stable_within_a_day_and_mixed(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_cards()
        first = client.get("/api/queue/today").json()["cards"]
        second = client.get("/api/queue/today").json()["cards"]
        assert [card["id"] for card in first] == [card["id"] for card in second]
        cohorts = ["review" if card["id"].startswith("review-") else "new" for card in first]
        assert all(left != right for left, right in zip(cohorts, cohorts[1:]))
        assert len({card["content_type"] for card in first[:6]}) >= 3
        assert len({card["position"] for card in first}) == len(first)


def test_training_queue_window_limits_cards_and_preserves_order(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_cards()
        complete = client.get("/api/queue/today").json()
        window_response = client.get("/api/queue/window?limit=3")
        assert window_response.status_code == 200
        window = window_response.json()
        assert [card["queue_entry_id"] for card in window["cards"]] == [
            card["queue_entry_id"] for card in complete["cards"][:3]
        ]
        assert window["count"] == complete["count"]
        assert len(window["cards"]) == 3


def test_study_queue_window_count_matches_published_prepared_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    today = date.today().isoformat()
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('regular','Regular','regular.pgn',?)", (today,))
            db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES('regular-card','regular','prefix',?,'[\"e2e4\"]','learning',?,'opening')", (START_FEN, today))
            db.execute("INSERT INTO studies(id,title,created_at,updated_at) VALUES('study-1','Study',?,?)", (today, today))
            db.execute("INSERT INTO study_chapters(id,study_id,title,position) VALUES('chapter-1','study-1','Chapter',0)")
            db.execute("""INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,filename,record_index,headers_json,diagnostics_json,valid,created_at)
                          VALUES('source-1','chapter-1','group-1',1,'','hash','study.pgn',0,'{}','[]',1,?)""", (today,))
            db.execute("INSERT INTO study_positions(id,source_id,child_index,fen,history_json,node_path) VALUES('position-1','source-1',0,?,'[]','0')", (START_FEN,))
            db.execute("INSERT INTO study_exercises(id,study_id,position_id,status,created_at,updated_at) VALUES('exercise-1','study-1','position-1','published',?,?)", (today, today))
            db.execute("""INSERT INTO study_exercise_revisions(exercise_id,revision,specification_json,specification_hash,created_at)
                          VALUES('exercise-1',1,'{"type":"choice","prompt":"Choose","hint":"","explanation":"","options":[{"id":"a","text":"A"}],"correct_option_ids":["a"]}','hash',?)""", (today,))
            db.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,study_exercise_id)
                          VALUES('study-card',NULL,'exercise',?,'[]','learning',?,'study_exercise','exercise-1')""", (START_FEN, today))
            db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'regular-card',0)", (today,))
            db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'study-card',1)", (today,))
        window_response = client.get("/api/queue/window?limit=1")
        prepared_response = client.get("/api/queue/prepared")
        assert window_response.status_code == 200, window_response.text
        assert prepared_response.status_code == 200, prepared_response.text
        window = window_response.json()
        prepared = prepared_response.json()
        assert window["count"] == prepared["count"] == 2
        assert [card["id"] for card in prepared["cards"]] == ["regular-card", "study-card"]
        assert prepared["cards"][1]["study_id"] == "study-1"
        assert prepared["cards"][1]["study_exercise_id"] == "exercise-1"
        assert prepared["cards"][1]["repertoire_id"] is None
        assert prepared["cards"][1]["study_snapshot"]["exercise_id"] == "exercise-1"


def test_daily_queue_uses_a_different_seed_for_the_next_day(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_cards()
        client.get("/api/queue/today")
        with database.connection() as db:
            from app.main import randomize_daily_queue

            today = date.today().isoformat()
            randomize_daily_queue(db, today)
            today_order = [
                row[0]
                for row in db.execute(
                    "SELECT card_id FROM daily_queue WHERE queue_date=? ORDER BY position",
                    (today,),
                )
            ]
            tomorrow = "2099-01-02"
            db.execute(
                """INSERT INTO daily_queue(queue_date,card_id,position)
                   SELECT ?,card_id,position FROM daily_queue WHERE queue_date=?""",
                (tomorrow, today),
            )
            randomize_daily_queue(db, tomorrow)
            tomorrow_order = [
                row[0]
                for row in db.execute(
                    "SELECT card_id FROM daily_queue WHERE queue_date=? ORDER BY position",
                    (tomorrow,),
                )
            ]
        assert today_order != tomorrow_order


def test_bury_excludes_card_until_next_day_without_review_or_schedule_change(tmp_path, monkeypatch):
    from datetime import timedelta
    from app import main

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    today = date.today()
    with TestClient(app) as client:
        _seed_cards()
        before = client.get("/api/queue/today").json()
        buried = before["cards"][0]
        assert client.post(f"/api/queue/entries/{before['cards'][1]['queue_entry_id']}/bury").status_code == 409
        assert client.post("/api/queue/entries/999999/bury").status_code == 409
        with database.read_connection() as db:
            original_card = dict(db.execute("SELECT * FROM cards WHERE id=?", (buried["id"],)).fetchone())
            original_reviews = [dict(row) for row in db.execute("SELECT * FROM reviews ORDER BY id")]
        result = client.post(f"/api/queue/entries/{buried['queue_entry_id']}/bury")
        assert result.status_code == 200, result.text
        assert result.json() == {"buried": True, "queue_entry_id": buried["queue_entry_id"]}
        after = client.get("/api/queue/today").json()
        assert after["count"] == before["count"] - 1
        assert [card["queue_entry_id"] for card in after["cards"]] == [
            card["queue_entry_id"] for card in before["cards"][1:]
        ]
        assert client.post(f"/api/queue/entries/{buried['queue_entry_id']}/bury").status_code == 409
        submit_foreground_write(lambda db: materialize_daily_queue(db, today.isoformat()), label="test-refresh-buried")
        refreshed = client.get("/api/queue/today").json()["cards"]
        assert [card["queue_entry_id"] for card in refreshed] == [card["queue_entry_id"] for card in after["cards"]]
        with database.read_connection() as db:
            assert dict(db.execute("SELECT * FROM cards WHERE id=?", (buried["id"],)).fetchone()) == original_card
            assert [dict(row) for row in db.execute("SELECT * FROM reviews ORDER BY id")] == original_reviews
            assert db.execute("SELECT status FROM daily_queue WHERE id=?", (buried["queue_entry_id"],)).fetchone()[0] == "buried"
    # Reopen the application against the same database to prove persistence.
    with TestClient(app) as client:
        assert buried["id"] not in {card["id"] for card in client.get("/api/queue/today").json()["cards"]}
        tomorrow = today + timedelta(days=1)

        class Tomorrow(date):
            @classmethod
            def today(cls):
                return tomorrow

        monkeypatch.setattr(main, "date", Tomorrow)
        from app import queue_commands
        monkeypatch.setattr(queue_commands, "date", Tomorrow)
        submit_foreground_write(lambda db: materialize_daily_queue(db, tomorrow.isoformat()), label="test-next-day-buried")
        assert buried["id"] in {card["id"] for card in client.get("/api/queue/today").json()["cards"]}


def test_bury_removes_all_queued_cycles_and_can_finish_the_daily_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_cards()
        before = client.get("/api/queue/today").json()["cards"]
        buried = before[0]
        with database.connection() as db:
            db.execute("DELETE FROM daily_queue WHERE card_id!=?", (buried["id"],))
            db.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(?,?,1,100)",
                       (date.today().isoformat(), buried["id"]))
            db.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,status) VALUES(?,?,2,101,'complete')",
                       (date.today().isoformat(), buried["id"]))
        response = client.post(f"/api/queue/entries/{buried['queue_entry_id']}/bury")
        assert response.status_code == 200, response.text
        queue = client.get("/api/queue/today").json()
        assert queue["count"] == 0
        assert queue["cards"] == []
        with database.read_connection() as db:
            assert [row[0] for row in db.execute("SELECT status FROM daily_queue WHERE card_id=?", (buried["id"],))] == ["buried", "buried", "complete"]


def test_defensive_stack_toggle_hides_today_without_erasing_reviews_or_queue_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_cards()
        today = date.today().isoformat()
        with database.connection() as db:
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type)
                   VALUES('defense-toggle','queue-test','checkpoint',?,'[]','learning',?,'defense')""",
                (START_FEN, today),
            )
            db.execute(
                """INSERT INTO daily_queue(queue_date,card_id,position)
                   VALUES(?,'defense-toggle',-1)""",
                (today,),
            )
            db.execute(
                """INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval)
                   VALUES('defense-toggle','correct',?,0,1)""",
                (f"{today}T00:00:00+00:00",),
            )
            entry_id = db.execute(
                "SELECT id FROM daily_queue WHERE card_id='defense-toggle'"
            ).fetchone()[0]
        before = client.get("/api/queue/today").json()
        assert entry_id in {card["queue_entry_id"] for card in before["cards"]}
        settings = client.get("/api/settings").json()
        assert settings["include_defensive_cards_in_daily_stack"] is True
        response = client.put("/api/settings", json={
            **settings, "include_defensive_cards_in_daily_stack": False,
        })
        assert response.status_code == 200
        hidden = client.get("/api/queue/today").json()
        assert entry_id not in {card["queue_entry_id"] for card in hidden["cards"]}
        assert hidden["count"] == before["count"] - 1
        assert client.get("/api/progress").json()["dueToday"] == hidden["count"]
        assert client.post(f"/api/queue/entries/{entry_id}/fail").status_code == 409
        with database.connection() as db:
            assert db.execute("SELECT status FROM daily_queue WHERE id=?", (entry_id,)).fetchone()[0] == "queued"
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='defense-toggle'").fetchone()[0] == 1
        response = client.put("/api/settings", json={
            **settings, "include_defensive_cards_in_daily_stack": True,
        })
        assert response.status_code == 200
        restored = client.get("/api/queue/today").json()
        assert entry_id in {card["queue_entry_id"] for card in restored["cards"]}


def test_postgres_bury_handler_excludes_all_cycles_without_reordering_and_rejects_stale_entry(tmp_path, monkeypatch):
    from app import queue_commands
    from fastapi import HTTPException
    import pytest

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    lock_requests = []

    class CommandDatabase:
        # SQL compatibility proof only; real PostgreSQL locks/receipts are
        # covered by the disposable durability runner.
        def __init__(self, db):
            self.db = db

        def execute(self, statement, parameters=()):
            return self.db.execute(statement.replace(" FOR UPDATE OF q", ""), parameters)

        def execute_native(self, statement, parameters=()):
            assert "pg_advisory_xact_lock" in statement
            lock_requests.append((statement, parameters))

    with TestClient(app) as client:
        _seed_cards()
        before = client.get("/api/queue/today").json()["cards"]
        buried = before[0]
        with database.connection() as db:
            original_positions = [(row["id"], row["position"]) for row in db.execute(
                "SELECT id,position FROM daily_queue ORDER BY id")]
            db.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(?,?,1,100)",
                       (date.today().isoformat(), buried["id"]))
            assert queue_commands.bury_queue_entry(CommandDatabase(db), {"entry_id": buried["queue_entry_id"]}) == {
                "buried": True, "queue_entry_id": buried["queue_entry_id"],
            }
            assert [(row["id"], row["position"]) for row in db.execute(
                "SELECT id,position FROM daily_queue WHERE cycle=0 ORDER BY id")] == original_positions
            assert [row[0] for row in db.execute("SELECT status FROM daily_queue WHERE card_id=?", (buried["id"],))] == ["buried", "buried"]
            with pytest.raises(HTTPException) as stale:
                queue_commands.bury_queue_entry(CommandDatabase(db), {"entry_id": buried["queue_entry_id"]})
            assert stale.value.status_code == 409
        assert [card["id"] for card in client.get("/api/queue/today").json()["cards"]] == [card["id"] for card in before[1:]]
        assert lock_requests == [
            ("SELECT pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))", ()),
            ("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"tempo:daily-queue-position:{date.today().isoformat()}",)),
        ] * 2


def test_buried_new_study_card_consumes_daily_quota_without_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    today = date.today().isoformat()
    with TestClient(app) as client:
        _seed_cards()
        with database.connection() as db:
            db.execute("UPDATE settings SET study_new_per_day=1 WHERE id=1")
            db.execute("INSERT INTO studies(id,title,created_at,updated_at) VALUES('burial-study','Burial quota',?,?)", (today, today))
            db.execute("INSERT INTO study_chapters(id,study_id,title,position) VALUES('burial-chapter','burial-study','Chapter',0)")
            db.execute("""INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,filename,record_index,headers_json,diagnostics_json,valid,created_at)
                          VALUES('burial-source','burial-chapter','burial-group',1,'','hash','burial.pgn',0,'{}','[]',1,?)""", (today,))
            db.execute("INSERT INTO study_positions(id,source_id,child_index,fen,history_json,node_path) VALUES('burial-position','burial-source',0,?,'[]','0')", (START_FEN,))
            for exercise_id in ("quota-a", "quota-b"):
                db.execute("INSERT INTO study_exercises(id,study_id,position_id,status,created_at,updated_at) VALUES(?,'burial-study','burial-position','published',?,?)", (exercise_id, today, today))
                db.execute("""INSERT INTO study_exercise_revisions(exercise_id,revision,specification_json,specification_hash,created_at)
                              VALUES(?,1,'{"type":"choice","prompt":"Choose","hint":"","explanation":"","options":[{"id":"a","text":"A"}],"correct_option_ids":["a"]}','hash',?)""", (exercise_id, today))
                db.execute("""INSERT INTO cards(id,kind,start_fen,moves_json,state,due_date,content_type,study_exercise_id)
                              VALUES(?,'exercise',?,'[]','new',?,'study_exercise',?)""", (exercise_id, START_FEN, today, exercise_id))
        submit_foreground_write(lambda db: materialize_daily_queue(db, today), label="test-study-bury-quota")
        initial_queue = client.get("/api/queue/today").json()["cards"]
        admitted_studies = [card for card in initial_queue if card["content_type"] == "study_exercise"]
        assert len(admitted_studies) == 1
        selected_card = admitted_studies[0]
        with database.connection() as db:
            db.execute("UPDATE daily_queue SET position=-1 WHERE id=?", (selected_card["queue_entry_id"],))
        before_burial = client.get("/api/queue/today").json()["cards"]
        assert client.post(f"/api/queue/entries/{selected_card['queue_entry_id']}/bury").status_code == 200
        submit_foreground_write(lambda db: materialize_daily_queue(db, today), label="test-study-bury-quota-refresh")
        refreshed_queue = client.get("/api/queue/today").json()["cards"]
        assert not any(card["content_type"] == "study_exercise" for card in refreshed_queue)
        assert [card["queue_entry_id"] for card in refreshed_queue] == [card["queue_entry_id"] for card in before_burial if card["id"] != selected_card["id"]]
        with database.read_connection() as db:
            consumed_admissions = db.execute("""SELECT card_id,status FROM daily_queue
                WHERE queue_date=? AND card_id IN ('quota-a','quota-b')""", (today,)).fetchall()
            assert [(row["card_id"], row["status"]) for row in consumed_admissions] == [(selected_card["id"], "buried")]
